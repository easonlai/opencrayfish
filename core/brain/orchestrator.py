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

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..emotions import Emotions
from ..empathy import EmpathyEngine, EmpathyReading
from ..monitor import Monitor, VitalSigns
from ..positive_filter import FilterResult, PositiveFilter
from ..provider import ChatMessage, Provider, ProviderUnavailable
from ..skills import SkillContext, SkillRegistry
from ..soul_handler import SoulHandler
from ..stm import ShortTermMemory
from .identity_responder import IdentityResponder, extract_identity
from .prompt_assembly import (
    assemble_system_prompt,
    build_minimal_retry_prompt,
    format_task_block,
)
from .task_parsing import TaskIntentParser

if TYPE_CHECKING:
    from ..cognition import CognitiveLoop, CognitiveTrace
    from ..scheduler import Task, TaskAction, TaskSpec, TaskUpdate
    from ..stm import Turn

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

# Identity short-circuit machinery (regexes, soul-block parser, dispatcher)
# lives in ``core/brain/identity_responder.py``; ``Brain.__init__`` builds
# ``self._identity_responder`` and ``_cycle`` calls
# ``self._identity_responder.try_handle(...)`` BEFORE any cognition / triage /
# SLM call.

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
    # True when the turn's KNOWLEDGE block carries live web results or
    # Cognitive Loop output. Replaces the previous string-scraping
    # heuristic that searched `system_prompt` for the leak-prone phrase
    # "Live SearXNG results".
    web_searched: bool = False


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
        skill_registry: SkillRegistry,
        skill_ctx: SkillContext,
        architect_name: str = "Architect",
        architect_honorific: str = "Boss",
        web_search_triage_enabled: bool = True,
        web_search_skill: str = "research",
        ltm_short_circuit_enabled: bool = True,
        ltm_short_circuit_min_score: int = 2,
        reflection_enabled: bool = True,
        cognition: CognitiveLoop | None = None,
    ) -> None:
        self._soul = soul
        self._monitor = monitor
        self._emotions = emotions
        self._empathy = empathy
        self._filter = positive_filter
        self._provider = provider
        self._stm = stm
        # Skill dispatch — every web search / LTM lookup / reflection
        # goes through the registry. The capability is reached via the
        # registry ("research", "recall", "self_reflect"). Construction-
        # time gating: callers can pass `reflection_enabled=False` to
        # suppress the fire-and-forget self_reflect dispatch when
        # ReflectionEngine isn't wired.
        self._skill_registry = skill_registry
        self._skill_ctx = skill_ctx
        self._architect_name = (architect_name or "Architect").strip() or "Architect"
        self._architect_honorific = (architect_honorific or "").strip()
        # Identity short-circuit dispatcher. Handed the skill registry +
        # context once; the orchestrator only ever passes the live
        # vitals / mood / salutation per turn. See
        # ``core/brain/identity_responder.py``.
        self._identity_responder = IdentityResponder(
            skill_registry=self._skill_registry,
            skill_ctx=self._skill_ctx,
            architect_name=self._architect_name,
        )
        # Recurring-research task intent parser. Stateless; holds only
        # the SLM provider. See ``core/brain/task_parsing.py``.
        self._task_parser = TaskIntentParser(provider=self._provider)
        self._triage_enabled = web_search_triage_enabled
        # Configurable name of the registered Skill that satisfies
        # Brain's web-triage fallback. Defaults to the in-tree
        # "research" Skill; a third-party package can ship a
        # replacement (e.g. "perplexity_research") and the operator
        # points cfg.cognition.web_search_skill at it. Both call sites
        # (``_maybe_web_search`` gating + ``_do_search`` dispatch)
        # route through this name — no other hardcode.
        self._web_search_skill = (web_search_skill or "research").strip() or "research"
        self._ltm_short_circuit_enabled = bool(ltm_short_circuit_enabled)
        self._ltm_short_circuit_min_score = max(1, int(ltm_short_circuit_min_score))
        self._reflection_enabled = bool(reflection_enabled)
        self._cognition = cognition
        # Lazy-built once per Brain instance from the live soul.md Designation.
        # soul.md is read async, so we can't build it in __init__; the first
        # call to _maybe_web_search() populates it.
        self._chitchat_re: re.Pattern[str] | None = None
        # Strong references to background tasks. CPython's event loop only
        # holds weak refs to tasks created via asyncio.create_task; a task
        # not referenced elsewhere may be GC'd mid-flight ("Task was
        # destroyed but it is pending!"). We add tasks here, drop them on
        # completion, and drain remaining ones in aclose().
        self._inflight: set[asyncio.Task[Any]] = set()

        # Foreground-priority signal. Background subsystems (Heartbeat's
        # proactive research, TaskScheduler's recurring synthesis) read
        # `is_foreground_busy()` at each long-running milestone and yield
        # the NPU back to the Architect when this is non-zero. A depth
        # counter (instead of a bare flag) handles the legitimate case
        # where Architect input arrives concurrently on two connectors
        # (Telegram + Web Chat) and both have a `think()` cycle in flight
        # — yield stays asserted until ALL of them finish.
        self._foreground_depth: int = 0
        # asyncio is single-threaded so int inc/dec at non-await points
        # is atomic; no lock needed for the counter itself.

    # ---------- public entry points ------------------------------------------

    def is_foreground_busy(self) -> bool:
        """True when an Architect-initiated `think()` cycle is in flight.

        Read by background subsystems (Heartbeat._proactive_research,
        TaskScheduler._fire) at long-running milestones so they can
        cooperatively yield NPU bandwidth back to the live conversation
        path. Cheap (single int comparison), safe to poll.
        """
        return self._foreground_depth > 0

    async def think(self, user_input: str) -> ThoughtTrace:
        """Full PROMPT_ASSEMBLY cycle for an incoming user message."""
        self._foreground_depth += 1
        log.info(
            "FOREGROUND start depth=%d input_chars=%d",
            self._foreground_depth, len(user_input or ""),
        )
        t0 = time.perf_counter()
        try:
            trace = await self._cycle(user_input=user_input, mission=None)
        finally:
            self._foreground_depth -= 1
            log.info(
                "FOREGROUND end depth=%d dur_ms=%.1f",
                self._foreground_depth,
                (time.perf_counter() - t0) * 1000.0,
            )
        # Self-reflection runs in the background — it must NOT delay the reply
        # the connector is about to send. Skip when the provider is offline:
        # there's no real reply to learn from and reflection itself would
        # just hit the same dead endpoint. Dispatched through the Skill
        # registry ("self_reflect") so the audit trail and timing land in
        # state/skills.jsonl alongside every other skill invocation.
        if self._reflection_enabled and trace.backend != "offline":
            task = asyncio.create_task(
                self._skill_registry.invoke(
                    "self_reflect",
                    self._skill_ctx,
                    kind="user",
                    input_text=user_input,
                    response=trace.filtered.text,
                    web_searched=trace.web_searched,
                    backend=trace.backend,
                )
            )
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        return trace

    async def aclose(self) -> None:
        """Drain background `self_reflect` tasks before shutdown.

        `think()` schedules reflection as fire-and-forget so the
        connector reply isn't delayed. On shutdown we must wait for any
        still-running reflections to finish their SLM call + JSONL
        write, otherwise the audit feed and reflection log get partial
        records when the event loop is torn down. Called by main.py
        BEFORE skill_registry.aclose_all().
        """
        if not self._inflight:
            return
        pending = list(self._inflight)
        log.info("Brain aclose: draining %d background task(s)", len(pending))
        await asyncio.gather(*pending, return_exceptions=True)

    async def proactive_thought(self, mission: str) -> ThoughtTrace:
        """Heartbeat-triggered thought (no human input)."""
        return await self._cycle(user_input=None, mission=mission)

    # ---------- recurring task pipeline (core/scheduler.py) ------------------
    # The 3 intent parsers (create / modify / action) moved to
    # ``core/brain/task_parsing.py`` during the v2.0 split (P1.1d).
    # ``Brain._task_parser`` holds a ``TaskIntentParser`` instance and
    # the methods below are thin compatibility wrappers so existing
    # connector / scheduler call sites (``brain.parse_task_intent(...)``,
    # ``brain.parse_task_modify_intent(...)``,
    # ``brain.parse_task_action_intent(...)``) keep working unchanged.
    # ``synthesize_task_report`` stays on ``Brain`` because it
    # orchestrates a full ``proactive_thought`` cycle (mood + identity
    # + reflection), which is a Brain-level concern.

    async def parse_task_intent(self, user_input: str) -> TaskSpec | None:
        """Wrapper for ``TaskIntentParser.parse_create`` (kept for compat)."""
        return await self._task_parser.parse_create(user_input)

    async def parse_task_modify_intent(
        self,
        user_input: str,
        current_tasks: list[Task],
    ) -> TaskUpdate | None:
        """Wrapper for ``TaskIntentParser.parse_modify`` (kept for compat)."""
        return await self._task_parser.parse_modify(user_input, current_tasks)

    async def parse_task_action_intent(
        self,
        user_input: str,
        current_tasks: list[Task],
    ) -> TaskAction | None:
        """Wrapper for ``TaskIntentParser.parse_action`` (kept for compat)."""
        return await self._task_parser.parse_action(user_input, current_tasks)

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
        # mishandles these consistently (see the regex block in
        # ``core/brain/identity_responder.py`` for the failure modes
        # from production logs). Returns None when the input doesn't
        # match — falls through to the normal pipeline. Skipped on
        # proactive turns (no user_input), and skipped when the
        # operator explicitly asked to search (they want fresh data,
        # not the persona block).
        if (
            is_user_turn
            and user_input
            and not _SEARCH_INTENT_RE.search(user_input)
        ):
            shortcut_reply = await self._identity_responder.try_handle(
                user_input=user_input,
                soul_block=soul_block,
                vitals=vitals,
                mood=mood,
                salutation=self._salutation(),
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
        cognitive_trace: CognitiveTrace | None = None
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
                # STM render is best-effort — a transient failure must not
                # break the cognition path. Log the exception so operators
                # can see why a particular turn ran without prior-turn
                # context (which can degrade follow-up question quality).
                log.exception(
                    "CHAT STM render failed; cognition will run without "
                    "prior-turn context for this message."
                )
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
        web_searched = bool(web_hits) or cognitive_engaged
        task_block = format_task_block(
            user_input=user_input,
            mission=mission,
            salutation=self._salutation(),
            web_searched=web_searched,
        )

        system_prompt = assemble_system_prompt(
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
                web_searched=web_searched,
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
            retry_prompt = build_minimal_retry_prompt(
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
            web_searched=web_searched,
        )

    # ---------- prompt assembly ----------------------------------------------
    # The prompt-assembly helpers (``_assemble``, ``_format_task_block``,
    # ``_user_mentions_codename``, ``_build_minimal_retry_prompt``) moved to
    # ``core/brain/prompt_assembly.py`` during the v2.0 split (P1.1c). They
    # are pure functions and unit-testable without a real Brain instance;
    # ``_cycle`` calls them directly via the module-level imports.

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

    # Identity short-circuit (``_handle_identity_question`` /
    # ``_try_identity_skill``) moved to
    # ``core/brain/identity_responder.py``; ``Brain._identity_responder``
    # holds the dispatcher and ``_cycle`` calls
    # ``self._identity_responder.try_handle(...)`` directly.

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
        if user_input is None or not self._skill_registry.has(self._web_search_skill):
            return ""

        text = user_input.strip()

        # Path 1: explicit user request — always search.
        if _SEARCH_INTENT_RE.search(text):
            # ``.strip("...")`` takes a SET of characters — intentional here
            # to peel any mix of trailing CJK + ASCII punctuation.
            query = _QUERY_STRIP_RE.sub("", text).strip(" ?。?！!.,，。:：")  # noqa: B005
            log.info("CHAT search PATH=explicit query=%r", (query or text)[:120])
            return await self._do_search(query or text, reason="explicit")

        # Path 2: chitchat / non-question — don't burn a triage call.
        if self._chitchat_re is None:
            soul_block = await self._soul.render_identity_block()
            designation, _, _ = extract_identity(soul_block)
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
        """Run the `research` Skill and format hits for the system prompt's KNOWLEDGE block.

        Dispatches via `SkillRegistry` instead of touching SearXNG
        directly. The Skill's `evidence` is iterated here so the output
        format stays byte-identical to the legacy path (the SLM's prompt
        is sensitive to the `[i] title\n    URL: url\n    snippet` shape).
        """
        search_t0 = time.perf_counter()
        result = await self._skill_registry.invoke(
            self._web_search_skill, self._skill_ctx, query=query, limit=5,
        )
        search_ms = int((time.perf_counter() - search_t0) * 1000)
        if not result.ok:
            log.warning(
                "CHAT search FAILED query=%r reason=%s error=%s",
                query, reason, result.error,
            )
            return (
                f"(SearXNG query for {query!r} failed — proceed without live web "
                "results and tell the Architect the search backend is offline.)"
            )
        results = result.evidence
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
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            snippet = (r.get("snippet") or "").replace("\n", " ").strip()
            lines.append(f"[{i}] {title}\n    URL: {url}\n    {snippet}")
        log.info(
            "CHAT search OK reason=%s query=%r hits=%d search_ms=%d first_url=%s",
            reason,
            query,
            len(results),
            search_ms,
            (results[0].get("url") or ""),
        )
        return "\n".join(lines)

    @staticmethod
    def _render_triage_context(stm_context: list[Turn]) -> str:
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
        stm_context: list[Turn] | None = None,
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
        # SET of characters — intentional, peels mixed CJK/ASCII punctuation.
        cleaned = cleaned.strip(" ?。?！!.,，。:：")  # noqa: B005
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

        Dispatches via the `recall` Skill instead of reading the
        archive file directly. The Skill returns evidence sorted by score
        (`[{"line": str, "score": int}, ...]`); we extract `top_score` and
        join lines to preserve the legacy `(text, top_score)` shape.
        """
        if not query.strip():
            return "", 0
        result = await self._skill_registry.invoke(
            "recall", self._skill_ctx, query=query, limit=5,
        )
        # recall is best-effort: failure (or empty evidence) collapses to
        # the legacy "no LTM hit" signal so callers branch the same way.
        if not result.ok or not result.evidence:
            return "", 0
        top_score = int(result.evidence[0].get("score", 0) or 0)
        text = "\n".join(
            str(e.get("line", "")) for e in result.evidence if e.get("line")
        )
        return text, top_score


# Module-level helpers — soul-block parsing (``_extract_identity`` /
# ``_DESIGNATION_RE`` / ``_CODENAME_RE`` / ``_CREATOR_RE``) moved to
# ``core/brain/identity_responder.py``; this module imports
# ``extract_identity`` from there for the prompt-assembly call sites.
