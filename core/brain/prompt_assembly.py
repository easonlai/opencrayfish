"""Prompt-assembly formatters (pure, side-effect free).

This module owns the THREE templates whose output flows into the SLM:

  * ``assemble_system_prompt`` \u2014 the full per-turn system prompt
    (soul + operator + physical + mood + empathy + knowledge + task).
    Called once per ``Brain._cycle``.
  * ``format_task_block``       \u2014 the "## THIS TURN" payload (user
    message + few-shot operator anchor, OR heartbeat mission, OR idle
    pulse). Assembled separately so the orchestrator can vary it per
    turn type without re-running the whole assembly.
  * ``build_minimal_retry_prompt`` \u2014 the stripped-down recovery
    prompt fired after ``_looks_like_prompt_leak`` flags the first
    synthesis output. Deliberately omits scaffolding the small SLM
    loves to echo verbatim.

Design rules for future maintainers (read before adding rules):

  * The IDENTITY of the agent is asserted EXACTLY ONCE \u2014 in the soul
    block (which carries ``**Designation**:`` injected from
    ``cfg.system.individual_designation``). Do NOT add a duplicate
    identity line above the soul block; small SLMs treat duplication
    as emphasis and start echoing the phrasing.
  * Per-deployment context (operator name + salutation) is asserted
    in the OPERATOR block, in FIRST-PERSON framing the model can
    adopt verbatim into the assistant role.
  * Avoid negative imperatives ("NEVER do X", "do NOT do X"). On
    small models they often drop the negation and retain the noun.
    Prefer positive few-shot anchors instead ("Operator: hi.  You: hi,
    <salutation>!").
  * Codename-vs-name disambiguation is CONDITIONAL \u2014 only included
    when the operator's message mentions the codename (see
    ``user_mentions_codename``).

These functions are pure: no I/O, no SLM calls, no state. They are
trivially unit-testable in isolation \u2014 the orchestrator passes in
everything they need as keyword arguments.
"""
from __future__ import annotations

import re

from .identity_responder import extract_identity


def user_mentions_codename(user_input: str | None, codename: str) -> bool:
    """True if the operator's message references the framework codename.

    Word-boundary, case-insensitive substring check. Used by
    ``assemble_system_prompt`` to decide whether to include the
    codename-vs-name disambiguation clause. Keeping this conditional
    avoids poisoning every turn with a rule that only matters when the
    operator brings the codename up.
    """
    if not user_input or not codename:
        return False
    pattern = rf"\b{re.escape(codename)}\b"
    return re.search(pattern, user_input, re.IGNORECASE) is not None


def assemble_system_prompt(
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
    """Compose the full per-turn system prompt.

    See module docstring for the design rules. The order of blocks here
    matters \u2014 ``KNOWLEDGE`` immediately precedes ``THIS TURN`` so the
    SLM's most-recent attention sits on the reference material when it
    generates the reply. Do not reorder without re-running the prompt-leak
    regression in ``scripts/smoke_cognition_dispatch.py``.
    """
    designation, codename, _creator = extract_identity(soul_block)
    salutation = (
        f"{architect_honorific} {architect_name}".strip()
        if architect_honorific
        else (architect_name or "operator")
    )
    operator_block = (
        f"You are speaking with {architect_name}. Address {architect_name} "
        f"as \"{salutation}\" in your replies, or simply by name."
    )
    # Conditional codename disambiguation \u2014 only when the user actually
    # mentioned the codename. Phrased as a positive instruction (one line)
    # rather than a wall of negatives.
    if user_mentions_codename(user_input, codename):
        operator_block += (
            f"\n\nNote: the operator just mentioned \"{codename}\". That is "
            f"the software project you run on, not your name. When asked "
            f"who you are, answer with your own name ({designation}); "
            f"mention {codename} only as the project, never as a name."
        )
    return (
        "## SOUL CONTEXT (READ-ONLY \u2014 your identity, laws, and persona)\n"
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


def format_task_block(
    *,
    user_input: str | None,
    mission: str | None,
    salutation: str,
    web_searched: bool = False,
) -> str:
    """Build the per-turn "## THIS TURN" payload.

    For user turns, uses a tiny positive few-shot anchor instead of a
    rules list. Small SLMs follow examples better than negative
    imperatives. The previous version's "NEVER address the operator by
    your own name / NEVER end with your own name as a sign-off" rules
    were a known anti-pattern \u2014 the model would echo the very nouns
    the rules were trying to prohibit.

    ``web_searched`` is retained for API compatibility but no longer
    appends a scaffolding suffix \u2014 the Cognitive Loop's KNOWLEDGE
    block header (\"Live SearXNG results (use these as the primary
    source of truth)\" or similar) already conveys both the evidence
    AND the framing. The previous suffix was redundant scaffolding and
    triggered 2 prompt-leak retries in a 1 h field run because the 1.5B
    SLM echoed it verbatim instead of synthesising.

    ``salutation`` is built by ``Brain._salutation()`` so this function
    needs no awareness of the architect-honorific config plumbing.
    """
    # Suffix intentionally removed \u2014 see docstring. `web_searched` is
    # still accepted so callers don't break; the signal is carried by
    # `ThoughtTrace.web_searched` for downstream telemetry.
    _ = web_searched  # silence unused-arg linters
    if user_input is not None:
        return (
            f"Operator ({salutation}): {user_input.strip()}\n"
            f"You:"
        )
    if mission is not None:
        return f"Heartbeat-triggered mission: {mission.strip()}"
    return "Idle pulse \u2014 produce a brief situational reflection."


def build_minimal_retry_prompt(
    *,
    user_input: str | None,
    mission: str | None,
    knowledge: str,
) -> str:
    """Construct a stripped-down system prompt for leak-recovery retry.

    Used after ``_looks_like_prompt_leak`` flags the first synthesis
    output. Deliberately omits soul/mood/empathy/section-header
    scaffolding and the imperative directives that small SLMs love to
    echo verbatim. Just the reference material and the question, in
    plain prose.
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
