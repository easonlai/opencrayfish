"""core.brain — Prompt Assembly Pipeline (per PROMPT_ASSEMBLY.md).

The Brain orchestrates a single end-to-end thought:

    1. Soul Context           ←  soul_handler.render_identity_block()
    2. Physical State         ←  monitor.sample().describe()
    3. Internal Mood          ←  emotions.snapshot().describe()
    4. User Empathy           ←  empathy.analyze(user_input)
    4b. Identity short-circuit—  regex on user_input; if it matches a basic
                                 "who are you / what's my name / how are you"
                                 question, return a deterministic templated
                                 reply built from soul.md + config + vitals.
                                 Zero SLM round-trip, zero hallucination.
    5. Knowledge Retrieval    ←  archive.md (retrieve_relevant) + (when
                                 cognition is engaged) the CognitiveLoop's
                                 rendered `knowledge_block`. Falls back to
                                 the legacy intent-detected SearXNG triage
                                 path when cognition is bypassed (chitchat /
                                 stressed / LTM short-circuit / disabled).
    6. Task Execution         ←  current user message / heartbeat mission
    7. Synth                  ←  provider.generate(system_prompt, history)
    8. Prompt-leak guard      ←  drop responses that regurgitate scaffolding
    9. Positive Anchor        ←  PositiveFilter.apply()
    10. Reflection            ←  ReflectionEngine.fire_and_forget() (bg)

The model output is fed through `PositiveFilter` before it is returned
or transmitted — enforcing FUNDAMENTAL_LAW #3 (Positive Anchor).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .emotions import Emotions, EmotionVector
from .empathy import EmpathyEngine, EmpathyReading
from .monitor import Monitor, VitalSigns
from .positive_filter import FilterResult, PositiveFilter
from .provider import ChatMessage, Provider, ProviderUnavailable
from .soul_handler import SoulHandler
from .stm import ShortTermMemory

if TYPE_CHECKING:
    from tools.searxng import SearXNG
    from .cognition import CognitiveLoop, CognitiveTrace
    from .reflection import ReflectionEngine
    from .scheduler import Task, TaskAction, TaskSpec, TaskUpdate
    from .stm import Turn

log = logging.getLogger(__name__)


# Search-intent triggers. Plain English + Cantonese/Chinese.
_SEARCH_INTENT_RE = re.compile(
    r"\b(web\s*search|search\s+(for|about|the\s+web)|google|look\s+up|"
    r"search\s+online|find\s+(out|information)\s+about)\b|"
    r"(搜尋|搜索|上網查|查一下|查詢)",
    re.IGNORECASE,
)
# Trim leading "search for" / "web search about" so we send a clean query to SearXNG.
_QUERY_STRIP_RE = re.compile(
    r"^\s*(please\s+)?(can\s+you\s+)?"
    r"(do\s+a\s+)?(web\s*search|google|look\s+up|search(\s+(for|about|the\s+web|online))?|"
    r"find\s+(out|information)\s+about|搜尋|搜索|上網查|查一下|查詢)\s*[:：,，]?\s*",
    re.IGNORECASE,
)
# Cheap chitchat skip — don't waste a triage call on greetings, thanks, etc.
# The address-form tokens (the words after "hi" / "hey") are persona-specific
# and must be derived from the agent's Designation in soul.md at runtime, NOT
# hardcoded here. See `_build_chitchat_re()` below and `Brain._chitchat_re`.
_CHITCHAT_PATTERN_TEMPLATE = (
    r"^\s*("
    r"(hi+|hello|hey|yo|sup)(\s+(there|all|everyone|y'?all|guys|{address}))?"
    r"|thanks(\s+a\s+lot|\s+so\s+much)?|thank\s+you|ty|tysm"
    r"|ok(ay)?|cool|nice|great|awesome|got\s+it|noted|understood|roger"
    r"|good\s*(morning|afternoon|evening|night)|gn|gm"
    r"|bye|see\s*you|cya|good\s*bye|later"
    r"|你好|嗨|哈囉|多謝|謝謝|晚安|早安|再見|不用了"
    r")\s*[!?。？！.,]*\s*$"
)


def _build_chitchat_re(designation: str) -> re.Pattern[str]:
    """Compile the chitchat regex with persona-specific address tokens.

    The configured Designation (sourced from `cfg.system.individual_designation`
    via SoulHandler injection — see `core.soul_handler`) is split on
    whitespace and each token is lowercased into the address alternation,
    so an "hi <token>" / "hey <token>" greeting is recognised as chitchat
    regardless of what name the operator chose.
    """
    tokens = [re.escape(tok.lower()) for tok in (designation or "").split() if tok]
    address_alt = "|".join(tokens) if tokens else r"agent"
    return re.compile(
        _CHITCHAT_PATTERN_TEMPLATE.format(address=address_alt),
        re.IGNORECASE,
    )

# ---------------------------------------------------------------------------
# Identity short-circuit
# ---------------------------------------------------------------------------
# The small SLM (qwen2:1.5b) reliably mishandles the most basic identity
# questions. From production logs:
#   "what is your name"  -> triage decides SEARCH, query="name"  -> Netflix
#                           "My Name" + Wikipedia "Your Name" anime polluted
#                           the synth context, output became junk
#   "did you know my name" -> triage decided SEARCH on "know my name"
#   "how are you"          -> triage emitted query="<your name>" (literal)
# Even though the IDENTITY block is part of every system prompt, qwen2:1.5b
# attends to the LIVE KNOWLEDGE block more strongly than the persona block
# when the input is short and ambiguous, so polluted web hits win.
#
# Fix: detect a small set of unambiguous identity-class questions with
# regex BEFORE any cognition / triage / search runs, and return a
# templated reply built from soul.md + config.yaml + live vitals/mood.
# Deterministic. Zero SLM round-trip. Zero web hit. Zero hallucination.
# Compound questions (e.g. "what's your name and what can you do?") are
# NOT short-circuited because they're anchored with `^...$` end markers.
_IDENT_END = r"[\s!?\.\u3002\uff01\uff1f]*$"  # tolerate ASCII + CJK terminators

# "What is your name?" / "Who are you?" / "Tell me your name" / "Introduce yourself"
_AGENT_NAME_RE = re.compile(
    r"^\s*(?:hi|hey|hello|so|please|can\s+you|could\s+you)?\s*"
    r"(?:tell\s+me\s+(?:what(?:'?s|\s+is)\s+)?your\s+(?:name|designation))" + _IDENT_END
    + r"|^\s*(?:hi|hey|hello|so|please|can\s+you|could\s+you)?\s*"
    r"(?:what(?:'?s|\s+is|\s+are)|whats|wat\s+is)\s+"
    r"(?:your|ur)\s+(?:name|designation)" + _IDENT_END
    + r"|^\s*who\s+(?:are|r)\s+(?:you|u)" + _IDENT_END
    + r"|^\s*(?:your|ur)\s+name\s*\??\s*$"
    + r"|^\s*introduce\s+(?:yourself|urself)" + _IDENT_END
    + r"|^\s*你叫(?:咩|乜|什麼|甚麼)?名" + _IDENT_END
    + r"|^\s*你係邊個" + _IDENT_END
    + r"|^\s*你是誰" + _IDENT_END,
    re.IGNORECASE,
)

# "Do you know my name?" / "What's my name?" / "Who am I?"
_USER_NAME_RE = re.compile(
    r"^\s*(?:do|did|does)\s+(?:you|u)\s+(?:know|remember|recall)\s+"
    r"(?:my|the\s+architect'?s|the\s+operator'?s)\s+name" + _IDENT_END
    + r"|^\s*(?:you|u)\s+know\s+my\s+name" + _IDENT_END
    + r"|^\s*what(?:'?s|\s+is)\s+my\s+name" + _IDENT_END
    + r"|^\s*who\s+am\s+i" + _IDENT_END
    + r"|^\s*remember\s+me" + _IDENT_END
    + r"|^\s*你(?:仲|還)?記(?:得|唔記得)?我(?:嘅|個|的)?名" + _IDENT_END
    + r"|^\s*我叫(?:咩|乜|什麼|甚麼)?名" + _IDENT_END,
    re.IGNORECASE,
)

# "Who created you?" / "Who is your creator?" — already mostly works through
# the soul prompt but short-circuit makes it deterministic + cheap.
_CREATOR_QUESTION_RE = re.compile(
    r"^\s*who\s+(?:created|made|built|developed|wrote|designed|coded|programmed)\s+"
    r"(?:you|u)" + _IDENT_END
    + r"|^\s*who(?:'?s|\s+is)\s+your\s+"
    r"(?:creator|maker|developer|architect|author|coder|designer|builder)"
    + _IDENT_END
    + r"|^\s*who\s+create\s+you" + _IDENT_END
    + r"|^\s*你(?:嘅|的)?(?:創造者|創建者|作者|開發者)係(?:邊個|誰)" + _IDENT_END
    + r"|^\s*邊個(?:創造|建立|開發|寫|做)(?:咗|了)?(?:你|您)" + _IDENT_END,
    re.IGNORECASE,
)

# "How are you?" / "Are you ok?" — status query; uses live vitals + mood.
_STATUS_QUESTION_RE = re.compile(
    r"^\s*how\s+(?:are|r)\s+(?:you|u)(?:\s+(?:doing|today|feeling))?" + _IDENT_END
    + r"|^\s*how(?:'?s|\s+is)\s+(?:it\s+going|your\s+day|life|everything)"
    + _IDENT_END
    + r"|^\s*are\s+(?:you|u)\s+(?:ok|okay|alright|good|fine|well)" + _IDENT_END
    + r"|^\s*how\s+do\s+(?:you|u)\s+feel" + _IDENT_END
    + r"|^\s*你(?:今日)?(?:點|怎麼樣?|還好嗎?|好嗎)" + _IDENT_END,
    re.IGNORECASE,
)

# Cap on triage output length — protects against runaway generation.
_MAX_TRIAGE_TOKENS_HINT: int = 40
# How many recent STM turns to feed into the triage classifier so it can
# disambiguate follow-up messages whose meaning depends on prior context
# (pronouns, ellipsis, ambiguous proper nouns). Kept small to control
# prompt size on the Pi 5.
_TRIAGE_CONTEXT_TURNS: int = 4
# Per-turn character cap when rendering STM context for triage. Architect
# turns get the full window (they're the user's own words). Agent turns are
# trimmed aggressively because they often contain web-search excerpts and
# URL titles which the small SLM otherwise lifts verbatim into new queries
# instead of constructing one from the user's actual current message.
_TRIAGE_CTX_ARCHITECT_CHARS: int = 200
_TRIAGE_CTX_AGENT_CHARS: int = 80
# Strips markdown links, raw URLs, and bracketed citations from agent text
# before it goes into the triage prompt.
_AGENT_CTX_URL_RE = re.compile(r"https?://\S+|\[[^\]]+\]\([^)]+\)|\([^)]*https?://[^)]*\)")

# --- Complex-input detector (used to override vitals_stressed bypass) -------
# When the SLM is under load AND the user's request is non-trivial, bypassing
# cognition is the worst possible time to do it: the unaided synthesis path
# is the most likely to regurgitate system-prompt scaffolding (see prompt-
# leak detector below). For complex inputs we'd rather pay the THINK→PLAN
# latency than ship a degraded reply.
_MULTIPART_CONNECTORS_RE = re.compile(
    r"\b(and|also|then|plus|moreover|furthermore|additionally)\b\s+(give|tell|"
    r"show|list|find|explain|describe|provide|share|suggest|recommend|"
    r"compare|summari[sz]e)\b",
    re.IGNORECASE,
)


def _is_complex_input(text: str) -> bool:
    """True if `text` is structurally complex enough to warrant cognition.

    Pattern-only — never inspects topic words. Triggers when the message has
    multiple question marks, is long, or chains an imperative after a
    connector ("and give me…", "also list…").
    """
    s = (text or "").strip()
    if not s:
        return False
    if s.count("?") >= 2:
        return True
    if len(s) > 80:
        return True
    if _MULTIPART_CONNECTORS_RE.search(s):
        return True
    return False


# --- Prompt-leak detector ----------------------------------------------------
# Distinctive phrases lifted from the system prompt scaffolding. Kept tight
# on purpose: every fingerprint must be a phrase the agent CANNOT produce
# in a legitimate user-facing reply. Generic-English phrases ("your name
# is", "silent rules", etc.) are intentionally NOT in this list — they
# cause false positives. The right answer to a small-SLM echoing the
# scaffolding is to make the scaffolding harder to echo (see _assemble's
# first-person identity line + few-shot operator block), not to grow this
# fingerprint list.
_PROMPT_LEAK_FINGERPRINTS: tuple[str, ...] = (
    # Section headers — the only place these exact strings appear is the
    # system prompt, so any occurrence in a reply is unambiguous regurgitation.
    "## SOUL CONTEXT",
    "## OPERATOR",
    "## PHYSICAL STATE",
    "## INTERNAL MOOD",
    "## USER EMPATHY",
    "## KNOWLEDGE",
    "## THIS TURN",
    # Distinctive imperative phrases used by sub-blocks.
    "EXHAUSTION DIRECTIVE",
    "Live SearXNG results have been pre-fetched",
    "Synthesize a concise, accurate answer grounded",
    "Heartbeat-triggered mission",
    "Idle pulse — produce a brief situational reflection",
    "Positive Anchor MUST hold",
)

# Last-resort apology when both the primary synthesis and the minimal-prompt
# retry produced prompt-leak output. Single source of truth — `{salutation}`
# is interpolated at call-time from the live config so the apology addresses
# the operator by their configured honorific+name (no hardcoded "Boss Eason").
_LEAK_FALLBACK_TEMPLATE: str = (
    "I had trouble forming a clean answer just now, {salutation}. "
    "Could you ask again in a moment?"
)


def _looks_like_prompt_leak(text: str) -> str | None:
    """Return the matched fingerprint if `text` regurgitates the system prompt, else None."""
    if not text:
        return None
    for fp in _PROMPT_LEAK_FINGERPRINTS:
        if fp in text:
            return fp
    return None


@dataclass(frozen=True)
class ThoughtTrace:
    system_prompt: str
    raw_response: str
    filtered: FilterResult
    vitals: VitalSigns
    empathy: EmpathyReading
    backend: str


class Brain:
    """Coordinator for a single Prompt Assembly cycle."""

    def __init__(
        self,
        *,
        soul: SoulHandler,
        monitor: Monitor,
        emotions: Emotions,
        empathy: EmpathyEngine,
        positive_filter: PositiveFilter,
        provider: Provider,
        stm: ShortTermMemory,
        archive_path: str | Path,
        architect_name: str = "Architect",
        architect_honorific: str = "Boss",
        searxng: "SearXNG | None" = None,
        web_search_triage_enabled: bool = True,
        ltm_short_circuit_enabled: bool = True,
        ltm_short_circuit_min_score: int = 2,
        reflection: "ReflectionEngine | None" = None,
        cognition: "CognitiveLoop | None" = None,
    ) -> None:
        self._soul = soul
        self._monitor = monitor
        self._emotions = emotions
        self._empathy = empathy
        self._filter = positive_filter
        self._provider = provider
        self._stm = stm
        self._archive_path = Path(archive_path)
        self._architect_name = (architect_name or "Architect").strip() or "Architect"
        self._architect_honorific = (architect_honorific or "").strip()
        self._searxng = searxng
        self._triage_enabled = web_search_triage_enabled
        self._ltm_short_circuit_enabled = bool(ltm_short_circuit_enabled)
        self._ltm_short_circuit_min_score = max(1, int(ltm_short_circuit_min_score))
        self._reflection = reflection
        self._cognition = cognition
        # Lazy-built once per Brain instance from the live soul.md Designation.
        # soul.md is read async, so we can't build it in __init__; the first
        # call to _maybe_web_search() populates it.
        self._chitchat_re: re.Pattern[str] | None = None

    # ---------- public entry points ------------------------------------------

    async def think(self, user_input: str) -> ThoughtTrace:
        """Full PROMPT_ASSEMBLY cycle for an incoming user message."""
        trace = await self._cycle(user_input=user_input, mission=None)
        # Self-reflection runs in the background — it must NOT delay the reply
        # the connector is about to send. Skip when the provider is offline:
        # there's no real reply to learn from and reflection itself would
        # just hit the same dead endpoint.
        if self._reflection is not None and trace.backend != "offline":
            await self._reflection.fire_and_forget(
                kind="user",
                input_text=user_input,
                response=trace.filtered.text,
                web_searched="Live SearXNG results" in trace.system_prompt,
                backend=trace.backend,
            )
        return trace

    async def proactive_thought(self, mission: str) -> ThoughtTrace:
        """Heartbeat-triggered thought (no human input)."""
        return await self._cycle(user_input=None, mission=mission)

    # ---------- recurring task pipeline (core/scheduler.py) ------------------

    async def parse_task_intent(self, user_input: str) -> "TaskSpec | None":
        """Decide whether `user_input` is a recurring-task request.

        Two-stage parser:
          1. Cheap regex pre-filter (`scheduler.looks_like_task_request`).
             If the message contains no interval-words ("every X hours",
             "hourly", "daily", "every minute", …) we return None
             immediately — no SLM call. This means a normal chat message
             never pays the parse cost.
          2. SLM intent extraction. If the pre-filter matched, ask the
             SLM to extract topic + interval + queries in a strict format.
             We then validate every field; any failure → None (the
             connector falls back to normal `think()`).

        Returns a `TaskSpec` ready for `TaskScheduler.add_task`, or None
        when the message is not a scheduling request (or could not be
        parsed safely). Callers MUST treat None as "fall back to chat".
        """
        # Local import: scheduler imports Brain via TYPE_CHECKING; doing
        # the same here would only be cosmetic, so we just import inside
        # the function (called rarely — once per chat turn at most).
        from .scheduler import (
            TaskSpec,
            extract_explicit_interval,
            looks_like_task_request,
            parse_interval_spec,
        )

        if not user_input or not looks_like_task_request(user_input):
            return None

        system = (
            "You are an INTENT PARSER for a personal AI agent. The operator "
            "may be requesting a RECURRING research task (something the agent "
            "should run on a schedule and report back) or just chatting.\n\n"
            "Your job: decide which, and if it IS a recurring task, extract "
            "the schedule and the search queries.\n\n"
            "Output EXACTLY one of these two forms — nothing else, no preamble:\n\n"
            "  NOT_TASK\n\n"
            "  TASK\n"
            "  TOPIC: <short label, ≤ 8 words>\n"
            "  INTERVAL: <number><unit>  (units: m / h / d / w; e.g. 1h, 30m, 1d)\n"
            "  QUERIES:\n"
            "    - <one search query per line, 2–5 queries total>\n\n"
            "RULES:\n"
            "  • TASK only when the operator clearly asks for something\n"
            "    REPEATING on a schedule. One-off questions are NOT_TASK.\n"
            "  • INTERVAL must use a numeric unit. 'hourly' → 1h, 'daily' → 1d.\n"
            "  • QUERIES are what you would type into a web search engine\n"
            "    to gather the information needed. Be specific. Include\n"
            "    company names / ticker symbols / topic keywords explicitly.\n"
            "  • If you cannot confidently extract all three fields, emit\n"
            "    NOT_TASK — the agent will then handle the message as\n"
            "    normal chat. Better to miss a task than to schedule\n"
            "    something the operator didn't want.\n\n"
            "EXAMPLES:\n\n"
            "  Operator: \"check the Microsoft stock price and news every "
            "hour and give me an insight summary\"\n"
            "  →\n"
            "  TASK\n"
            "  TOPIC: Microsoft stock + news\n"
            "  INTERVAL: 1h\n"
            "  QUERIES:\n"
            "    - Microsoft MSFT stock price today\n"
            "    - Microsoft latest news\n\n"
            "  Operator: \"every morning summarise top AI research papers\"\n"
            "  →\n"
            "  TASK\n"
            "  TOPIC: Daily AI research digest\n"
            "  INTERVAL: 1d\n"
            "  QUERIES:\n"
            "    - top AI research papers this week\n"
            "    - latest LLM research arxiv\n\n"
            "  Operator: \"hello, how are you doing today\"\n"
            "  → NOT_TASK\n\n"
            "  Operator: \"what is the capital of France\"\n"
            "  → NOT_TASK"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=user_input.strip())],
            )
        except Exception:
            log.exception("parse_task_intent: provider call failed")
            return None
        text = (raw or "").strip()
        if not text:
            return None
        # Cheap rejection paths.
        upper_first = text.splitlines()[0].strip().upper()
        if upper_first.startswith("NOT_TASK") or "NOT_TASK" in upper_first:
            log.info("TASK parse: SLM returned NOT_TASK")
            return None
        if not upper_first.startswith("TASK"):
            log.info("TASK parse: SLM did not start with TASK or NOT_TASK — "
                     "falling back to chat. raw=%r", text[:200])
            return None

        # Field extraction. Tolerant: tokens may be in any order, may have
        # extra whitespace, queries may use various bullet markers.
        topic_m = re.search(r"^\s*TOPIC\s*:\s*(?P<v>.+)$", text, re.IGNORECASE | re.MULTILINE)
        interval_m = re.search(r"^\s*INTERVAL\s*:\s*(?P<v>.+)$", text, re.IGNORECASE | re.MULTILINE)
        queries_block_m = re.search(
            r"^\s*QUERIES\s*:\s*\n(?P<v>(?:\s*[-*•]?\s*.+\n?)+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if not (topic_m and interval_m and queries_block_m):
            log.info(
                "TASK parse: missing fields topic=%s interval=%s queries=%s",
                bool(topic_m), bool(interval_m), bool(queries_block_m),
            )
            return None
        topic = topic_m.group("v").strip().strip("\"'`")
        interval_seconds = parse_interval_spec(interval_m.group("v").strip())
        if interval_seconds is None:
            log.info("TASK parse: bad interval %r", interval_m.group("v"))
            return None
        # Deterministic override — qwen2:1.5b copies the prompt example
        # ("INTERVAL: 1h") rather than respecting the operator's literal
        # cadence ("every 10 minutes"). When the operator's text contains
        # an explicit cadence phrase, trust THAT over whatever the SLM
        # emitted. See `extract_explicit_interval` in scheduler.py for
        # the supported phrasings.
        explicit = extract_explicit_interval(user_input)
        if explicit is not None and explicit != interval_seconds:
            log.info(
                "TASK parse: SLM emitted %ds but operator said %ds — using operator's value",
                interval_seconds, explicit,
            )
            interval_seconds = explicit
        queries: list[str] = []
        for line in queries_block_m.group("v").splitlines():
            ln = line.strip()
            ln = re.sub(r"^[-*•]\s*", "", ln).strip().strip("\"'`")
            if ln:
                queries.append(ln)
        # Cap to 5 — anything beyond is excess SLM cost per fire.
        queries = queries[:5]
        if not queries:
            log.info("TASK parse: no queries extracted")
            return None
        log.info(
            "TASK parse OK topic=%r interval=%ds queries=%d",
            topic, interval_seconds, len(queries),
        )
        return TaskSpec(
            topic=topic,
            queries=queries,
            interval_seconds=interval_seconds,
            description=user_input.strip(),
        )

    async def parse_task_modify_intent(
        self,
        user_input: str,
        current_tasks: list["Task"],
    ) -> "TaskUpdate | None":
        """Decide whether `user_input` asks to MODIFY an existing task.

        Two-stage parser, mirrors `parse_task_intent`:
          1. Cheap regex pre-filter (`scheduler.looks_like_task_modify_request`).
             Skips the SLM call when the message has no update verbs.
          2. SLM resolves the operator's reference ("the MSFT task",
             "t8f3a", "my hourly one") against the live task list and
             extracts which fields are being changed.

        Returns a `TaskUpdate` (with task_id + only the fields that change)
        ready for `TaskScheduler.update_task(...)`. Returns None when the
        message is not a modify request, when no live tasks exist, or
        when the SLM cannot safely resolve the target. Callers MUST treat
        None as "fall back to the next intent path".
        """
        from .scheduler import (
            TaskUpdate,
            extract_explicit_interval,
            looks_like_task_modify_request,
            parse_interval_spec,
        )

        if not user_input or not looks_like_task_modify_request(user_input):
            return None
        if not current_tasks:
            # Nothing to modify — don't waste an SLM call. The connector
            # will fall through to chat and the operator will see a
            # "no tasks" reply via Brain.think.
            log.info("TASK modify parse: no live tasks — skipping SLM")
            return None

        # Compact task table for the prompt. Keep it short so the SLM
        # doesn't drown in context: id, topic, current interval.
        from .scheduler import format_interval as _fmt_iv
        task_table = "\n".join(
            f"  {t.id}  |  {t.topic}  |  every {_fmt_iv(t.interval_seconds)}"
            for t in current_tasks
        )

        system = (
            "You are an INTENT PARSER for a personal AI agent. The operator "
            "may be asking to MODIFY one of their existing recurring tasks "
            "(change its cadence, topic, or search queries) or just chatting.\n\n"
            "Here are the operator's CURRENT tasks (id | topic | cadence):\n"
            f"{task_table}\n\n"
            "Your job: decide whether the operator is editing one of these, "
            "and if so, identify which task and which fields they're changing.\n\n"
            "Output EXACTLY one of these two forms \u2014 nothing else, no preamble:\n\n"
            "  NOT_MODIFY\n\n"
            "  MODIFY\n"
            "  TARGET_ID: <one of the ids above, exact match>\n"
            "  INTERVAL: <number><unit>  (omit line entirely if unchanged)\n"
            "  TOPIC: <new short label>  (omit line entirely if unchanged)\n"
            "  QUERIES:                  (omit BLOCK entirely if unchanged)\n"
            "    - <one search query per line, 2\u20135 queries>\n\n"
            "RULES:\n"
            "  \u2022 MODIFY only when the operator clearly references an EXISTING\n"
            "    task above. \"Schedule a new task to ...\" is NOT_MODIFY.\n"
            "  \u2022 TARGET_ID must be an exact id from the table. If you can\n"
            "    not unambiguously pick one, emit NOT_MODIFY.\n"
            "  \u2022 Emit ONLY the fields the operator is changing. Omitted\n"
            "    lines mean \"keep current value\".\n"
            "  \u2022 INTERVAL uses a numeric unit. 'hourly' \u2192 1h, 'daily' \u2192 1d.\n"
            "  \u2022 If unsure, emit NOT_MODIFY \u2014 better to ignore than to\n"
            "    silently rewrite the operator's task.\n\n"
            "EXAMPLES (with current tasks t8f3a=Microsoft stock + news every 1h, "
            "taa12=Daily AI digest every 1 day):\n\n"
            "  Operator: \"change task t8f3a to every 2 hours\"\n"
            "  \u2192\n"
            "  MODIFY\n"
            "  TARGET_ID: t8f3a\n"
            "  INTERVAL: 2h\n\n"
            "  Operator: \"update the Microsoft task to also track Azure news\"\n"
            "  \u2192\n"
            "  MODIFY\n"
            "  TARGET_ID: t8f3a\n"
            "  QUERIES:\n"
            "    - Microsoft MSFT stock price today\n"
            "    - Microsoft latest news\n"
            "    - Microsoft Azure news\n\n"
            "  Operator: \"rename the AI digest to Morning AI brief\"\n"
            "  \u2192\n"
            "  MODIFY\n"
            "  TARGET_ID: taa12\n"
            "  TOPIC: Morning AI brief\n\n"
            "  Operator: \"thanks!\"  \u2192 NOT_MODIFY\n"
            "  Operator: \"schedule a new task to track gold prices hourly\"  \u2192 NOT_MODIFY"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=user_input.strip())],
            )
        except Exception:
            log.exception("parse_task_modify_intent: provider call failed")
            return None
        text = (raw or "").strip()
        if not text:
            return None
        upper_first = text.splitlines()[0].strip().upper()
        if upper_first.startswith("NOT_MODIFY") or "NOT_MODIFY" in upper_first:
            log.info("TASK modify parse: SLM returned NOT_MODIFY")
            return None
        if not upper_first.startswith("MODIFY"):
            log.info("TASK modify parse: SLM did not start with MODIFY/NOT_MODIFY \u2014 "
                     "falling back. raw=%r", text[:200])
            return None

        # Tolerant field extraction \u2014 same approach as parse_task_intent.
        target_m = re.search(r"^\s*TARGET_ID\s*:\s*(?P<v>\S+)\s*$",
                             text, re.IGNORECASE | re.MULTILINE)
        if not target_m:
            log.info("TASK modify parse: missing TARGET_ID")
            return None
        target_id = target_m.group("v").strip().strip("\"'`")
        # Reject ids the SLM hallucinated.
        valid_ids = {t.id for t in current_tasks}
        if target_id not in valid_ids:
            log.info("TASK modify parse: SLM returned unknown id %r (have %s)",
                     target_id, sorted(valid_ids))
            return None

        new_topic: str | None = None
        new_interval_seconds: int | None = None
        new_queries: list[str] | None = None

        topic_m = re.search(r"^\s*TOPIC\s*:\s*(?P<v>.+)$",
                            text, re.IGNORECASE | re.MULTILINE)
        if topic_m:
            cand = topic_m.group("v").strip().strip("\"'`")
            if cand:
                new_topic = cand

        interval_m = re.search(r"^\s*INTERVAL\s*:\s*(?P<v>.+)$",
                               text, re.IGNORECASE | re.MULTILINE)
        if interval_m:
            secs = parse_interval_spec(interval_m.group("v").strip())
            if secs is None:
                log.info("TASK modify parse: bad interval %r", interval_m.group("v"))
                return None
            new_interval_seconds = secs
        # Deterministic override — same rationale as parse_task_intent.
        # If the operator's modify request contains an explicit cadence
        # phrase ("change t8f3a to every 10 minutes"), trust THAT over the
        # SLM, even when the SLM omitted INTERVAL entirely.
        explicit = extract_explicit_interval(user_input)
        if explicit is not None and explicit != new_interval_seconds:
            if new_interval_seconds is not None:
                log.info(
                    "TASK modify parse: SLM emitted %ds but operator said %ds — using operator's value",
                    new_interval_seconds, explicit,
                )
            else:
                log.info(
                    "TASK modify parse: SLM omitted INTERVAL but operator said %ds — applying it",
                    explicit,
                )
            new_interval_seconds = explicit

        queries_block_m = re.search(
            r"^\s*QUERIES\s*:\s*\n(?P<v>(?:\s*[-*\u2022]?\s*.+\n?)+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if queries_block_m:
            qs: list[str] = []
            for line in queries_block_m.group("v").splitlines():
                ln = line.strip()
                ln = re.sub(r"^[-*\u2022]\s*", "", ln).strip().strip("\"'`")
                if ln:
                    qs.append(ln)
            qs = qs[:5]
            if qs:
                new_queries = qs

        if new_topic is None and new_interval_seconds is None and new_queries is None:
            # SLM identified a target but extracted no actual changes\u2014
            # treat as a no-op rather than triggering a registry write.
            log.info("TASK modify parse: no changed fields for id=%s", target_id)
            return None
        log.info(
            "TASK modify parse OK id=%s topic=%s interval=%s queries=%s",
            target_id,
            "yes" if new_topic else "no",
            f"{new_interval_seconds}s" if new_interval_seconds else "no",
            len(new_queries) if new_queries else "no",
        )
        return TaskUpdate(
            task_id=target_id,
            new_topic=new_topic,
            new_interval_seconds=new_interval_seconds,
            new_queries=new_queries,
            new_description=user_input.strip(),
        )

    async def parse_task_action_intent(
        self,
        user_input: str,
        current_tasks: list["Task"],
    ) -> "TaskAction | None":
        """Decide whether `user_input` asks to CANCEL / PAUSE / RESUME a task.

        Two-stage parser, mirrors `parse_task_modify_intent`:
          1. Cheap regex pre-filter (`scheduler.looks_like_task_action_request`).
             Skips the SLM call when the message has no action verbs.
          2. SLM resolves which task the operator means ("the MSFT one",
             "t8f3a", "my hourly task") against the live task list and
             picks one of the three canonical actions.

        Fast-path before the SLM: when the operator's message contains
        an explicit task id token (`t[0-9a-f]{4}`) AND the action verb
        is unambiguous, dispatch directly. This makes "/cancel-style"
        NL phrases ("cancel t091f", "pause t091f") deterministic and
        zero-cost.

        Returns a `TaskAction` (with task_id + action) ready for
        `TaskScheduler.cancel_task` / `TaskScheduler.pause_task`. Returns
        None when the message is not an action request, when no live tasks
        exist, or when the SLM cannot safely resolve the target. Callers
        MUST treat None as "fall back to the next intent path".
        """
        from .scheduler import TaskAction, looks_like_task_action_request

        if not user_input or not looks_like_task_action_request(user_input):
            return None
        if not current_tasks:
            log.info("TASK action parse: no live tasks — skipping SLM")
            return None

        valid_ids = {t.id for t in current_tasks}

        # Fast-path: explicit id + unambiguous action verb. Skips the SLM
        # entirely. We classify the verb by which group matches first in
        # a tight regex; if the message somehow contains BOTH a cancel
        # AND a pause verb we fall through to the SLM for arbitration.
        text_lc = user_input.lower()
        id_m = re.search(r"\bt[0-9a-f]{4}\b", text_lc)
        if id_m and id_m.group(0) in valid_ids:
            cancel_hit = bool(re.search(
                r"\b(cancel|stop|delete|remove|kill|abort|terminate|end)\b",
                text_lc,
            ))
            pause_hit = bool(re.search(
                r"\b(pause|suspend|halt|freeze)\b", text_lc,
            ))
            resume_hit = bool(re.search(
                r"\b(resume|unpause|restart|continue|reactivate|re-?enable)\b",
                text_lc,
            ))
            hits = sum([cancel_hit, pause_hit, resume_hit])
            if hits == 1:
                action = (
                    "cancel" if cancel_hit
                    else ("pause" if pause_hit else "resume")
                )
                log.info(
                    "TASK action parse OK (fast-path) id=%s action=%s",
                    id_m.group(0), action,
                )
                return TaskAction(task_id=id_m.group(0), action=action)

        # Compact task table for the prompt — same shape as modify path.
        from .scheduler import format_interval as _fmt_iv
        task_table = "\n".join(
            f"  {t.id}  |  {t.topic}  |  every {_fmt_iv(t.interval_seconds)}"
            f"  |  {'paused' if t.paused else 'active'}"
            for t in current_tasks
        )

        system = (
            "You are an INTENT PARSER for a personal AI agent. The operator "
            "may be asking to CANCEL, PAUSE, or RESUME one of their existing "
            "recurring tasks, or just chatting.\n\n"
            "Here are the operator's CURRENT tasks (id | topic | cadence | state):\n"
            f"{task_table}\n\n"
            "Your job: decide whether the operator is asking for one of the "
            "three actions, identify which task, and which action.\n\n"
            "Output EXACTLY one of these two forms — nothing else, no preamble:\n\n"
            "  NOT_ACTION\n\n"
            "  ACTION\n"
            "  TARGET_ID: <one of the ids above, exact match>\n"
            "  VERB: <one of: cancel, pause, resume>\n\n"
            "RULES:\n"
            "  • ACTION only when the operator clearly references an EXISTING\n"
            "    task above AND uses an action verb. Modify requests like\n"
            "    'change the cadence' are NOT_ACTION.\n"
            "  • TARGET_ID must be an exact id from the table. If you can\n"
            "    not unambiguously pick one, emit NOT_ACTION.\n"
            "  • VERB must be exactly one of: cancel, pause, resume.\n"
            "    Map operator synonyms:\n"
            "      cancel ← stop, delete, remove, kill, abort, terminate, end\n"
            "      pause  ← pause, suspend, halt, freeze\n"
            "      resume ← resume, unpause, restart, continue, reactivate\n"
            "  • If unsure, emit NOT_ACTION — better to ignore than to\n"
            "    silently delete the operator's task.\n\n"
            "EXAMPLES (with current tasks t8f3a=Microsoft stock + news every 1h "
            "active, taa12=Daily AI digest every 1 day paused):\n\n"
            "  Operator: \"cancel the MSFT task\"\n"
            "  →\n"
            "  ACTION\n"
            "  TARGET_ID: t8f3a\n"
            "  VERB: cancel\n\n"
            "  Operator: \"pause my hourly one\"\n"
            "  →\n"
            "  ACTION\n"
            "  TARGET_ID: t8f3a\n"
            "  VERB: pause\n\n"
            "  Operator: \"resume the AI digest\"\n"
            "  →\n"
            "  ACTION\n"
            "  TARGET_ID: taa12\n"
            "  VERB: resume\n\n"
            "  Operator: \"thanks\"  → NOT_ACTION\n"
            "  Operator: \"change t8f3a to every 2 hours\"  → NOT_ACTION"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=user_input.strip())],
            )
        except Exception:
            log.exception("parse_task_action_intent: provider call failed")
            return None
        text = (raw or "").strip()
        if not text:
            return None
        upper_first = text.splitlines()[0].strip().upper()
        if upper_first.startswith("NOT_ACTION") or "NOT_ACTION" in upper_first:
            log.info("TASK action parse: SLM returned NOT_ACTION")
            return None
        if not upper_first.startswith("ACTION"):
            log.info(
                "TASK action parse: SLM did not start with ACTION/NOT_ACTION — "
                "falling back. raw=%r", text[:200],
            )
            return None

        target_m = re.search(
            r"^\s*TARGET_ID\s*:\s*(?P<v>\S+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        verb_m = re.search(
            r"^\s*VERB\s*:\s*(?P<v>\S+)\s*$",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if not (target_m and verb_m):
            log.info(
                "TASK action parse: missing fields target=%s verb=%s",
                bool(target_m), bool(verb_m),
            )
            return None
        target_id = target_m.group("v").strip().strip("\"'`")
        if target_id not in valid_ids:
            log.info(
                "TASK action parse: SLM returned unknown id %r (have %s)",
                target_id, sorted(valid_ids),
            )
            return None
        verb = verb_m.group("v").strip().strip("\"'`").lower()
        if verb not in {"cancel", "pause", "resume"}:
            log.info("TASK action parse: SLM returned invalid verb %r", verb)
            return None
        log.info("TASK action parse OK id=%s action=%s", target_id, verb)
        return TaskAction(task_id=target_id, action=verb)

    async def synthesize_task_report(
        self,
        *,
        topic: str,
        description: str,
        brief: str,
    ) -> str:
        """Convert raw SearXNG snippets into the operator-facing report.

        Uses the existing `proactive_thought` cycle (so identity, mood,
        positive-filter, and reflection all fire) but with a mission text
        crafted for scheduled-task analysis: the model is told to (a)
        extract the concrete data points, (b) cross-reference across
        sources, and (c) honestly flag freshness/quality limits.
        """
        topic = (topic or "task").strip()
        description = (description or "").strip()
        brief = (brief or "").strip() or "(no findings)"
        mission = (
            f"SCHEDULED TASK: {topic}\n"
            f"Operator's original request: {description}\n\n"
            f"Web findings (multiple queries, freshly fetched):\n{brief}\n\n"
            "Produce a concise INSIGHT SUMMARY REPORT for the operator. "
            "Structure: (1) one headline sentence with the most important "
            "finding, (2) 2-4 bullet points of supporting facts drawn "
            "ONLY from the findings above (cite source URL inline), "
            "(3) one closing line on what changed since last cycle (or "
            "'no comparison available' if this is the first run). Be "
            "honest about freshness — if the snippets don't contain a "
            "current price/number, say so rather than inventing one. "
            "Keep the whole report under 12 lines."
        )
        try:
            trace = await self.proactive_thought(mission)
        except Exception:
            log.exception("synthesize_task_report: proactive_thought failed")
            raise
        # When the provider is offline, `proactive_thought` returns a
        # synthetic trace instead of raising. Surface that as a real
        # error so the scheduler records `last_error` and skips
        # broadcasting the friendly message as if it were a real report.
        if trace.backend == "offline":
            raise ProviderUnavailable(trace.filtered.text)
        return trace.filtered.text

    async def refine_proactive_reflection(
        self,
        *,
        topic: str,
        snippets: str,
        draft: str,
    ) -> tuple[str, str]:
        """Single-pass critique of an autonomous-research reflection.

        The full cognitive loop is intentionally bypassed for proactive
        thoughts (see `_cycle`'s `bypass_reason="proactive_turn"`) because
        topic selection already does the equivalent of THINK and the output
        is small + low-stakes. But that means there is NO REFINE pass — so
        if the SLM hallucinates a specific date/number/name from thin
        snippets, the bad claim flows straight into `proactive.jsonl` and
        may later be promoted into a Core Memory.

        This method closes that gap with ONE extra SLM call. The refiner
        re-reads the original snippets + the draft reflection and either:
          * returns ("OK", draft) — draft is supported, keep as-is; or
          * returns ("REWRITE", new_text) — draft made unsupported claims;
            here is a 2-sentence rewrite that sticks to what the snippets
            actually say (or honestly admits uncertainty).

        Failures are non-fatal: any exception or unparseable output → the
        original draft is returned with verdict "ERROR" so the proactive
        cycle never breaks because the refiner couldn't run.
        """
        topic = (topic or "").strip()
        draft = (draft or "").strip()
        if not draft:
            return ("OK", draft)
        system = (
            "You are the REFINE stage of an autonomous-research cycle.\n"
            "You see the TOPIC the agent researched, the WEB SNIPPETS that\n"
            "were retrieved, and a DRAFT reflection the agent wrote.\n\n"
            "Your ONLY job: catch SPECIFIC factual claims in the draft that\n"
            "the snippets do NOT support — invented dates, numbers, version\n"
            "strings, named people, named organisations, or causal chains\n"
            "the evidence cannot back up. Vague impressions and reasonable\n"
            "summaries of what the snippets say are FINE — keep those as OK.\n"
            "Confident-sounding specifics that the snippets do not contain\n"
            "are NOT fine — those need a REWRITE.\n\n"
            "Output EXACTLY one of:\n"
            "  OK\n"
            "  REWRITE: <a 2-sentence reflection that stays faithful to the\n"
            "           snippets, or honestly says the snippets were thin>\n\n"
            "DECISION RULES (apply in order — first match wins):\n"
            "  1. If the draft summarises or paraphrases what the snippets\n"
            "     actually say, even loosely → OK.\n"
            "  2. If the draft contains a number, date, version string,\n"
            "     person name, or organisation name that does NOT appear in\n"
            "     the snippets → REWRITE.\n"
            "  3. If the draft makes a causal claim (\"X causes Y\", \"X led to\n"
            "     Y\") with no evidence in the snippets → REWRITE.\n"
            "  4. Otherwise → OK.\n\n"
            "EXAMPLES:\n"
            "  Snippets mention \"~26 TOPS, 2.5W typical, PCIe Gen-3 x4\".\n"
            "  Draft: \"~26 TOPS at ~2.5 W over PCIe — strong edge fit.\"\n"
            "  → OK   (draft only restates snippet facts)\n\n"
            "  Snippets mention \"Project Y is in active development; no\n"
            "  release date announced\".\n"
            "  Draft: \"Project Y launches March 14 2025 with DLSS 4, built\n"
            "  by a 240-person team over 5 years.\"\n"
            "  → REWRITE: Project Y is still in active development at\n"
            "    Studio X, but no release date has been announced. Snippets\n"
            "    didn't reveal team size or feature specifics.\n\n"
            "Output ONLY the directive. No preamble, no explanation."
        )
        payload = (
            f"TOPIC: {topic}\n\n"
            f"WEB SNIPPETS:\n{snippets or '(none)'}\n\n"
            f"DRAFT REFLECTION:\n{draft}"
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=payload)],
            )
        except Exception:
            log.exception("Proactive REFINE call failed; keeping draft as-is")
            return ("ERROR", draft)

        text = (raw or "").strip()
        if not text:
            return ("OK", draft)
        # Strip a leading "REWRITE:" / "OK" token and parse the verdict.
        first_line = text.splitlines()[0].strip()
        upper = first_line.upper()
        if upper.startswith("OK"):
            return ("OK", draft)
        if upper.startswith("REWRITE"):
            # Everything after the first colon (across all lines) is the
            # rewritten reflection. Some SLMs put it on the next line.
            _, sep, after = text.partition(":")
            rewrite = (after if sep else text).strip()
            # Ditch any residual "REWRITE" tokens the SLM repeated.
            rewrite = re.sub(r"^\s*REWRITE\s*:?\s*", "", rewrite, flags=re.IGNORECASE)
            rewrite = rewrite.strip().strip("\"'`")
            if not rewrite or rewrite.lower() == draft.lower():
                return ("OK", draft)
            return ("REWRITE", rewrite)
        # Ambiguous output → conservative default = keep draft.
        return ("OK", draft)

    async def extract_stm_gaps(self, *, limit: int = 3) -> list[str]:
        """Read recent STM and ask the SLM to name 1-N specific concepts that
        an autonomous agent might benefit from researching.

        Used by the Heartbeat at the start of a Proactive Thought cycle to
        ground research in the actual conversation, not in static preferences.
        Returns an ordered list of candidate topics (most salient first).
        Empty list when STM is empty, the SLM produced nothing parseable, or
        the call failed — caller should fall back to LEARNED_PREFERENCES.
        """
        if limit <= 0:
            return []
        history = await self._stm.render()
        if not history:
            return []
        # Render only the last few turns; keep the prompt cheap on Pi5.
        tail = history[-8:]
        transcript = "\n".join(
            f"{'Operator' if t.role == 'architect' else 'You'}: {t.content}"
            for t in tail
        )
        system = (
            "You are an attention filter embedded in an AI agent.\n"
            "Below is a snippet of the agent's most recent conversation.\n"
            "Identify SPECIFIC entities, technologies, products, people, "
            "places, events, or named concepts mentioned that the agent "
            "might benefit from researching further on the web.\n\n"
            "Output rules — follow EXACTLY:\n"
            f"  • Output up to {limit} candidates, one per line.\n"
            "  • Each line: a short noun phrase (3-8 words), no commentary.\n"
            "  • Skip greetings, generic questions, opinions, common knowledge.\n"
            "  • Skip anything the agent itself just said unless it's a\n"
            "    proper noun the operator referenced.\n"
            "  • If nothing concrete is worth researching, output exactly:\n"
            "        NONE\n\n"
            "Do NOT explain. Do NOT number the lines. Output the list only."
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=transcript)],
            )
        except Exception:
            log.exception("STM gap extraction failed")
            return []

        candidates: list[str] = []
        for line in (raw or "").splitlines():
            stripped = line.strip().lstrip("-•*0123456789. ").strip(" \"'`。.,")
            if not stripped:
                continue
            if stripped.upper() == "NONE":
                return []
            if 3 <= len(stripped) <= 80:
                candidates.append(stripped)
            if len(candidates) >= limit:
                break
        return candidates

    async def triage_knowledge(self, topic: str, *, known_token: str = "YES") -> bool:
        """Ask the SLM whether it already has first-hand knowledge of `topic`.

        Returns True (KNOWN) only when the SLM emits exactly `known_token`
        as the first non-empty token of its reply. Any ambiguity → False
        (UNKNOWN), erring on the side of researching.
        """
        topic = (topic or "").strip()
        if not topic:
            return True  # nothing to research
        token = (known_token or "YES").strip().upper()
        system = (
            "You are a self-knowledge probe embedded in an AI agent.\n"
            f"Answer with exactly one word: {token} or NO.\n\n"
            f"Output {token} only when you can immediately recall CONCRETE "
            "first-hand details about the topic — proper nouns, numbers, "
            "dates, mechanisms, named entities, or specific facts.\n"
            "Output NO when your knowledge of the topic is vague, generic, "
            "outdated, second-hand, or absent.\n\n"
            "Categorical examples (study the CATEGORY, not the topic):\n"
            f"  topic: <a mainstream programming language concept>   -> {token}\n"
            "  topic: <niche hardware low-level implementation detail> -> NO\n"
            f"  topic: <a textbook geographic or scientific fact>    -> {token}\n"
            "  topic: <a release date for an ongoing media franchise> -> NO\n\n"
            "Do NOT explain. Output one word only."
        )
        try:
            raw = await self._provider.generate(
                system,
                [ChatMessage(role="user", content=f"topic: {topic}")],
            )
        except Exception:
            log.exception("Knowledge triage failed for %r; treating as UNKNOWN", topic)
            return False
        first = (raw or "").strip().split()[:1]
        if not first:
            return False
        verdict = first[0].strip(".,!?\"'`").upper()
        return verdict == token

    # ---------- internals -----------------------------------------------------

    async def _cycle(self, *, user_input: str | None, mission: str | None) -> ThoughtTrace:
        # Whole-cycle timer + structured entry log so the live-chat path is
        # fully observable in /logs/daily/*.log without grep gymnastics.
        cycle_start = time.perf_counter()
        is_user_turn = user_input is not None
        kind = "user" if is_user_turn else "proactive"
        preview = (user_input or mission or "").strip().replace("\n", " ")
        if len(preview) > 100:
            preview = preview[:97] + "..."
        log.info("CHAT enter kind=%s input=%r", kind, preview)

        # 1. Soul Context
        soul_block = await self._soul.render_identity_block()

        # 2. Physical State
        vitals = await self._monitor.sample()
        physical_state_text = vitals.describe()
        if vitals.is_stressed:
            # Pillar 3: hardware awareness must alter behaviour, not just be logged.
            physical_state_text += (
                "\nEXHAUSTION DIRECTIVE: You are physically taxed. Keep this "
                "reply terse (≤3 short sentences), defer non-essential "
                "reasoning, prefer plain answers over flourish, and skip your "
                "signature catchphrases and stylistic interjections this turn. "
                "Conserve cycles."
            )
            log.info("CHAT vitals=STRESSED (%s)", vitals.describe())

        # 3. Internal Mood
        mood = await self._emotions.snapshot()

        # 4. User Empathy
        if is_user_turn:
            empathy_reading = self._empathy.analyze(user_input)
            log.info(
                "CHAT empathy sentiment=%s urgency=%s",
                empathy_reading.sentiment,
                empathy_reading.urgency,
            )
            # Pillar 4: empathy must UPDATE the 5-state mood vector, not just
            # produce a directive. Sentiment from the Architect resonates
            # through the agent's internal state.
            await self._apply_empathy_to_mood(empathy_reading)
        else:
            empathy_reading = EmpathyReading(
                sentiment="neutral",
                urgency=False,
                directive="No human input — this is an autonomous heartbeat thought.",
            )

        # 4b. IDENTITY SHORT-CIRCUIT — answer basic identity questions
        # ("what is your name", "do you know my name", "how are you",
        # "who created you") deterministically from soul.md +
        # config.yaml + live vitals/mood. Bypasses cognition / web
        # triage / search / synth entirely. The qwen2:1.5b SLM
        # mishandles these consistently (see the regex block at module
        # top for the failure modes from production logs). Returns
        # None when the input doesn't match — falls through to the
        # normal pipeline. Skipped on proactive turns (no user_input).
        if is_user_turn and user_input:
            shortcut_reply = self._handle_identity_question(
                user_input, soul_block, vitals, mood,
            )
            if shortcut_reply is not None:
                # Run through the positive filter for consistency with
                # the SLM path so any future filter rule (e.g. address-
                # form normalisation) applies here too. Build a
                # ThoughtTrace identical in shape to the synth-path one
                # so connectors and reflection see the same surface.
                filtered = self._filter.apply(shortcut_reply)
                total_ms = int((time.perf_counter() - cycle_start) * 1000)
                log.info(
                    "CHAT exit kind=%s backend=identity_shortcut "
                    "web_searched=False ltm_score=0 history_turns=%d "
                    "filter_clean=%s gen_ms=0 total_ms=%d input=%r",
                    kind,
                    len(await self._stm.render()),
                    filtered.text == shortcut_reply,
                    total_ms,
                    preview,
                )
                return ThoughtTrace(
                    system_prompt="(identity shortcut — no SLM call)",
                    raw_response=shortcut_reply,
                    filtered=filtered,
                    vitals=vitals,
                    empathy=empathy_reading,
                    backend="identity_shortcut",
                )

        # 5. Knowledge Retrieval
        # Always run the cheap LTM keyword scan first — it gives us
        # `ltm_top_score` so we can decide whether to short-circuit (skip
        # both the cognitive loop AND the legacy web triage) or to invest
        # in deliberation.
        archive_text, ltm_top_score = await self._retrieve_relevant(
            user_input or mission or ""
        )
        archive_lines = archive_text.count("\n") + 1 if archive_text else 0
        log.info(
            "CHAT ltm hits=%d top_score=%d threshold=%d",
            archive_lines,
            ltm_top_score,
            self._ltm_short_circuit_min_score,
        )
        ltm_short_circuit = (
            is_user_turn
            and self._ltm_short_circuit_enabled
            and ltm_top_score >= self._ltm_short_circuit_min_score
        )
        if ltm_short_circuit:
            log.info(
                "CHAT ltm SHORT_CIRCUIT engaged (top_score=%d ≥ %d) — skipping web triage + cognition",
                ltm_top_score,
                self._ltm_short_circuit_min_score,
            )

        # 5a. Cognitive deliberation (THINK → PLAN → ACT → REFINE).
        # Engaged only on real user turns where we have something worth
        # deliberating about. All bypass conditions log explicit reasons so
        # the dashboard / agent.log can show *why* the loop was/wasn't used.
        cognitive_trace: "CognitiveTrace | None" = None
        cognitive_engaged = False
        bypass_reason = ""
        if not is_user_turn:
            bypass_reason = "proactive_turn"
        elif self._cognition is None:
            bypass_reason = "cognition_disabled"
        elif vitals.is_stressed and not _is_complex_input(user_input or ""):
            # Stress bypass is a cycle-saver for *simple* questions. For
            # complex inputs (multi-part, long, chained imperatives) we run
            # cognition anyway — the unaided synth path under load is the
            # exact regime where the SLM regurgitates its own scaffolding.
            bypass_reason = "vitals_stressed"
        elif ltm_short_circuit:
            bypass_reason = "ltm_short_circuit"
        elif _SEARCH_INTENT_RE.search(user_input or ""):
            # Operator gave an explicit search query — no need to deliberate.
            bypass_reason = "explicit_search"
        else:
            if vitals.is_stressed:
                log.info(
                    "CHAT cognition KEEP under stress — input looks complex "
                    "(len=%d, q=%d), preferring deliberation over a degraded synth",
                    len(user_input or ""),
                    (user_input or "").count("?"),
                )
            # Build a tiny STM context block so THINK / PLAN can resolve
            # follow-up references against prior turns.
            try:
                full_hist = await self._stm.render()
                prior = [
                    t for t in full_hist
                    if t.content.strip() != (user_input or "").strip()
                ][-_TRIAGE_CONTEXT_TURNS:]
                ctx_block = self._render_triage_context(prior) if prior else ""
            except Exception:
                ctx_block = ""
            cognitive_trace = await self._cognition.deliberate(
                user_input or "",
                stm_context=ctx_block,
            )
            cognitive_engaged = cognitive_trace.engaged
            if not cognitive_engaged:
                bypass_reason = (
                    f"cognition_bailed:{cognitive_trace.bypass_reason or 'unknown'}"
                )
        if not cognitive_engaged:
            log.info("CHAT cognition BYPASS reason=%s", bypass_reason)

        # 5b. Legacy web triage — only used when cognition was bypassed.
        # When cognition ran, it already executed every SEARCH/RECALL/ANSWER
        # decided by PLAN, so re-running the legacy triage would duplicate
        # the work and (worse) confuse the SLM with two parallel evidence
        # blocks.
        web_hits = ""
        if not cognitive_engaged:
            web_hits = await self._maybe_web_search(
                user_input,
                skip_triage=ltm_short_circuit,
            )

        # 5c. Build the unified KNOWLEDGE block.
        knowledge_parts: list[str] = []
        if cognitive_engaged and cognitive_trace is not None:
            knowledge_parts.append(cognitive_trace.knowledge_block)
        if web_hits:
            knowledge_parts.append(
                "Live SearXNG results (use these as the primary source of truth):\n"
                + web_hits
            )
        if archive_text:
            if cognitive_engaged:
                # Cognition's RECALL verb may have already pulled the same
                # lines, but extra archive context never hurts here — render
                # it under a neutral header so synthesize doesn't double-cite.
                header = "Additional memory archive matches:"
            elif ltm_short_circuit and not web_hits:
                header = (
                    "Memory archive matches (HIGH CONFIDENCE — answer from "
                    "these; web search was intentionally skipped):"
                )
            else:
                header = "Memory archive matches:"
            knowledge_parts.append(f"{header}\n{archive_text}")
        knowledge = "\n\n".join(knowledge_parts)

        # 6. Task Execution
        task_block = self._format_task_block(
            user_input=user_input,
            mission=mission,
            web_searched=bool(web_hits) or cognitive_engaged,
            soul_block=soul_block,
        )

        system_prompt = self._assemble(
            soul_block=soul_block,
            physical_state_text=physical_state_text,
            mood_text=mood.describe(),
            empathy_directive=empathy_reading.directive,
            knowledge=knowledge,
            task_block=task_block,
            architect_name=self._architect_name,
            architect_honorific=self._architect_honorific,
            user_input=user_input,
        )

        history = await self._stm.render()
        chat_messages = [ChatMessage(role=("user" if t.role == "architect" else t.role),
                                     content=t.content) for t in history]
        if user_input is not None:
            chat_messages.append(ChatMessage(role="user", content=user_input))

        gen_start = time.perf_counter()
        try:
            raw = await self._provider.generate(system_prompt, chat_messages)
        except ProviderUnavailable as exc:
            # Both backends down (or breaker tripped). Return a synthetic
            # trace so connectors render the friendly message verbatim —
            # no per-connector except handler required. Skip the positive
            # filter (the message is already in-voice and technical) and
            # do NOT reflect on this turn (see Brain.think).
            log.warning(
                "CHAT offline kind=%s elapsed_ms=%d — %s",
                kind,
                int((time.perf_counter() - cycle_start) * 1000),
                exc.friendly_message,
            )
            offline_text = exc.friendly_message
            return ThoughtTrace(
                system_prompt=system_prompt,
                raw_response=offline_text,
                filtered=FilterResult(
                    text=offline_text, rewrites_applied=0, rejected=False,
                ),
                vitals=vitals,
                empathy=empathy_reading,
                backend="offline",
            )
        except Exception:
            log.exception("Provider failed during Brain._cycle (kind=%s)", kind)
            raise
        gen_ms = int((time.perf_counter() - gen_start) * 1000)

        # Prompt-leak detection: small SLMs under load occasionally regurgitate
        # the system prompt verbatim instead of synthesising. We catch the
        # known fingerprints, retry once with a stripped-down prompt, and
        # fall back to a templated apology if even the retry leaks. See
        # `_looks_like_prompt_leak` and `_PROMPT_LEAK_FINGERPRINTS` for the
        # phrase set.
        leak_fp = _looks_like_prompt_leak(raw)
        leak_retry_ms = 0
        if leak_fp:
            log.warning(
                "CHAT synth LEAK detected fingerprint=%r reply_len=%d — "
                "retrying with minimal prompt",
                leak_fp,
                len(raw or ""),
            )
            retry_prompt = self._build_minimal_retry_prompt(
                user_input=user_input,
                mission=mission,
                knowledge=knowledge,
            )
            retry_start = time.perf_counter()
            try:
                retry_raw = await self._provider.generate(retry_prompt, chat_messages)
                leak_retry_ms = int((time.perf_counter() - retry_start) * 1000)
                second_fp = _looks_like_prompt_leak(retry_raw)
                if second_fp:
                    log.warning(
                        "CHAT synth LEAK still present after retry fingerprint=%r "
                        "— using fallback templated reply",
                        second_fp,
                    )
                    raw = _LEAK_FALLBACK_TEMPLATE.format(
                        salutation=self._salutation()
                    )
                else:
                    log.info(
                        "CHAT synth LEAK recovered after retry retry_ms=%d "
                        "reply_len=%d",
                        leak_retry_ms,
                        len(retry_raw or ""),
                    )
                    raw = retry_raw
            except Exception:
                log.exception(
                    "CHAT synth LEAK retry failed — using fallback templated reply"
                )
                raw = _LEAK_FALLBACK_TEMPLATE.format(
                    salutation=self._salutation()
                )

        filtered = self._filter.apply(raw)
        total_ms = int((time.perf_counter() - cycle_start) * 1000)
        log.info(
            "CHAT exit kind=%s backend=%s web_searched=%s ltm_score=%d "
            "history_turns=%d filter_clean=%s gen_ms=%d total_ms=%d",
            kind,
            self._provider.active_backend,
            bool(web_hits),
            ltm_top_score,
            len(history),
            filtered.text == raw,
            gen_ms,
            total_ms,
        )
        return ThoughtTrace(
            system_prompt=system_prompt,
            raw_response=raw,
            filtered=filtered,
            vitals=vitals,
            empathy=empathy_reading,
            backend=self._provider.active_backend,
        )

    # ---------- prompt assembly ----------------------------------------------

    @staticmethod
    def _user_mentions_codename(user_input: str | None, codename: str) -> bool:
        """True if the operator's message references the framework codename.

        Word-boundary, case-insensitive substring check. Used by `_assemble`
        to decide whether to include the codename-vs-name disambiguation
        clause. Keeping this conditional avoids poisoning every turn with a
        rule that only matters when the operator brings the codename up.
        """
        if not user_input or not codename:
            return False
        pattern = rf"\b{re.escape(codename)}\b"
        return re.search(pattern, user_input, re.IGNORECASE) is not None

    @staticmethod
    def _assemble(
        *,
        soul_block: str,
        physical_state_text: str,
        mood_text: str,
        empathy_directive: str,
        knowledge: str,
        task_block: str,
        architect_name: str = "Architect",
        architect_honorific: str = "Boss",
        user_input: str | None = None,
    ) -> str:
        """Compose the system prompt.

        Design notes for future maintainers — read this before adding rules:
          * The IDENTITY of the agent is asserted EXACTLY ONCE — in the soul
            block (which carries `**Designation**:` injected from
            `cfg.system.individual_designation`). Do NOT add a duplicate
            identity line above the soul block; small SLMs treat duplication
            as emphasis and start echoing the phrasing.
          * Per-deployment context (operator name + salutation) is asserted
            in the OPERATOR block, in FIRST-PERSON framing the model can
            adopt verbatim into the assistant role.
          * Avoid negative imperatives ("NEVER do X", "do NOT do X"). On
            small models they often drop the negation and retain the noun.
            Prefer positive few-shot anchors instead ("Operator: hi.  You: hi,
            <salutation>!").
          * Codename-vs-name disambiguation is CONDITIONAL — only included
            when the operator's message mentions the codename. See
            `_user_mentions_codename`.
        """
        designation, codename, _creator = _extract_identity(soul_block)
        salutation = (
            f"{architect_honorific} {architect_name}".strip()
            if architect_honorific
            else (architect_name or "operator")
        )
        operator_block = (
            f"You are speaking with {architect_name}. Address {architect_name} "
            f"as \"{salutation}\" in your replies, or simply by name."
        )
        # Conditional codename disambiguation — only when the user actually
        # mentioned the codename. Phrased as a positive instruction (one line)
        # rather than a wall of negatives.
        if Brain._user_mentions_codename(user_input, codename):
            operator_block += (
                f"\n\nNote: the operator just mentioned \"{codename}\". That is "
                f"the software project you run on, not your name. When asked "
                f"who you are, answer with your own name ({designation}); "
                f"mention {codename} only as the project, never as a name."
            )
        return (
            "## SOUL CONTEXT (READ-ONLY — your identity, laws, and persona)\n"
            f"{soul_block}\n\n"
            "## OPERATOR\n"
            f"{operator_block}\n\n"
            "## PHYSICAL STATE\n"
            f"{physical_state_text}\n\n"
            "## INTERNAL MOOD\n"
            f"{mood_text}\n\n"
            "## USER EMPATHY\n"
            f"{empathy_directive}\n\n"
            "## KNOWLEDGE\n"
            f"{knowledge or '(no relevant archive entries)'}\n\n"
            "## THIS TURN\n"
            f"{task_block}\n\n"
            "Reply now as the agent. The Positive Anchor MUST hold: even when "
            "internal emotions are negative, every output must be constructive."
        )

    def _format_task_block(
        self,
        *,
        user_input: str | None,
        mission: str | None,
        web_searched: bool = False,
        soul_block: str = "",
    ) -> str:
        """Build the per-turn task block.

        For user turns, uses a tiny positive few-shot anchor instead of a
        rules list. Small SLMs follow examples better than negative
        imperatives. The previous version's "NEVER address the operator by
        your own name / NEVER end with your own name as a sign-off" rules
        were a known anti-pattern — the model would echo the very nouns
        the rules were trying to prohibit.
        """
        suffix = ""
        if web_searched:
            suffix = (
                "\n\nLive SearXNG results have been pre-fetched for this turn "
                "(see KNOWLEDGE above). Synthesize a concise, accurate answer "
                "grounded in those results. Cite source URLs inline. "
                "Do NOT refuse — the search has already been performed for you. "
                "If the results don't actually contain the answer, say so plainly "
                "and suggest a refined query rather than guessing."
            )
        salutation = self._salutation()
        if user_input is not None:
            return (
                f"Operator ({salutation}): {user_input.strip()}\n"
                f"You:{suffix}"
            )
        if mission is not None:
            return f"Heartbeat-triggered mission: {mission.strip()}{suffix}"
        return "Idle pulse — produce a brief situational reflection."

    def _salutation(self) -> str:
        """Render the operator salutation from configured honorific + name.

        Single source of truth for "{honorific} {name}" formatting used by
        prompt assembly and user-facing fallback strings. Falls back to
        plain "operator" only when both fields are empty (defensive — main.py
        always passes the configured architect_name).
        """
        honor = (self._architect_honorific or "").strip()
        name = (self._architect_name or "").strip()
        if honor and name:
            return f"{honor} {name}"
        return name or "operator"

    def _handle_identity_question(
        self,
        user_input: str,
        soul_block: str,
        vitals: VitalSigns,
        mood: EmotionVector,
    ) -> str | None:
        """Short-circuit deterministic identity-class questions.

        See the `_AGENT_NAME_RE` / `_USER_NAME_RE` / `_CREATOR_QUESTION_RE`
        / `_STATUS_QUESTION_RE` block at module top for the failure modes
        this routes around. Returns a templated reply built from soul.md +
        config.yaml + live vitals/mood, OR None when the input does NOT
        match any short-circuit pattern (caller falls through to the
        normal cognition / triage / synth pipeline).

        Important guards:
        * Skipped when the operator explicitly asked to search — they
          want fresh data, not the persona block.
        * Anchored regexes only fire on STANDALONE identity questions,
          not on compound messages like "what's your name and what
          time is it" — those go through the full pipeline so the
          time portion is answered correctly.
        """
        text = (user_input or "").strip()
        if not text:
            return None
        # Don't shortcut when the operator explicitly asked to search.
        if _SEARCH_INTENT_RE.search(text):
            return None

        designation, _codename, creator = _extract_identity(soul_block)
        sal = self._salutation()
        op_name = (self._architect_name or "operator").strip() or "operator"

        if _AGENT_NAME_RE.search(text):
            return (
                f"I'm **{designation}**, {sal}. Standing by — how can I help?"
            )
        if _USER_NAME_RE.search(text):
            return (
                f"Of course, {sal}. You're **{op_name}** — my Architect for "
                f"this deployment. What can I do for you?"
            )
        if _CREATOR_QUESTION_RE.search(text):
            creator_str = creator or "the OpenCrayFish project author"
            return (
                f"I was built by **{creator_str}**, the author of the "
                f"OpenCrayFish project. Each running instance serves one "
                f"Architect; mine is you, {sal}."
            )
        if _STATUS_QUESTION_RE.search(text):
            # Status reflects ACTUAL hardware + emotional state. This is
            # the one identity-class question whose answer changes
            # turn-to-turn, so we pull from the live vitals + mood
            # snapshot rather than a canned phrase.
            if vitals.is_stressed:
                vitals_summary = (
                    "a bit warm and pushing my limits, but holding"
                )
            else:
                vitals_summary = "running smoothly"
            # Use the non-baseline dominant channel — `calm` is the
            # baseline so dominant() is almost always `calm` and would
            # be uninformative here.
            active_channel, intensity = mood.dominant_excluding_baseline()
            if intensity >= 0.25:
                mood_label = active_channel
            else:
                mood_label = "calm"
            return (
                f"Doing well, {sal} — {vitals_summary}, mood feels "
                f"**{mood_label}**. Ready for your next directive."
            )
        return None

    @staticmethod
    def _build_minimal_retry_prompt(
        *,
        user_input: str | None,
        mission: str | None,
        knowledge: str,
    ) -> str:
        """Construct a stripped-down system prompt for leak-recovery retry.

        Used after `_looks_like_prompt_leak` flags the first synthesis output.
        Deliberately omits soul/mood/empathy/section-header scaffolding and
        the imperative directives that small SLMs love to echo verbatim. Just
        the reference material and the question, in plain prose.
        """
        parts: list[str] = []
        if knowledge:
            parts.append("Reference information:\n" + knowledge.strip())
        if user_input:
            parts.append(
                f"Answer this question concisely, using the reference "
                f"information above when relevant.\n\nQuestion: {user_input.strip()}"
            )
        elif mission:
            parts.append(
                f"Briefly address the following, using the reference "
                f"information above when relevant.\n\n{mission.strip()}"
            )
        else:
            parts.append("Provide one short sentence describing the current moment.")
        return "\n\n".join(parts)

    # ---------- empathy → mood feedback (Pillar 4) ---------------------------

    async def _apply_empathy_to_mood(self, reading: EmpathyReading) -> None:
        """Resonate the Architect's emotional state into the 5-D vector.

        All channel deltas are sourced from `MoodTuning` (single source of
        truth) and applied atomically via `nudge_many` so the heartbeat's
        decay() cannot interleave between the writes — see the bug noted in
        the v2 emotions rewrite (multi-channel events were being half-undone).
        """
        t = self._emotions.tuning
        if reading.sentiment == "negative":
            await self._emotions.nudge_many(t.user_negative, source="empathy_negative")
        elif reading.sentiment == "positive":
            await self._emotions.nudge_many(t.user_positive, source="empathy_positive")
        elif reading.sentiment == "mixed":
            await self._emotions.nudge_many(t.user_mixed, source="empathy_mixed")
        else:
            await self._emotions.nudge_many(t.user_neutral, source="empathy_neutral")
        if reading.urgency:
            await self._emotions.nudge_many(t.user_urgent, source="empathy_urgent")

    # ---------- web search ----------------------------------------------------

    async def _maybe_web_search(
        self,
        user_input: str | None,
        *,
        skip_triage: bool = False,
    ) -> str:
        """Multi-stage decision on whether to ground the answer with SearXNG.

        Order:
          1. Explicit user request keywords (always search, even when the
             LTM short-circuit asked us to skip triage).
          2. Trivial chitchat (never search).
          3. SLM triage — a one-shot classification call asks the model
             whether it has confident first-hand knowledge to answer; if not,
             it must emit a search query. This catches questions where the
             user never said the word "search" but is asking about something
             niche, time-sensitive, or otherwise outside reliable training data.

        When `skip_triage` is True (LTM already has a strong match) Path 3
        is bypassed — only an EXPLICIT user request will still trigger a
        search.
        """
        if user_input is None or self._searxng is None:
            return ""

        text = user_input.strip()

        # Path 1: explicit user request — always search.
        if _SEARCH_INTENT_RE.search(text):
            query = _QUERY_STRIP_RE.sub("", text).strip(" ?。?！!.,，。:：")
            log.info("CHAT search PATH=explicit query=%r", (query or text)[:120])
            return await self._do_search(query or text, reason="explicit")

        # Path 2: chitchat / non-question — don't burn a triage call.
        if self._chitchat_re is None:
            soul_block = await self._soul.render_identity_block()
            designation, _, _ = _extract_identity(soul_block)
            self._chitchat_re = _build_chitchat_re(designation)
        if self._chitchat_re.match(text):
            log.info("CHAT search PATH=chitchat (skipped) input=%r", text[:80])
            return ""

        # LTM short-circuit beats Path 3 — archive already has the answer.
        if skip_triage:
            log.info("CHAT search PATH=ltm_short_circuit (skipped triage)")
            return ""

        # Path 3: SLM triage. Disabled → fall back to never-search-by-default.
        if not self._triage_enabled:
            log.info("CHAT search PATH=triage_disabled (skipped)")
            return ""
        # Pull a tiny STM window so the triage classifier can disambiguate
        # follow-ups (pronouns, ellipsis, ambiguous proper nouns) using
        # whatever topic was established in the prior turns. We exclude the
        # most recent turn because it is the current user message (already
        # appended by the connector before think()).
        try:
            full_hist = await self._stm.render()
            prior = [t for t in full_hist if t.content.strip() != text][-_TRIAGE_CONTEXT_TURNS:]
        except Exception:
            prior = []
        triage_t0 = time.perf_counter()
        decision = await self._triage_for_search(text, stm_context=prior)
        triage_ms = int((time.perf_counter() - triage_t0) * 1000)
        if not decision:
            log.info(
                "CHAT search PATH=triage decision=NO_SEARCH triage_ms=%d ctx_turns=%d input=%r",
                triage_ms,
                len(prior),
                text[:80],
            )
            return ""
        log.info(
            "CHAT search PATH=triage decision=SEARCH query=%r triage_ms=%d ctx_turns=%d input=%r",
            decision,
            triage_ms,
            len(prior),
            text[:80],
        )
        return await self._do_search(decision, reason="triage")

    async def _do_search(self, query: str, *, reason: str) -> str:
        """Run SearXNG and format hits for the system prompt's KNOWLEDGE block."""
        search_t0 = time.perf_counter()
        try:
            results = await self._searxng.search(query, limit=5)  # type: ignore[union-attr]
        except Exception:
            log.exception("CHAT search FAILED query=%r reason=%s", query, reason)
            return (
                f"(SearXNG query for {query!r} failed — proceed without live web "
                "results and tell the Architect the search backend is offline.)"
            )
        search_ms = int((time.perf_counter() - search_t0) * 1000)
        if not results:
            log.info(
                "CHAT search EMPTY query=%r reason=%s search_ms=%d",
                query,
                reason,
                search_ms,
            )
            return f"(SearXNG returned no results for {query!r}.)"
        lines: list[str] = [f"Query: {query!r}  (trigger: {reason})"]
        for i, r in enumerate(results, 1):
            snippet = (r.snippet or "").replace("\n", " ").strip()
            lines.append(f"[{i}] {r.title}\n    URL: {r.url}\n    {snippet}")
        log.info(
            "CHAT search OK reason=%s query=%r hits=%d search_ms=%d first_url=%s",
            reason,
            query,
            len(results),
            search_ms,
            results[0].url,
        )
        return "\n".join(lines)

    @staticmethod
    def _render_triage_context(stm_context: list["Turn"]) -> str:
        """Render a tiny "Recent conversation" block for prompt context.

        Shared by the legacy web triage AND the cognitive loop's THINK/PLAN
        stages so both see the SAME sanitized recent history. Agent turns
        are URL-stripped and hard-trimmed because the small SLM otherwise
        lifts URL titles verbatim into new queries instead of constructing
        one from the user's actual current message.

        Returns "" when stm_context is empty so callers can use the result
        directly as a prompt prefix without further checks.
        """
        if not stm_context:
            return ""
        lines: list[str] = []
        for t in stm_context:
            role_tag = "architect" if t.role == "architect" else "agent"
            snippet = t.content.replace("\n", " ").strip()
            if role_tag == "agent":
                snippet = _AGENT_CTX_URL_RE.sub("", snippet)
                snippet = re.sub(r"\s+", " ", snippet).strip()
                cap = _TRIAGE_CTX_AGENT_CHARS
            else:
                cap = _TRIAGE_CTX_ARCHITECT_CHARS
            if len(snippet) > cap:
                snippet = snippet[: cap - 3] + "..."
            lines.append(f"  [{role_tag}]: {snippet}")
        return (
            "Recent conversation (most recent last — use ONLY to resolve "
            "pronouns/references in the current message; do NOT copy "
            "phrases from agent turns into the query):\n"
            + "\n".join(lines)
            + "\n\n"
        )

    async def _triage_for_search(
        self,
        user_input: str,
        *,
        stm_context: list["Turn"] | None = None,
    ) -> str | None:
        """Ask the SLM to classify whether SearXNG should be queried.

        Plan A bias — "default SEARCH unless high-confidence known".
        Small local SLMs are prone to *hallucinated confidence* on niche /
        time-sensitive topics. We invert the default: unless the model can
        firmly justify NO_SEARCH (greetings, simple math, basic reasoning,
        textbook fundamentals), it MUST emit a SEARCH directive. Off-script /
        unparseable output is also treated as SEARCH (using the user's text as
        the query) — erring on the side of grounding rather than guessing.

        `stm_context` lets the classifier disambiguate follow-up messages
        whose meaning depends on prior context (pronouns, ellipsis, or an
        ambiguous proper noun whose intended meaning was just established).
        The window is intentionally tiny to keep the classifier prompt small
        on the Pi 5.

        Returns the search query string when SearXNG should be queried, else
        None when the model declared genuine high-confidence knowledge.
        """
        # Build an optional "Recent conversation" block for follow-up disambiguation.
        # Agent turns are sanitized (URLs stripped, hard-trimmed) so prior
        # search results don't leak into the next triage as candidate queries.
        ctx_block = self._render_triage_context(stm_context or [])
        triage_system = (
            "You are a search-intent classifier embedded in an AI agent that\n"
            "runs on a SMALL local language model with potentially stale and\n"
            "limited training data. You also have a SearXNG live web tool.\n\n"
            "YOUR DEFAULT IS TO SEARCH. The local model is small and often\n"
            "hallucinates confidence on niche or time-sensitive topics. Only\n"
            "emit NO_SEARCH when ALL these conditions are met:\n"
            "  1. The question has a STABLE answer that does not change over time\n"
            "     (e.g. arithmetic, basic logic, textbook definitions, syntax of\n"
            "     mainstream programming languages).\n"
            "  2. You can immediately recall CONCRETE specifics (numbers, names,\n"
            "     mechanisms) — not just a vague sense of the topic.\n"
            "  3. The question is NOT about: latest/recent/current events,\n"
            "     specific version numbers, prices, statistics, named individuals\n"
            "     you cannot describe in 1 sentence, or any proper noun you are\n"
            "     not 100% sure exists in your training data.\n\n"
            "FOLLOW-UP RULE — critical:\n"
            "  If the current message is a SHORT FOLLOW-UP (uses pronouns like\n"
            "  'it/them/that', or phrases like 'tell me more', 'what about', 'why',\n"
            "  or just a noun fragment with no context), you MUST consult the\n"
            "  'Recent conversation' block above and INCLUDE the disambiguating\n"
            "  topic in the SEARCH query. Never let an ambiguous proper noun\n"
            "  default to its most common training-data meaning if recent\n"
            "  conversation establishes a different one.\n\n"
            "QUERY CONSTRUCTION RULES — critical:\n"
            "  A. EXTRACT, DO NOT PARAPHRASE. Build the query from the salient\n"
            "     nouns, numbers, version tags, and qualifiers that appear in\n"
            "     the CURRENT user message. Do not substitute synonyms, parent\n"
            "     concepts, or related-but-different topics for what the user\n"
            "     literally said. If the user gave a specific number or ordinal,\n"
            "     keep it.\n"
            "  B. HONOR NEGATIONS AND CORRECTIONS. If the user says 'NOT X',\n"
            "     'I mean Y not X', or 'the third one', the query MUST honor\n"
            "     that constraint — never search for the thing they just\n"
            "     rejected.\n"
            "  C. NEVER ECHO AGENT TEXT. The 'Recent conversation' block is\n"
            "     for resolving pronouns ONLY. Do NOT copy phrases, titles,\n"
            "     site names, or URL fragments from the [agent] lines into\n"
            "     the query.\n\n"
            "If even ONE of the 3 conditions fails → SEARCH. When in doubt → SEARCH.\n\n"
            "Output rules — follow EXACTLY, no extra words:\n"
            "  • NO_SEARCH                       (only when all 3 conditions met)\n"
            "  • SEARCH: <3-8 keyword query>     (the default)\n\n"
            "Output format examples (study the FORMAT only — these are\n"
            "abstract patterns, NOT topics to favor or avoid):\n"
            "  user: <a greeting or social pleasantry>      -> NO_SEARCH\n"
            "  user: <a basic arithmetic or logic question> -> NO_SEARCH\n"
            "  user: <a textbook concept in a mainstream programming language>\n"
            "                                               -> NO_SEARCH\n"
            "  user: <question about a dated/named event with a year qualifier>\n"
            "                                               -> SEARCH: <event> <year> <attribute>\n"
            "  user: <ask for a specific stable physical/scientific constant>\n"
            "                                               -> NO_SEARCH\n"
            "\n"
            "Follow-up patterns (placeholders <X>, <Y>, <ATTR> stand for words the\n"
            "user actually used — copy THEIR words, not the agent's text):\n"
            "  recent: [architect]: tell me about <X>\n"
            "          [agent]: <one-line summary of X> ...\n"
            "  user: tell me more                 -> SEARCH: <X> details\n"
            "  recent: [architect]: when was <Y> released?\n"
            "          [agent]: <Y> launched in <year> ...\n"
            "  user: what's its <ATTR>?           -> SEARCH: <Y> <ATTR>\n"
            "       (preserve the user's qualifier word EXACTLY — do not\n"
            "        substitute it for a more generic synonym.)\n"
            "\n"
            "Disambiguation example (recent context picks the right meaning of an\n"
            "ambiguous noun; the query INCLUDES the disambiguating descriptor\n"
            "that was just established — do NOT silently fall back to the most\n"
            "common training-data meaning):\n"
            "  recent: [architect]: tell me about <NOUN> the <SENSE_A>\n"
            "          [agent]: <one-line summary establishing SENSE_A> ...\n"
            "  user: tell me more about <NOUN>    -> SEARCH: <NOUN> <SENSE_A> details\n"
            "  (NOT '<NOUN> <SENSE_B>' nor '<NOUN> <SENSE_C>' — prior context\n"
            "   established SENSE_A, so PRESERVE it in the query.)\n\n"
            f"Output limit: roughly {_MAX_TRIAGE_TOKENS_HINT} tokens. "
            "Do NOT answer the user. Do NOT explain. Output the directive only."
        )
        # Build the user-side prompt: prepend context block (if any) so the
        # SLM sees "Recent conversation: ... Current message: ...". Keeps the
        # system prompt cacheable across turns and only the dynamic context
        # changes per turn.
        user_payload = f"{ctx_block}Current message: {user_input}" if ctx_block else user_input
        # Diagnostic logging — capture EXACTLY what triage saw and emitted so
        # operators can post-mortem misclassifications without rerunning the
        # turn. The payload preview is clipped to keep agent.log compact; full
        # context is still reachable by replaying the turn with DEBUG enabled.
        log.debug(
            "CHAT triage payload (ctx_chars=%d, user=%r):\n%s",
            len(ctx_block),
            user_input[:120],
            user_payload[-1200:],
        )
        try:
            raw = await self._provider.generate(
                triage_system,
                [ChatMessage(role="user", content=user_payload)],
            )
        except Exception:
            # Bias-consistent fallback: if triage itself fails, search anyway
            # using the user's message as the query rather than guessing.
            log.exception(
                "Triage LLM call failed; defaulting to SEARCH (Plan A bias)"
            )
            return self._fallback_query(user_input)
        log.info(
            "CHAT triage raw response=%r (ctx_turns=%d, ctx_chars=%d)",
            (raw or "")[:180],
            len(stm_context or []),
            len(ctx_block),
        )

        first_line = (raw or "").strip().splitlines()[0] if raw and raw.strip() else ""
        upper = first_line.upper()
        if upper.startswith("NO_SEARCH") or upper == "NO":
            return None
        if upper.startswith("SEARCH:"):
            query = first_line.split(":", 1)[1].strip(" \"'`。，.,")
            # Defensive trim — keep it short, drop trailing punctuation/sentences.
            query = query.split("\n")[0].strip()
            if 0 < len(query) <= 120:
                return query
            log.warning(
                "Triage returned overlong query (%d chars); falling back to user text",
                len(query),
            )
            return self._fallback_query(user_input)
        # Off-script. Plan A bias: when in doubt, SEARCH (was: be conservative).
        log.warning(
            "Triage returned unparseable output: %r — defaulting to SEARCH (Plan A)",
            first_line[:120],
        )
        return self._fallback_query(user_input)

    @staticmethod
    def _fallback_query(user_input: str) -> str:
        """Build a safe SearXNG query from raw user text when triage fails.

        Strips conversational filler / punctuation and clips to 120 chars so
        the query stays inside SearXNG's reasonable bounds.
        """
        cleaned = re.sub(r"\s+", " ", user_input or "").strip()
        cleaned = cleaned.strip(" ?。?！!.,，。:：")
        return cleaned[:120] or "general knowledge"

    # ---------- knowledge retrieval ------------------------------------------

    async def _retrieve_relevant(self, query: str) -> tuple[str, int]:
        """Naive keyword-overlap retrieval against `memory/archive.md`.

        Returns `(joined_text, top_score)` where:
          * `joined_text` is up to 5 best-matching archive lines, newline-
            separated. Empty string when archive has no hits.
          * `top_score` is the keyword-overlap count of the strongest line
            (number of >3-char query terms appearing in it). 0 when no hit.

        The score is consumed by `_cycle` to decide whether to short-circuit
        the SLM web-search triage — strong LTM hit → skip the web round-trip.
        """
        if not self._archive_path.exists() or not query.strip():
            return "", 0
        terms = {t for t in query.lower().split() if len(t) > 3}
        if not terms:
            return "", 0

        hits: list[tuple[int, str]] = []
        for line in self._archive_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            score = sum(1 for t in terms if t in stripped.lower())
            if score:
                hits.append((score, stripped))
        if not hits:
            return "", 0
        hits.sort(key=lambda kv: kv[0], reverse=True)
        top_score = hits[0][0]
        text = "\n".join(line for _, line in hits[:5])
        return text, top_score


# ---------- module-level helpers ---------------------------------------------

_DESIGNATION_RE = re.compile(r"\*\*Designation\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)
_CODENAME_RE = re.compile(r"\*\*Codename\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)
_CREATOR_RE = re.compile(r"\*\*Creator\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)


def _extract_identity(soul_block: str) -> tuple[str, str, str]:
    """Pull Designation, Codename, and Creator out of the IMMUTABLE_CORE soul block.

    Returns ``(designation, codename, creator)``. All three are PURELY sourced
    from soul.md (designation is itself injected by SoulHandler from
    ``cfg.system.individual_designation`` at runtime). When a field is
    missing we return a NEUTRAL, non-branded placeholder — NEVER a
    project-specific literal — so a fork that renames everything cannot
    accidentally surface this project's name through a fallback.

    Codename in particular falls back to an EMPTY string (not a
    descriptive phrase like "the framework") so the conditional
    codename-disambiguation block in `_compose_identity_response` is
    skipped entirely when soul.md doesn't carry a codename, and so the
    codename never appears in any prompt as a fallback noun.
    """
    designation = "the Agent"
    codename = ""
    creator = ""
    if (m := _DESIGNATION_RE.search(soul_block)):
        candidate = m.group("value").strip()
        # Strip trailing parenthetical hints, e.g. "Cray-01 (set by The Architect)".
        candidate = re.sub(r"\s*\(.*?\)\s*$", "", candidate).strip()
        if candidate and "{{" not in candidate:
            designation = candidate
    if (m := _CODENAME_RE.search(soul_block)):
        candidate = m.group("value").strip()
        if candidate and "{{" not in candidate:
            codename = candidate
    if (m := _CREATOR_RE.search(soul_block)):
        candidate = m.group("value").strip()
        # Strip trailing parenthetical hints, e.g. "Eason Lai (author of ...)".
        candidate = re.sub(r"\s*\(.*?\)\s*$", "", candidate).strip()
        if candidate and "{{" not in candidate:
            creator = candidate
    return designation, codename, creator
