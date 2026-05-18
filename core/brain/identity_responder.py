"""Identity short-circuit + soul-block parsing.

The small SLM (qwen2:1.5b) reliably mishandles the most basic identity
questions. From production logs:

  ``"what is your name"``  → triage decides SEARCH, query="name" → Netflix
                             "My Name" + Wikipedia "Your Name" anime polluted
                             the synth context, output became junk
  ``"did you know my name"`` → triage decided SEARCH on "know my name"
  ``"how are you"``          → triage emitted ``query="<your name>"`` (literal)

Even though the IDENTITY block is part of every system prompt, the model
attends to the LIVE KNOWLEDGE block more strongly than the persona block
when the input is short and ambiguous, so polluted web hits win.

Fix: detect a small set of unambiguous identity-class questions with
regex BEFORE any cognition / triage / search runs, and return a
templated reply built from soul.md + config.yaml + live vitals/mood.
Deterministic. Zero SLM round-trip. Zero web hit. Zero hallucination.

Compound questions (e.g. ``"what's your name and what can you do?"``) are
NOT short-circuited because the regexes are anchored with ``^...$`` end
markers — those fall through to the normal cognition / triage / synth
pipeline so the time / task portion is answered correctly.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..emotions import EmotionVector
    from ..monitor import VitalSigns
    from ..skills import SkillContext, SkillRegistry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Identity-question regexes
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Soul-block parsing (IMMUTABLE_CORE)
# ---------------------------------------------------------------------------
_DESIGNATION_RE = re.compile(r"\*\*Designation\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)
_CODENAME_RE = re.compile(r"\*\*Codename\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)
_CREATOR_RE = re.compile(r"\*\*Creator\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)


def extract_identity(soul_block: str) -> tuple[str, str, str]:
    """Pull Designation, Codename, and Creator out of the IMMUTABLE_CORE soul block.

    Returns ``(designation, codename, creator)``. All three are PURELY sourced
    from soul.md (designation is itself injected by SoulHandler from
    ``cfg.system.individual_designation`` at runtime). When a field is
    missing we return a NEUTRAL, non-branded placeholder — NEVER a
    project-specific literal — so a fork that renames everything cannot
    accidentally surface this project's name through a fallback.

    Codename in particular falls back to an EMPTY string (not a
    descriptive phrase like "the framework") so the conditional
    codename-disambiguation block in `assemble_system_prompt` is
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


# Legacy private alias — retained only as an in-module convenience; the
# package facade (``core/brain/__init__.py``) no longer re-exports it as of
# v2.0 / P1.2. External callers MUST use ``extract_identity``. This alias
# stays for any in-package code that historically called the private name.
_extract_identity = extract_identity


# ---------------------------------------------------------------------------
# IdentityResponder
# ---------------------------------------------------------------------------
class IdentityResponder:
    """Deterministic short-circuit for identity-class questions.

    Held by ``Brain`` and consulted at the top of ``_cycle`` (before any
    cognition / triage / SLM call). The orchestrator passes in the live
    ``vitals`` and ``mood`` snapshots plus the rendered ``salutation`` so
    this class needs no awareness of monitoring or persona-config wiring.

    The ``IdentitySkill`` delegation (name + creator branches) keeps the
    persona block as a single source of truth — a custom skill can
    override the templated reply without touching this file.
    """

    def __init__(
        self,
        *,
        skill_registry: SkillRegistry | None,
        skill_ctx: SkillContext | None,
        architect_name: str,
    ) -> None:
        self._skill_registry = skill_registry
        self._skill_ctx = skill_ctx
        self._architect_name = architect_name

    async def try_handle(
        self,
        *,
        user_input: str,
        soul_block: str,
        vitals: VitalSigns,
        mood: EmotionVector,
        salutation: str,
    ) -> str | None:
        """Short-circuit deterministic identity-class questions.

        Returns a templated reply built from soul.md + config.yaml + live
        vitals/mood, OR ``None`` when the input does NOT match any
        short-circuit pattern (caller falls through to the normal
        cognition / triage / synth pipeline).

        ``_AGENT_NAME_RE`` and ``_CREATOR_QUESTION_RE`` delegate to
        ``IdentitySkill`` via the registry so the persona block lives in
        ONE place. The other two branches stay inline because they need
        vitals / mood / architect-name that the Skill doesn't see. Skill
        failures fall back to the previous inline templates so this path
        can never regress.

        Callers MUST gate this on the absence of explicit search intent
        (e.g. ``not _SEARCH_INTENT_RE.search(text)``) — the orchestrator
        already does this; identity_responder no longer second-guesses
        the search-intent decision here.
        """
        text = (user_input or "").strip()
        if not text:
            return None

        designation, _codename, creator = extract_identity(soul_block)
        op_name = (self._architect_name or "operator").strip() or "operator"

        if _AGENT_NAME_RE.search(text):
            skill_reply = await self._try_skill(kind="name")
            if skill_reply:
                return f"{skill_reply.rstrip('.')}, {salutation}. Standing by — how can I help?"
            return (
                f"I'm **{designation}**, {salutation}. Standing by — how can I help?"
            )
        if _USER_NAME_RE.search(text):
            return (
                f"Of course, {salutation}. You're **{op_name}** — my Architect for "
                f"this deployment. What can I do for you?"
            )
        if _CREATOR_QUESTION_RE.search(text):
            skill_reply = await self._try_skill(kind="creator")
            if skill_reply:
                return (
                    f"{skill_reply} Each running instance serves one "
                    f"Architect; mine is you, {salutation}."
                )
            creator_str = creator or "the OpenCrayFish project author"
            return (
                f"I was built by **{creator_str}**, the author of the "
                f"OpenCrayFish project. Each running instance serves one "
                f"Architect; mine is you, {salutation}."
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
                f"Doing well, {salutation} — {vitals_summary}, mood feels "
                f"**{mood_label}**. Ready for your next directive."
            )
        return None

    async def _try_skill(self, *, kind: str) -> str | None:
        """Invoke ``IdentitySkill`` via the registry; return its summary or ``None``.

        Centralises the registry call + failure handling so the identity
        shortcut can delegate to a real ``Skill`` without risking a
        regression: any exception, missing registry, or non-OK result
        returns ``None`` and the caller falls back to the inline template.
        """
        if self._skill_registry is None or self._skill_ctx is None:
            return None
        if not self._skill_registry.has("identity"):
            return None
        try:
            result = await self._skill_registry.invoke(
                "identity", self._skill_ctx, kind=kind,
            )
        except Exception:
            log.exception("CHAT identity-skill dispatch failed kind=%s", kind)
            return None
        if not result.ok:
            log.info(
                "CHAT identity-skill kind=%s returned ok=False error=%r",
                kind, (result.error or "")[:120],
            )
            return None
        summary = (result.summary or "").strip()
        return summary or None
