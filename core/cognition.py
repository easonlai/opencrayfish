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

Every deliberation is appended to `state/deliberation.jsonl` for audit and
dashboard visibility. Failures never raise — the loop degrades to whatever
evidence it managed to collect.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .provider import Provider
    from tools.searxng import SearXNG

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
_PLAN_RE = re.compile(
    r"^\s*Q(?P<idx>\d+)\s*[:\-.)]\s*"
    r"(?P<verb>RECALL|SEARCH|ANSWER)\b"
    r"(?:\s*[:\-]?\s*[\"'`]?(?P<query>[^\"'`\n]+?)[\"'`]?\s*)?$",
    re.IGNORECASE | re.MULTILINE,
)
# Refine output: either "OK" alone, or "GAP: SEARCH \"...\"".
_REFINE_OK_RE = re.compile(r"^\s*OK\b", re.IGNORECASE | re.MULTILINE)
_REFINE_GAP_RE = re.compile(
    r"^\s*GAP\s*:\s*SEARCH\s*[\"'`]?(?P<query>[^\"'`\n]+?)[\"'`]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_VALID_VERBS = {"RECALL", "SEARCH", "ANSWER"}


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
    """One sub-question paired with the verb chosen for it."""
    sub_q: str
    verb: str               # RECALL | SEARCH | ANSWER
    query: str = ""         # populated only for SEARCH


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
        searxng: "SearXNG | None",
        archive_path: str | Path,
        max_subquestions: int = 3,
        max_act_rounds: int = 2,    # 1 = no refine; 2 = one refine allowed
        refine_enabled: bool = True,
        feed_path: Path | str = DELIBERATION_FEED,
        timezone: str = "UTC",
    ) -> None:
        from .provider import ChatMessage  # local import to avoid cycle on type stub
        self._ChatMessage = ChatMessage

        self._provider = provider
        self._searxng = searxng
        self._archive_path = Path(archive_path)
        self._max_subq = max(1, min(int(max_subquestions), 5))
        self._max_rounds = max(1, min(int(max_act_rounds), 3))
        self._refine_enabled = bool(refine_enabled) and self._max_rounds >= 2
        self._feed_path = Path(feed_path)
        self._feed_path.parent.mkdir(parents=True, exist_ok=True)
        self._tz = ZoneInfo(timezone)

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
                self._persist(trace)
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
                    if gap_q and self._searxng is not None:
                        gap_step = PlanStep(
                            sub_q=f"(refine) {gap_q}",
                            verb="SEARCH",
                            query=gap_q,
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
            self._persist(trace)
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
        """SLM call #2: assign one verb to each sub-question."""
        system = (
            "You are the PLAN stage of an agent cognitive loop. For EACH\n"
            "sub-question shown below, output ONE line that picks ONE verb\n"
            "from this fixed menu:\n\n"
            "  RECALL                   - search the agent's local memory archive\n"
            "  SEARCH \"<3-8 keywords>\"  - issue a live web search via SearXNG\n"
            "  ANSWER                   - the small SLM can answer from training data\n"
            "\n"
            "OUTPUT FORMAT (follow EXACTLY — one line per Q, in order, no\n"
            "prose, no explanation, no preface):\n"
            "  Q1: <VERB> [\"<query>\" if SEARCH]\n"
            "  Q2: <VERB> [\"<query>\" if SEARCH]\n"
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

        for m in _PLAN_RE.finditer(raw or ""):
            idx = int(m.group("idx"))
            if idx in seen_idx or idx < 1 or idx > len(subqs):
                continue
            verb = m.group("verb").upper()
            if verb not in _VALID_VERBS:
                continue
            query = (m.group("query") or "").strip().strip("\"'`,;.")
            if verb != "SEARCH":
                query = ""
            elif not query:
                # SEARCH without a query → fall back to keyword-extracted form
                # of the user's literal message (single-Q case) or the sub_q
                # (multi-part case). Better than dropping the step OR sending
                # a full English sentence to SearXNG.
                query = self._keywordize(_fallback_kw_source(idx, subqs[idx - 1]))
            plan.append(PlanStep(sub_q=subqs[idx - 1], verb=verb, query=query))
            seen_idx.add(idx)
        # Fill in any sub-questions the SLM forgot to plan for. Default verb
        # is SEARCH (safer than ANSWER for an unplanned question). When PLAN
        # missed Q1 in a single-question turn, prefer the user's literal
        # message over the THINK-derived sub_q so a contaminated THINK can
        # never poison the search query.
        for i, q in enumerate(subqs, start=1):
            if i in seen_idx:
                continue
            log.info("CHAT cognition PLAN missing Q%d → defaulting to SEARCH", i)
            plan.append(PlanStep(
                sub_q=q,
                verb="SEARCH",
                query=self._keywordize(_fallback_kw_source(i, q)),
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
        t0 = time.perf_counter()
        if step.verb == "SEARCH":
            content, hits = await self._do_search(step.query or step.sub_q)
        elif step.verb == "RECALL":
            content, hits = self._do_recall(step.sub_q)
        else:  # ANSWER — no retrieval, synthesize will lean on SLM training data
            content, hits = ("(SLM training data only — no retrieval performed)", 0)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "CHAT cognition stage=ACT verb=%s sub_q=%r query=%r hits=%d elapsed_ms=%d",
            step.verb, step.sub_q[:60], step.query[:80], hits, elapsed_ms,
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
        """SEARCH verb: SearXNG. Returns (formatted_text, hit_count)."""
        if self._searxng is None:
            return ("(no SearXNG configured)", 0)
        q = (query or "").strip()
        if not q:
            return ("(empty query)", 0)
        try:
            results = await self._searxng.search(q, limit=5)
        except Exception as exc:
            log.warning("CHAT cognition SEARCH failed q=%r: %s", q[:80], exc)
            return (f"(search failed: {exc.__class__.__name__})", 0)
        if not results:
            return ("(no results)", 0)
        lines = [f"Query: {q!r}"]
        for r in results:
            title = (r.title or "").strip()
            url = (r.url or "").strip()
            snippet = (r.snippet or "").strip().replace("\n", " ")[:240]
            lines.append(f"- {title} ({url})\n  {snippet}")
        return ("\n".join(lines), len(results))

    def _do_recall(self, sub_q: str) -> tuple[str, int]:
        """RECALL verb: keyword-overlap search against archive.md."""
        if not self._archive_path.exists():
            return ("(archive empty)", 0)
        q = (sub_q or "").lower()
        terms = {t for t in q.split() if len(t) > 3}
        if not terms:
            return ("(no recallable terms)", 0)
        hits: list[tuple[int, str]] = []
        for line in self._archive_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            score = sum(1 for t in terms if t in stripped.lower())
            if score:
                hits.append((score, stripped))
        if not hits:
            return ("(no archive matches)", 0)
        hits.sort(key=lambda kv: kv[0], reverse=True)
        top = [line for _, line in hits[:5]]
        return ("\n".join(f"- {line}" for line in top), len(top))

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

    def _persist(self, trace: CognitiveTrace) -> None:
        """Append the trace to the deliberation feed (non-blocking on errors)."""
        try:
            with self._feed_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(trace.to_jsonable(), ensure_ascii=False) + "\n")
        except Exception:
            log.exception("CHAT cognition persist FAILED feed=%s", self._feed_path)
