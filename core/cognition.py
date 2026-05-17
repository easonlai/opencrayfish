"""core.cognition — Autonomous Cognitive Loop (THINK → PLAN → ACT → REFINE).

Gives the agent a biological-style deliberation cycle in front of the final
synthesize call. The trick that makes this viable on a small Pi 5 SLM is the
same one used by the triage classifier: instead of asking the model to "be
a planner" with chain-of-thought, each stage is a separate one-job prompt
with a hard output cap, parsed by regex, and bounded in rounds.

Stages
------
1. THINK     — restate the user's INTENT in one sentence and decompose it
               into ≤N atomic sub-questions.
2. PLAN      — assign EXACTLY ONE verb from a fixed 3-verb menu to each
               sub-question:  RECALL | SEARCH "<q>" | ANSWER.
3. ACT       — execute the plan concurrently. RECALL hits archive.md, SEARCH
               hits SearXNG. Evidence is collected per sub-question.
4. REFINE    — (optional) re-read intent + evidence; emit `OK` or
               `GAP: SEARCH "<q>"`. Hard cap of ONE refine round.

The loop returns a `CognitiveTrace`. Brain renders the trace's
`knowledge_block` into the synthesize prompt — replacing what the legacy
`_retrieve_relevant + _maybe_web_search` produced for engaged turns.

Every deliberation is appended to `state/deliberation.jsonl` (rotated by
local date with bounded retention) for audit and dashboard visibility.
Failures never raise — the loop degrades to whatever evidence it managed
to collect.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .skills import CostTier, PlanMenuEntry, SkillContext, SkillRegistry

if TYPE_CHECKING:
    from .monitor import Monitor
    from .provider import Provider

log = logging.getLogger(__name__)

DELIBERATION_FEED: Path = Path("state/deliberation.jsonl")

# Output caps — keep each stage's prompt budget tiny so the SLM stays focused.
_MAX_THINK_TOKENS: int = 120
_MAX_PLAN_TOKENS: int = 120
_MAX_REFINE_TOKENS: int = 40

# Parsers for stage output. All regexes are line-oriented and tolerant of
# minor SLM noise (leading bullets, surrounding quotes).
_INTENT_RE = re.compile(r"^\s*INTENT\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_QUESTION_RE = re.compile(
    r"^\s*Q(?P<idx>\d+)\s*[:\-.)]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Plan lines: "Q1: SEARCH \"minions 3 movie\"" | "Q2: RECALL" | "Q3: ANSWER"
# Phase 3 made the verb list dynamic — the regex is rebuilt per call
# from the active PlanMenuEntry list (see `_build_plan_regex`). The
# static fallback below is kept ONLY for the rare case where the
# registry yields zero entries (we still parse but the runner will
# refuse unknown verbs).
#
# IMPORTANT: the optional query group uses `[^\S\n]*` (horizontal
# whitespace) NOT `\s*`. Because `\s` includes `\n`, a `\s*` between
# the verb and the query would let the lazy query capture pull text
# from the NEXT Q-line, producing a single match that swallows the
# rest of the plan. `[^\S\n]*` forbids crossing the line boundary so
# each Q-line stays self-contained.
_PLAN_RE_TEMPLATE = (
    r"^[^\S\n]*Q(?P<idx>\d+)[^\S\n]*[:\-.)][^\S\n]*"
    r"(?P<verb>{verbs})\b"
    r"(?:[^\S\n]*[:\-]?[^\S\n]*[\"'`]?(?P<query>[^\"'`\n]+?)[\"'`]?[^\S\n]*)?$"
)
# Refine output: either "OK" alone, or "GAP: SEARCH \"...\".
_REFINE_OK_RE = re.compile(r"^\s*OK\b", re.IGNORECASE | re.MULTILINE)
_REFINE_GAP_RE = re.compile(
    r"^\s*GAP\s*:\s*SEARCH\s*[\"'`]?(?P<query>[^\"'`\n]+?)[\"'`]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _build_plan_regex(entries: list[PlanMenuEntry]) -> re.Pattern[str]:
    """Compile the PLAN-line regex from the active verb set.

    Per-call so a Skill registered/unregistered at runtime (e.g. a future
    MCP-bridge plugin) immediately changes what the parser accepts.
    Verbs are escaped to defend against an externally-provided Skill
    choosing an exotic verb character.
    """
    if not entries:
        # Defensive: fall back to a never-matching regex so a misconfigured
        # registry can't crash the parser. The PLAN loop will then default
        # every Q to SEARCH via the missing-Q fallback path.
        verbs = "__NO_VERBS__"
    else:
        verbs = "|".join(re.escape(e.verb) for e in entries)
    pattern = _PLAN_RE_TEMPLATE.format(verbs=verbs)
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# Salience helpers — pattern-only, NO hardcoded topic/value lists.
# ---------------------------------------------------------------------------
#
# These power three guards that defend the loop against recency-bias topic
# contamination from prior STM turns:
#
#   1. Verbatim-noun guard   — verifies THINK preserved every salient token
#                              the user actually typed (otherwise the loop
#                              would silently substitute a prior topic).
#   2. Topic-shift detector  — flags new content words present in the
#                              current message but absent from prior STM.
#   3. PLAN fallback safety  — when PLAN can't parse a Q, it keywordises
#                              the user's *raw* input rather than trusting
#                              the (possibly contaminated) THINK sub_q.
#
# Crucially, none of these consult a list of "known topics". They detect
# salience purely from token *shape*: capitalisation, quoting, version-like
# digits, and novelty relative to the prior STM block.

# Capitalised tokens (likely proper nouns), e.g. "Otto", "Hailo", "Pi5".
_CAP_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")
# Quoted strings, e.g. "Bob", 'getUpdates', `safesearch`.
_QUOTED_RE = re.compile(r"[\"'`]([^\"'`\n]{1,40})[\"'`]")
# Version-like tokens: 3.13, v2.5.1, 1.0a, 4.7.0-rc1.
_VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+)+[A-Za-z0-9\-]*\b")
# Standalone multi-digit numbers: 2026, 100, 8765 (single digits are noisy).
# Negative lookbehind/ahead prevent matching inner digits of version tokens
# like "13" inside "v3.13".
_NUMBER_RE = re.compile(r"(?<![\d.])\d{2,}(?![\d.])")
# Alphabetic content words ≥3 chars (used for novelty / new-noun detection).
_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z\-]{2,}\b")


def _salient_tokens(text: str) -> set[str]:
    """Extract user-introduced salient tokens (lowercased for comparison).

    Salience is defined structurally — capitalised words, quoted strings,
    version tags, multi-digit numbers. This is what THINK / PLAN / the
    final synthesis MUST preserve from the user's literal message.

    Pattern-only: this function never references a list of known topics.
    """
    if not text:
        return set()
    tokens: set[str] = set()
    for m in _CAP_TOKEN_RE.finditer(text):
        tokens.add(m.group(0).lower())
    for m in _QUOTED_RE.finditer(text):
        # Split the quoted span into individual word/version tokens too,
        # so multi-word quotes ('Rise of Gru') still yield comparable units.
        for w in re.findall(r"[A-Za-z0-9][\w\-\.]+", m.group(1)):
            tokens.add(w.lower())
    for m in _VERSION_RE.finditer(text):
        tokens.add(m.group(0).lower())
    for m in _NUMBER_RE.finditer(text):
        tokens.add(m.group(0))
    return tokens


def _content_words(text: str, stopwords: frozenset[str]) -> set[str]:
    """Lowercased alpha words ≥3 chars, minus the supplied stopwords."""
    if not text:
        return set()
    return {
        w.lower()
        for w in _WORD_RE.findall(text)
        if w.lower() not in stopwords
    }


def _new_content_words(
    user_input: str, prior_context: str, stopwords: frozenset[str]
) -> set[str]:
    """Content words in user_input that are absent from prior_context.

    Used by the topic-shift detector to warn THINK that a new noun was
    introduced (e.g. "bob" appearing after a prior turn about "otto").
    """
    return _content_words(user_input, stopwords) - _content_words(
        prior_context, stopwords
    )


def _missing_from_haystack(salient: set[str], *texts: str) -> set[str]:
    """Return any salient token not present (substring match) in any text."""
    if not salient:
        return set()
    haystack = " ".join(t.lower() for t in texts if t)
    return {t for t in salient if t not in haystack}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanStep:
    """One sub-question paired with the verb chosen for it.

    `skill_name` is the registry key the verb resolves to at parse time.
    The ACT runner dispatches via
    `skill_registry.invoke(skill_name, ...)` instead of switching on
    `verb`. `verb` is the SLM-facing UPPER token kept for logging and
    deliberation audit byte-identical to legacy traces.
    """
    sub_q: str
    verb: str               # SLM-facing UPPER token, e.g. "SEARCH"
    query: str = ""         # populated only for verbs with a query arg
    skill_name: str = ""    # registry key resolved from verb ("" = noop)


@dataclass
class Evidence:
    """Result of executing one PlanStep."""
    sub_q: str
    verb: str
    query: str
    content: str            # joined text (snippets, archive lines, or "")
    hits: int               # 0 for ANSWER; N for RECALL/SEARCH
    elapsed_ms: int


@dataclass
class CognitiveTrace:
    """Everything the loop produced for one user turn."""
    ts: str
    backend: str
    user_input: str
    intent: str
    sub_questions: list[str]
    plan: list[PlanStep]
    evidence: list[Evidence]
    refine_rounds: int
    refine_decisions: list[str] = field(default_factory=list)
    knowledge_block: str = ""
    total_ms: int = 0
    engaged: bool = True             # False when the loop bailed (e.g. THINK failed)
    bypass_reason: str = ""          # populated when engaged=False

    def to_jsonable(self) -> dict:
        d = asdict(self)
        # PlanStep / Evidence are dataclasses → already jsonable via asdict.
        return d


# ---------------------------------------------------------------------------
# Cognitive Loop
# ---------------------------------------------------------------------------

class CognitiveLoop:
    """Orchestrates THINK → PLAN → ACT → REFINE for a single user turn."""

    def __init__(
        self,
        *,
        provider: "Provider",
        skill_registry: SkillRegistry,
        skill_ctx: SkillContext,
        monitor: "Monitor | None" = None,
        max_subquestions: int = 3,
        max_act_rounds: int = 2,    # 1 = no refine; 2 = one refine allowed
        refine_enabled: bool = True,
        cost_tier_cap: CostTier = "expensive",
        auto_offline_filter: bool = True,
        dispatch_answer_via_skill: bool = False,
        feed_path: Path | str = DELIBERATION_FEED,
        feed_retain_days: int = 14,
        timezone: str = "UTC",
    ) -> None:
        from .provider import ChatMessage  # local import to avoid cycle on type stub
        from .jsonl_writer import RotatingJsonlWriter
        self._ChatMessage = ChatMessage

        self._provider = provider
        # Skill dispatch — every verb in the PLAN menu resolves to a
        # Skill at parse time and dispatches via the registry in ACT.
        # The PLAN menu itself is generated from
        # `SkillRegistry.plan_menu(...)`, so registering a new Skill
        # with a `plan_verb` automatically extends what the SLM can
        # pick — no edits to this file required.
        self._skill_registry = skill_registry
        self._skill_ctx = skill_ctx
        # Monitor is OPTIONAL. When provided, `_active_plan_entries()`
        # consults vital signs to auto-degrade the PLAN menu (stressed
        # vitals → cap to 'cheap', no expensive web calls). When None,
        # the cap comes from `cost_tier_cap` unchanged.
        self._monitor = monitor
        self._max_subq = max(1, min(int(max_subquestions), 5))
        self._max_rounds = max(1, min(int(max_act_rounds), 3))
        self._refine_enabled = bool(refine_enabled) and self._max_rounds >= 2
        # PLAN menu filter knobs. `cost_tier_cap` is the OPERATOR
        # baseline (config-driven); `_active_plan_entries()` may tighten
        # it further at runtime based on vitals. `auto_offline_filter`
        # consults `provider.is_tripped` per turn so the SLM never picks
        # a network skill while the circuit-breaker is open.
        self._cost_tier_cap: CostTier = cost_tier_cap
        self._auto_offline_filter = bool(auto_offline_filter)
        # When True, ANSWER verb actually runs `direct_answer` Skill
        # (one extra SLM call per ANSWER step) and surfaces the reply
        # as evidence. When False (legacy), ANSWER is a marker that
        # tells synth "no retrieval needed" — no extra SLM call.
        self._dispatch_answer_via_skill = bool(dispatch_answer_via_skill)
        self._feed_path = Path(feed_path)
        self._feed_path.parent.mkdir(parents=True, exist_ok=True)
        # Deliberation traces rotate by local date with bounded
        # retention. Deliberations are bigger than skill audit
        # rows (whole THINK/PLAN/ACT/REFINE payload), so default to a
        # shorter retention window than the skill feed.
        self._feed_writer = RotatingJsonlWriter(
            self._feed_path,
            retain_days=feed_retain_days,
            tz=timezone,
        )
        self._tz = ZoneInfo(timezone)

    # ---------- PLAN-menu plumbing -------------------------------------------

    async def _active_plan_entries(self) -> list[PlanMenuEntry]:
        """Compute the verb menu the PLAN stage should offer THIS turn.

        Pulls two runtime signals into the registry filter:

          1. **Provider circuit-breaker** — when `auto_offline_filter`
             is on (default) and `provider.is_tripped` is True, drop
             every skill with `requires_network=True` so the SLM
             never picks SEARCH while we can't reach SearXNG.

          2. **Vitals stress** — when a Monitor is wired and reports
             `is_stressed=True`, tighten the cost-tier cap to 'cheap'
             so the loop stays local (no expensive web round-trips
             while the Pi is thermally throttling or RAM-starved).

        Both signals OVERRIDE the operator-configured baseline cap;
        never RELAX it (a stressed Pi never gets MORE permissions).
        """
        cap: CostTier = self._cost_tier_cap
        exclude_network = False

        if self._auto_offline_filter and getattr(self._provider, "is_tripped", False):
            exclude_network = True

        if self._monitor is not None:
            try:
                vitals = await self._monitor.sample()
                if getattr(vitals, "is_stressed", False):
                    # Tighten to 'cheap' only if the operator wasn't
                    # already restricting harder (don't promote 'free').
                    if cap == "expensive":
                        cap = "cheap"
            except Exception:
                log.exception("CHAT cognition vitals sample failed (keeping cap=%s)", cap)

        entries = self._skill_registry.plan_menu(
            cost_tier_cap=cap,
            exclude_network=exclude_network,
        )
        log.debug(
            "CHAT cognition plan_menu cap=%s exclude_network=%s entries=%s",
            cap, exclude_network, [e.verb for e in entries],
        )
        return entries

    # ---------- public entry --------------------------------------------------

    async def deliberate(
        self,
        user_input: str,
        *,
        stm_context: str = "",
    ) -> CognitiveTrace:
        """Run the full loop. Always returns a trace, never raises."""
        t0 = time.perf_counter()
        ts = datetime.now(tz=self._tz).isoformat(timespec="seconds")
        backend = getattr(self._provider, "active_backend", "?")
        trace = CognitiveTrace(
            ts=ts,
            backend=backend,
            user_input=user_input,
            intent="",
            sub_questions=[],
            plan=[],
            evidence=[],
            refine_rounds=0,
        )
        try:
            # 1. THINK
            think_t0 = time.perf_counter()
            intent, subqs = await self._stage_think(user_input, stm_context)
            think_ms = int((time.perf_counter() - think_t0) * 1000)
            log.info(
                "CHAT cognition stage=THINK intent=%r subq=%d elapsed_ms=%d",
                intent[:80], len(subqs), think_ms,
            )
            if not subqs:
                trace.engaged = False
                trace.bypass_reason = "think_empty"
                trace.total_ms = int((time.perf_counter() - t0) * 1000)
                await self._persist(trace)
                return trace
            trace.intent = intent
            trace.sub_questions = subqs

            # 2. PLAN
            plan_t0 = time.perf_counter()
            plan = await self._stage_plan(intent, subqs, user_input=user_input)
            plan_ms = int((time.perf_counter() - plan_t0) * 1000)
            log.info(
                "CHAT cognition stage=PLAN steps=%s elapsed_ms=%d",
                ",".join(f"{s.verb}{'(' + s.query + ')' if s.query else ''}" for s in plan),
                plan_ms,
            )
            trace.plan = plan

            # 3. ACT (round 1)
            evidence = await self._stage_act(plan)
            trace.evidence.extend(evidence)
            trace.refine_rounds = 1

            # 4. REFINE (optional, single extra round at most)
            if self._refine_enabled:
                refine_t0 = time.perf_counter()
                decision = await self._stage_refine(intent, subqs, trace.evidence)
                refine_ms = int((time.perf_counter() - refine_t0) * 1000)
                trace.refine_decisions.append(decision)
                log.info(
                    "CHAT cognition stage=REFINE decision=%r elapsed_ms=%d",
                    decision[:80], refine_ms,
                )
                if decision.upper().startswith("GAP"):
                    gap_q = self._extract_gap_query(decision)
                    if gap_q and self._skill_registry.has("research"):
                        gap_step = PlanStep(
                            sub_q=f"(refine) {gap_q}",
                            verb="SEARCH",
                            query=gap_q,
                            skill_name="research",
                        )
                        trace.plan.append(gap_step)
                        more = await self._stage_act([gap_step])
                        trace.evidence.extend(more)
                        trace.refine_rounds = 2

            # 5. Build knowledge block for synthesize
            trace.knowledge_block = self._render_knowledge(trace)
        except Exception:
            # Loop failure must NEVER block the reply. Log + degrade gracefully.
            log.exception("CHAT cognition FAILED user_input=%r", user_input[:120])
            trace.engaged = False
            trace.bypass_reason = "exception"
        finally:
            trace.total_ms = int((time.perf_counter() - t0) * 1000)
            log.info(
                "CHAT cognition done engaged=%s rounds=%d evidence=%d total_ms=%d",
                trace.engaged, trace.refine_rounds, len(trace.evidence), trace.total_ms,
            )
            await self._persist(trace)
        return trace

    # ---------- stages --------------------------------------------------------

    async def _stage_think(
        self, user_input: str, stm_context: str
    ) -> tuple[str, list[str]]:
        """SLM call #1: produce INTENT + sub-questions."""
        system = (
            "You are the THINK stage of an agent cognitive loop. You read the\n"
            "user's current message and decompose it into atomic sub-questions.\n"
            "\n"
            "OUTPUT FORMAT (follow EXACTLY — every line starts with a literal\n"
            "label, no prose, no explanation, no preface):\n"
            "  INTENT: <one short sentence stating what the user actually wants>\n"
            f"  Q1: <atomic sub-question 1 — a complete question>\n"
            f"  Q2: <atomic sub-question 2, ONLY if the request has a 2nd part>\n"
            f"  Q3: <atomic sub-question 3, ONLY if the request has a 3rd part>\n"
            "\n"
            "EXTRACTION RULES — critical:\n"
            "  A. EXTRACT, DO NOT PARAPHRASE. Copy the user's actual nouns,\n"
            "     numbers, version tags, dates, and proper nouns VERBATIM into\n"
            "     the INTENT and the Qs. Do not substitute synonyms or generic\n"
            "     descriptors for specific values the user gave you.\n"
            "  B. PRESERVE MULTI-PART STRUCTURE. If the user asked two or three\n"
            "     things joined by 'and', commas, or separate clauses, emit one\n"
            "     Q per part. Do NOT collapse them into one Q.\n"
            f"  C. Maximum {self._max_subq} sub-questions. Use FEWER (just Q1)\n"
            "     when the request is genuinely a single question.\n"
            "  D. Do NOT answer the question. Do NOT plan tools. Just decompose.\n"
            "\n"
            "Format examples (study the FORMAT only — these are abstract\n"
            "patterns with placeholders, NOT topics to copy):\n"
            "  user: <a single-part question about <X>>\n"
            "      INTENT: answer <verbatim phrasing about X>\n"
            "      Q1: <complete question about X using user's exact wording>\n"
            "  user: <a two-part question about <X> and <Y>>\n"
            "      INTENT: answer about <X> and <Y>\n"
            "      Q1: <complete question about X>\n"
            "      Q2: <complete question about Y>\n"
            "  user: <arithmetic with specific values <A> and <B>>\n"
            "      INTENT: compute <A> <op> <B>\n"
            "      Q1: what is <A> <op> <B>?         (PRESERVE the actual values)\n"
            "\n"
            f"Output limit: roughly {_MAX_THINK_TOKENS} tokens. Output the\n"
            "INTENT line and Q-lines only. No commentary."
        )
        # Topic-shift detector: if the user introduced lowercase content
        # words that don't appear anywhere in the prior STM block, prepend
        # a soft warning so THINK is nudged to honour the new noun(s)
        # BEFORE the verbatim-noun guard has to fire downstream. Saves
        # latency by reducing guard hits; pure pattern match, no
        # hardcoded topic list.
        shift_notice = ""
        if stm_context:
            new_words = _new_content_words(
                user_input, stm_context, self._FILLER_WORDS
            )
            if new_words:
                shift_notice = (
                    "TOPIC-SHIFT NOTICE: the current message introduces "
                    f"new noun(s) {sorted(new_words)} not present above. "
                    "DO NOT carry forward the prior topic; deliberate about "
                    "the NEW noun(s) only.\n\n"
                )
        user_payload = (
            f"{stm_context}{shift_notice}Current message: {user_input}"
            if (stm_context or shift_notice)
            else f"Current message: {user_input}"
        )
        raw = await self._safe_generate(system, user_payload, "THINK")
        intent_m = _INTENT_RE.search(raw or "")
        intent = intent_m.group("value").strip() if intent_m else user_input.strip()[:200]
        subqs: list[str] = []
        for m in _QUESTION_RE.finditer(raw or ""):
            q = m.group("value").strip().strip("\"'`")
            if q and len(q) <= 240:
                subqs.append(q)
            if len(subqs) >= self._max_subq:
                break
        # Fallback: if THINK produced no Qs, treat the user input itself as Q1
        # so the loop still does something useful.
        if not subqs:
            subqs = [user_input.strip()]

        # Verbatim-noun guard: when prior STM is in scope, the small SLM
        # sometimes substitutes the previous turn's topic for the new one
        # (e.g. user says "bob", THINK emits Q about "otto"). The must-
        # preserve set is the UNION of two pattern-only signals:
        #   (a) structurally salient tokens — capitalised, quoted, versions,
        #       multi-digit numbers (e.g. "Hailo", "v3.13", "2026").
        #   (b) topic-shift signal — content words present in the user's
        #       message but ABSENT from the prior STM context (e.g.
        #       lowercase "bob" appearing after a turn about "otto").
        # If any token in this set is missing from BOTH the INTENT and
        # every Q, discard THINK's output and fall back to the user's raw
        # message as Q1 — that guarantees the rest of the loop deliberates
        # about the right thing, even if THINK was contaminated.
        salient = _salient_tokens(user_input)
        if stm_context:
            salient |= _new_content_words(
                user_input, stm_context, self._FILLER_WORDS
            )
        if salient:
            missing = _missing_from_haystack(salient, intent, *subqs)
            if missing:
                log.warning(
                    "CHAT cognition THINK guard FIRED: salient tokens %s "
                    "missing from intent=%r subqs=%r — discarding THINK "
                    "output and using user_input as Q1.",
                    sorted(missing), intent[:80], [q[:60] for q in subqs],
                )
                # Replace BOTH intent and subqs so PLAN never sees the
                # contaminated payload.
                intent = user_input.strip()[:200]
                subqs = [user_input.strip()]
        return intent, subqs

    async def _stage_plan(
        self, intent: str, subqs: list[str], *, user_input: str = ""
    ) -> list[PlanStep]:
        """SLM call #2: assign one verb to each sub-question.

        The verb menu is built dynamically from
        `SkillRegistry.plan_menu(...)` so registering a new Skill (with
        a `plan_verb`) automatically extends what the SLM can pick.
        Filters (cost-tier cap, exclude-network) are recomputed every
        turn from vitals + provider circuit-breaker state, so a
        stressed Pi or an offline SearXNG immediately drops options
        the SLM otherwise wastes a turn picking.
        """
        entries = await self._active_plan_entries()
        verb_to_skill: dict[str, PlanMenuEntry] = {e.verb: e for e in entries}
        plan_re = _build_plan_regex(entries)
        # Render the menu block from entries. Each line:
        #   `  VERB [arg-hint]  - description`
        # Width-padded to 26 chars before the dash so the menu lines up
        # visually — small SLMs anchor on column alignment as a
        # "this is a list" cue.
        menu_lines: list[str] = []
        for e in entries:
            head = e.verb if not e.arg_hint else f"{e.verb} {e.arg_hint}"
            menu_lines.append(f"  {head:<26} - {e.description}")
        menu_block = "\n".join(menu_lines) if menu_lines else "  (no skills available)"
        # Default fallback verb for missing-Q recovery: prefer the
        # research/SEARCH-style verb (network-aware) when present,
        # else the first available verb in the menu, else legacy "SEARCH".
        fallback_verb: str = "SEARCH"
        fallback_skill: str = "research"
        for e in entries:
            if e.has_query_arg:
                fallback_verb = e.verb
                fallback_skill = e.skill_name
                break
        else:
            if entries:
                fallback_verb = entries[0].verb
                fallback_skill = entries[0].skill_name
        system = (
            "You are the PLAN stage of an agent cognitive loop. For EACH\n"
            "sub-question shown below, output ONE line that picks ONE verb\n"
            "from this menu:\n\n"
            f"{menu_block}\n"
            "\n"
            "OUTPUT FORMAT (follow EXACTLY — one line per Q, in order, no\n"
            "prose, no explanation, no preface):\n"
            "  Q1: <VERB> [\"<query>\" if the verb takes one]\n"
            "  Q2: <VERB> [\"<query>\" if the verb takes one]\n"
            "\n"
            "Format examples (study the FORMAT only — these are abstract\n"
            "patterns with placeholders, NOT topics to copy):\n"
            "  Q1: SEARCH \"<noun phrase from user, 3-8 keywords>\"\n"
            "  Q2: RECALL\n"
            "  Q3: ANSWER\n"
            "\n"
            "VERB SELECTION RULES:\n"
            "  - SEARCH for: time-sensitive facts, named entities, version\n"
            "    numbers, proper nouns you are not 100% sure of, any\n"
            "    'latest/recent/current' question.\n"
            "  - RECALL for: anything the operator has discussed before, their\n"
            "    personal preferences, prior conversation context.\n"
            "  - ANSWER ONLY for: stable textbook facts (arithmetic, basic\n"
            "    definitions, mainstream programming syntax) that DO NOT\n"
            "    depend on dates or versions.\n"
            "  - When in doubt, prefer SEARCH over ANSWER. The local model\n"
            "    is small.\n"
            "\n"
            "QUERY CONSTRUCTION (for SEARCH):\n"
            "  - 3-8 keywords, NOT a full sentence.\n"
            "  - EXTRACT the user's actual nouns, numbers, and version tags\n"
            "    from the sub-question. Do NOT paraphrase.\n"
            "  - Drop filler words: 'what', 'is', 'the', 'a', 'how', '?'.\n"
            "\n"
            f"Output limit: roughly {_MAX_PLAN_TOKENS} tokens. Output ONLY\n"
            "the Q-lines. Any other text will be rejected."
        )
        # Render the THINK output back into the user payload so PLAN has full
        # context without us re-prompting.
        user_payload_lines = [f"INTENT: {intent}"]
        for i, q in enumerate(subqs, start=1):
            user_payload_lines.append(f"Q{i}: {q}")
        raw = await self._safe_generate(
            system, "\n".join(user_payload_lines), "PLAN"
        )
        plan: list[PlanStep] = []
        seen_idx: set[int] = set()
        # Helper: choose the safer keyword source for a fallback SEARCH.
        # When the THINK guard already replaced subqs with user_input, both
        # are identical; when subqs is a (possibly contaminated) paraphrase
        # of a single-question turn, the user's literal message is the more
        # trustworthy source. For multi-part turns (Q2/Q3) the per-Q sub_q
        # is the right source since user_input covers all parts.
        def _fallback_kw_source(idx: int, sub_q: str) -> str:
            if user_input and idx == 1 and len(subqs) == 1:
                return user_input
            return sub_q

        for m in plan_re.finditer(raw or ""):
            idx = int(m.group("idx"))
            if idx in seen_idx or idx < 1 or idx > len(subqs):
                continue
            verb = m.group("verb").upper()
            entry = verb_to_skill.get(verb)
            if entry is None:
                continue
            query = (m.group("query") or "").strip().strip("\"'`,;.")
            if not entry.has_query_arg:
                query = ""
            elif not query:
                # Query-arg verb without a query → fall back to keyword-
                # extracted form of the user's literal message (single-Q
                # case) or the sub_q (multi-part case). Better than
                # dropping the step OR sending a full English sentence to
                # the underlying tool.
                query = self._keywordize(_fallback_kw_source(idx, subqs[idx - 1]))
            plan.append(PlanStep(
                sub_q=subqs[idx - 1],
                verb=entry.verb,
                query=query,
                skill_name=entry.skill_name,
            ))
            seen_idx.add(idx)
        # Fill in any sub-questions the SLM forgot to plan for. Default verb
        # is the registry's network-aware verb (typically SEARCH); if the
        # network was filtered out we use the first available verb. When
        # PLAN missed Q1 in a single-question turn, prefer the user's
        # literal message over the THINK-derived sub_q so a contaminated
        # THINK can never poison the search query.
        for i, q in enumerate(subqs, start=1):
            if i in seen_idx:
                continue
            log.info(
                "CHAT cognition PLAN missing Q%d → defaulting to %s",
                i, fallback_verb,
            )
            fallback_entry = verb_to_skill.get(fallback_verb)
            fallback_needs_query = (
                fallback_entry.has_query_arg if fallback_entry else True
            )
            plan.append(PlanStep(
                sub_q=q,
                verb=fallback_verb,
                query=self._keywordize(_fallback_kw_source(i, q)) if fallback_needs_query else "",
                skill_name=fallback_skill,
            ))
        return plan

    async def _stage_act(self, steps: list[PlanStep]) -> list[Evidence]:
        """Execute every step concurrently. Each verb has its own runner."""
        if not steps:
            return []
        coros = [self._run_step(s) for s in steps]
        results = await asyncio.gather(*coros, return_exceptions=True)
        evidence: list[Evidence] = []
        for s, r in zip(steps, results):
            if isinstance(r, BaseException):
                log.warning(
                    "CHAT cognition ACT step verb=%s sub_q=%r failed: %s",
                    s.verb, s.sub_q[:60], r,
                )
                evidence.append(Evidence(
                    sub_q=s.sub_q, verb=s.verb, query=s.query,
                    content="(no result — step failed)", hits=0, elapsed_ms=0,
                ))
            else:
                evidence.append(r)
        return evidence

    async def _run_step(self, step: PlanStep) -> Evidence:
        """Execute one PlanStep by dispatching its resolved Skill.

        Dispatch table is `step.skill_name`. The verb (`SEARCH`
        / `RECALL` / `ANSWER`) is kept on the step for logging and
        deliberation-audit byte-identity with legacy traces, but the
        runner no longer cares about it — the registry key is the
        authoritative dispatch handle.

        Three known shapes (legacy verbs) get bespoke formatting so the
        synth prompt is byte-identical across releases. Any future
        Skill with a `plan_verb` (e.g. an MCP-bridged tool) gets a
        generic `summary`-based dispatch.
        """
        t0 = time.perf_counter()
        skill_name = step.skill_name
        if skill_name == "research":
            content, hits = await self._do_search(step.query or step.sub_q)
        elif skill_name == "recall":
            content, hits = await self._do_recall(step.sub_q)
        elif skill_name == "direct_answer":
            if self._dispatch_answer_via_skill:
                content, hits = await self._do_direct_answer(step.sub_q)
            else:
                # Legacy ANSWER semantic: no retrieval performed, synth
                # leans on the SLM's training data. The string is a
                # marker for the synth prompt's KNOWLEDGE block.
                content, hits = (
                    "(SLM training data only — no retrieval performed)", 0,
                )
        elif skill_name and self._skill_registry.has(skill_name):
            # Generic dispatch path for any registered Skill that
            # exposes a `plan_verb` but isn't one of the three legacy
            # verbs. We invoke with query=step.query|sub_q and surface
            # the Skill's `summary` directly into the evidence content.
            content, hits = await self._do_generic_skill(
                skill_name, step.query or step.sub_q,
            )
        else:
            # Empty skill_name or unknown name — degrade to ANSWER's
            # no-op marker rather than failing the whole turn.
            log.warning(
                "CHAT cognition unknown skill_name=%r verb=%r — treating as ANSWER no-op",
                skill_name, step.verb,
            )
            content, hits = (
                "(SLM training data only — no retrieval performed)", 0,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "CHAT cognition stage=ACT verb=%s skill=%s sub_q=%r query=%r hits=%d elapsed_ms=%d",
            step.verb, skill_name, step.sub_q[:60], step.query[:80], hits, elapsed_ms,
        )
        return Evidence(
            sub_q=step.sub_q, verb=step.verb, query=step.query,
            content=content, hits=hits, elapsed_ms=elapsed_ms,
        )

    async def _stage_refine(
        self,
        intent: str,
        subqs: list[str],
        evidence: list[Evidence],
    ) -> str:
        """SLM call #3: decide OK or one GAP."""
        system = (
            "You are the REFINE stage of an agent cognitive loop. You see\n"
            "the original INTENT, the SUB-QUESTIONS, and the EVIDENCE that\n"
            "was just gathered. Decide if the evidence is sufficient.\n\n"
            "Output exactly ONE of:\n"
            "  OK                       - evidence is enough; proceed to reply\n"
            "  GAP: SEARCH \"<query>\"    - one specific gap remains; ONE more search\n\n"
            "Rules:\n"
            "  - Output OK by default. Only emit GAP when there is a CONCRETE\n"
            "    missing piece you can name and search for.\n"
            "  - Maximum ONE GAP per turn. Do not chain refinements.\n"
            "  - GAP queries: 3-8 keywords, NOT a sentence.\n"
            "  - Do NOT explain. Output the directive only.\n\n"
            f"Output limit: roughly {_MAX_REFINE_TOKENS} tokens."
        )
        # Render evidence compactly so the small SLM can hold it.
        ev_lines: list[str] = []
        for e in evidence:
            preview = e.content.strip().replace("\n", " ")[:160]
            ev_lines.append(
                f"- [{e.verb}] sub_q={e.sub_q[:80]!r} hits={e.hits} preview={preview!r}"
            )
        payload_lines = [
            f"INTENT: {intent}",
            "SUB-QUESTIONS:",
            *(f"  Q{i}: {q}" for i, q in enumerate(subqs, start=1)),
            "EVIDENCE:",
            *ev_lines,
        ]
        raw = await self._safe_generate(
            system, "\n".join(payload_lines), "REFINE"
        )
        text = (raw or "").strip()
        if not text:
            return "OK"
        if _REFINE_GAP_RE.search(text):
            return text.splitlines()[0].strip()
        if _REFINE_OK_RE.search(text):
            return "OK"
        # Ambiguous → conservative default = OK (don't burn another round).
        return "OK"

    # ---------- verb runners --------------------------------------------------

    async def _do_search(self, query: str) -> tuple[str, int]:
        """SEARCH verb: dispatches the `research` Skill. Returns (formatted_text, hit_count).

        Routes through the registry rather than touching SearXNG
        directly. The result's `evidence` (list of `{title, url, snippet}`
        dicts) is iterated here so the formatted block stays byte-identical
        to the legacy `- title (url)\n  snippet` shape the SLM expects.
        """
        if not self._skill_registry.has("research"):
            return ("(no SearXNG configured)", 0)
        q = (query or "").strip()
        if not q:
            return ("(empty query)", 0)
        result = await self._skill_registry.invoke(
            "research", self._skill_ctx, query=q, limit=5,
        )
        if not result.ok:
            log.warning(
                "CHAT cognition SEARCH failed q=%r: %s", q[:80], result.error or "?",
            )
            return (f"(search failed: {result.error or 'unknown'})", 0)
        results = result.evidence
        if not results:
            return ("(no results)", 0)
        lines = [f"Query: {q!r}"]
        for r in results:
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            snippet = (r.get("snippet") or "").strip().replace("\n", " ")[:240]
            lines.append(f"- {title} ({url})\n  {snippet}")
        return ("\n".join(lines), len(results))

    async def _do_recall(self, sub_q: str) -> tuple[str, int]:
        """RECALL verb: dispatches the `recall` Skill (keyword overlap vs archive.md).

        Routes through the registry. The Skill returns evidence
        sorted by score (`[{"line": str, "score": int}, ...]`); we keep
        the legacy `- {line}` bullet format byte-identical so the synth
        prompt is unchanged.
        """
        q = (sub_q or "").strip()
        if not q:
            return ("(no recallable terms)", 0)
        result = await self._skill_registry.invoke(
            "recall", self._skill_ctx, query=q, limit=5,
        )
        if not result.ok or not result.evidence:
            return ("(no archive matches)", 0)
        lines = [str(e.get("line", "")) for e in result.evidence if e.get("line")]
        if not lines:
            return ("(no archive matches)", 0)
        return ("\n".join(f"- {line}" for line in lines), len(lines))

    async def _do_direct_answer(self, sub_q: str) -> tuple[str, int]:
        """ANSWER verb (opt-in): dispatch the `direct_answer` Skill.

        Off by default to preserve byte-identical legacy behavior
        (where ANSWER is a no-op marker telling synth not to bother
        retrieving). Operators flip `dispatch_answer_via_skill=True`
        when they want the SLM's per-sub-question answer surfaced as
        evidence in the synth KNOWLEDGE block.
        """
        q = (sub_q or "").strip()
        if not q:
            return ("(empty sub-question)", 0)
        result = await self._skill_registry.invoke(
            "direct_answer", self._skill_ctx, query=q,
        )
        if not result.ok or not result.summary.strip():
            return ("(SLM training data only — no retrieval performed)", 0)
        text = result.summary.strip().replace("\n", " ")
        # Cap to keep one ANSWER step from dominating the synth budget.
        if len(text) > 320:
            text = text[:317].rstrip() + "..."
        return (f"- {text}", 1)

    async def _do_generic_skill(self, skill_name: str, query: str) -> tuple[str, int]:
        """Generic dispatch for any registered Skill that declared `plan_verb`.

        Falls back to the Skill's `summary` field (every Skill must
        produce one) and the count of evidence entries it returned.
        Failures degrade to a clear marker so synth knows the step
        didn't bring useful evidence.
        """
        q = (query or "").strip()
        result = await self._skill_registry.invoke(
            skill_name, self._skill_ctx, query=q,
        )
        if not result.ok:
            return (f"(skill {skill_name!r} failed: {result.error or 'unknown'})", 0)
        summary = (result.summary or "").strip()
        hits = len(result.evidence)
        if not summary:
            return (f"(skill {skill_name!r} returned no summary)", hits)
        return (summary, hits)

    # ---------- helpers -------------------------------------------------------

    async def _safe_generate(self, system: str, user: str, stage: str) -> str:
        """Wrap provider.generate with stage-aware logging."""
        try:
            raw = await self._provider.generate(
                system,
                [self._ChatMessage(role="user", content=user)],
            )
            return raw or ""
        except Exception:
            log.exception("CHAT cognition stage=%s provider call failed", stage)
            return ""

    @staticmethod
    def _extract_gap_query(decision: str) -> str:
        m = _REFINE_GAP_RE.search(decision)
        if not m:
            return ""
        return (m.group("query") or "").strip().strip("\"'`,;.")

    # Common English filler words to strip when falling back to a
    # keyword-only query. Kept conservative — domain nouns/numbers MUST
    # survive untouched so the resulting query still represents the user's
    # actual question. Mirrors the spirit of the triage classifier's
    # "EXTRACT, do NOT paraphrase" rule.
    _FILLER_WORDS = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
        "do", "does", "did", "doing", "done",
        "can", "could", "should", "would", "may", "might", "must",
        "i", "you", "your", "we", "they", "them", "it", "its",
        "of", "in", "on", "at", "for", "to", "from", "with", "about",
        "and", "or", "but", "so", "as", "this", "that", "these", "those",
        "tell", "me", "give", "show", "find", "explain",
    })

    @classmethod
    def _keywordize(cls, text: str) -> str:
        """Strip filler words & punctuation, leaving the salient nouns/numbers.

        Used as a fallback when PLAN/THINK omit a SEARCH query — the
        sub-question text often reads like a sentence ("what is X?") which
        SearXNG handles poorly compared to the keyword-only form ("X").
        """
        cleaned = re.sub(r"[^\w\s\-]", " ", text or "")
        tokens = [t for t in cleaned.lower().split() if t]
        kept = [t for t in tokens if t not in cls._FILLER_WORDS]
        # Preserve original casing for the kept tokens by re-walking the input.
        out: list[str] = []
        seen = 0
        for raw_tok in re.findall(r"\w[\w\-]*", text or ""):
            if seen >= len(kept):
                break
            if raw_tok.lower() == kept[seen]:
                out.append(raw_tok)
                seen += 1
        result = " ".join(out)[:120].strip()
        return result or (text or "")[:120]
    def _render_knowledge(self, trace: CognitiveTrace) -> str:
        """Format the trace into a knowledge block for the synthesize prompt.

        Designed to read well to a small SLM: explicit headers, indented
        evidence, an instruction at the bottom telling synthesize how to use
        what it sees. Citations come through naturally because each SEARCH
        block lists URLs alongside titles.
        """
        if not trace.engaged:
            return ""
        lines: list[str] = [
            "Cognitive deliberation (the agent's own structured reasoning for this turn):",
            f"INTENT: {trace.intent}",
            "SUB-QUESTIONS:",
        ]
        for i, q in enumerate(trace.sub_questions, start=1):
            lines.append(f"  Q{i}: {q}")
        lines.append("")
        lines.append("EVIDENCE GATHERED:")
        for i, e in enumerate(trace.evidence, start=1):
            verb_tag = e.verb if e.verb != "SEARCH" else f"SEARCH {e.query!r}"
            lines.append(f"[Step {i}] sub_q={e.sub_q!r}  via {verb_tag}  (hits={e.hits})")
            lines.append(self._indent(e.content, "    "))
            lines.append("")
        if trace.refine_rounds > 1:
            lines.append(
                f"(Refine round was used — {trace.refine_decisions[-1] if trace.refine_decisions else ''})"
            )
            lines.append("")
        lines.append(
            "Synthesize a complete answer that addresses the INTENT using the "
            "EVIDENCE above plus your own reasoning. When you used SEARCH "
            "evidence, cite the URL inline. If the evidence does not actually "
            "answer part of the INTENT, say so plainly rather than guessing."
        )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _indent(text: str, prefix: str) -> str:
        return "\n".join(prefix + ln for ln in (text or "").splitlines() if ln)

    async def _persist(self, trace: CognitiveTrace) -> None:
        """Append the trace via the date-rotating writer (never raises)."""
        await self._feed_writer.append(trace.to_jsonable())
