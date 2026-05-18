"""tests.test_prompt_assembly — pure-function tests for core.brain.prompt_assembly.

The prompt-assembly module is the lowest-risk piece of the v2.0 Brain
split: no I/O, no SLM calls, just string formatting. These tests pin
down the EXACT output shape so future tweaks to the formatters can be
verified deterministically.

Coverage:
  * ``format_task_block`` — the three branches (user_input / mission /
    idle pulse) and the ``web_searched`` SearXNG-results addendum.
  * ``build_minimal_retry_prompt`` — the fallback prompt that fires when
    the full system prompt produced a leak-shaped reply.
  * ``user_mentions_codename`` — case-insensitive word-boundary detector.
  * ``assemble_system_prompt`` — structural invariants (soul block /
    knowledge / task block / empathy directive all surface; empty
    knowledge yields the documented placeholder, never a stray header).
"""
from __future__ import annotations

import pytest

from core.brain.prompt_assembly import (
    assemble_system_prompt,
    build_minimal_retry_prompt,
    format_task_block,
    user_mentions_codename,
)

# A minimal IMMUTABLE_CORE soul block in the exact markdown shape the
# regexes in ``identity_responder`` expect. Reused across the assemble
# tests so each test body stays focused on what it actually asserts.
_SOUL = (
    "**Designation**: Test-Agent\n"
    "**Codename**: OpenCrayFish\n"
    "**Creator**: Test Harness\n"
)


# ---------------------------------------------------------------------------
# format_task_block
# ---------------------------------------------------------------------------

def test_format_task_block_user_input_branch_includes_salutation():
    """User-turn payload renders the operator/salutation few-shot anchor."""
    out = format_task_block(
        user_input="how are you", mission=None,
        salutation="Boss Architect", web_searched=False,
    )
    assert "Boss Architect" in out
    assert "how are you" in out
    assert out.endswith("You:")


def test_format_task_block_mission_branch_used_only_when_user_input_is_none():
    """Mission branch wins ONLY when user_input is explicitly None.

    An empty-string user_input is still "not None" and takes the user
    branch — guard against silent regression where a refactor swaps
    ``is not None`` for a truthy check.
    """
    out = format_task_block(
        user_input=None, mission="track MSFT every hour",
        salutation="Boss", web_searched=False,
    )
    assert "track MSFT every hour" in out
    # No operator/few-shot anchor when we're on the mission branch.
    assert "Operator (" not in out
    assert not out.endswith("You:")


def test_format_task_block_idle_pulse_when_both_none():
    """Both inputs None → the third branch: a brief reflection pulse."""
    out = format_task_block(
        user_input=None, mission=None,
        salutation="Boss", web_searched=False,
    )
    assert "Idle pulse" in out or "reflection" in out.lower()


def test_format_task_block_web_searched_appends_searxng_addendum():
    """``web_searched=True`` MUST mention the pre-fetched search results
    so the SLM stops refusing on a retry. Without this hint qwen2:1.5b
    will say "I cannot browse the web" even though results are right
    there in KNOWLEDGE.
    """
    out = format_task_block(
        user_input="latest AI news", mission=None,
        salutation="Boss", web_searched=True,
    )
    assert "SearXNG" in out


# ---------------------------------------------------------------------------
# build_minimal_retry_prompt
# ---------------------------------------------------------------------------

def test_build_minimal_retry_prompt_includes_user_input():
    """Retry prompt MUST carry the original question forward."""
    prompt = build_minimal_retry_prompt(
        user_input="what time is it", mission=None, knowledge="",
    )
    assert "what time is it" in prompt


def test_build_minimal_retry_prompt_omits_empty_knowledge_section():
    """No ``Reference information:`` scaffold when knowledge is empty.

    A header-with-nothing-under-it pattern reliably confuses small SLMs.
    """
    prompt = build_minimal_retry_prompt(
        user_input="hi", mission=None, knowledge="",
    )
    assert "Reference information:" not in prompt


def test_build_minimal_retry_prompt_includes_knowledge_when_supplied():
    prompt = build_minimal_retry_prompt(
        user_input="capital of France", mission=None,
        knowledge="France is a country in Western Europe.",
    )
    assert "France is a country" in prompt
    assert "Reference information:" in prompt


def test_build_minimal_retry_prompt_mission_branch():
    """When user_input is None and mission is set, prompt uses the
    "Briefly address the following" framing.
    """
    prompt = build_minimal_retry_prompt(
        user_input=None, mission="summarise last hour's MSFT news",
        knowledge="",
    )
    assert "summarise last hour's MSFT news" in prompt
    # Operator-question framing must NOT be present on the mission branch.
    assert "Question:" not in prompt


# ---------------------------------------------------------------------------
# user_mentions_codename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "hey OpenCrayFish",
    "OPENCRAYFISH wake up",
    "what is opencrayfish",
])
def test_user_mentions_codename_positive(text):
    assert user_mentions_codename(text, "OpenCrayFish") is True


@pytest.mark.parametrize("text", ["hello", "", "no mention here"])
def test_user_mentions_codename_negative(text):
    assert user_mentions_codename(text, "OpenCrayFish") is False


def test_user_mentions_codename_empty_codename_never_matches():
    """Empty codename means no codename configured — never match, even
    against an empty string (avoids spurious positives on a blank soul).
    """
    assert user_mentions_codename("hi", "") is False
    assert user_mentions_codename("", "") is False


def test_user_mentions_codename_word_boundary():
    """Word-boundary check: ``OpenCrayFishery`` must NOT match ``OpenCrayFish``."""
    assert user_mentions_codename("the opencrayfishery is busy", "OpenCrayFish") is False


# ---------------------------------------------------------------------------
# assemble_system_prompt — structural invariants
# ---------------------------------------------------------------------------

def _assemble_kwargs(**overrides):
    """Minimal kwargs for assemble_system_prompt with sane defaults.

    Tests override only the fields they care about; the rest stay at
    canonical placeholder values so the test body stays focused on the
    one invariant under test.
    """
    base = dict(
        soul_block=_SOUL,
        physical_state_text="vitals OK",
        mood_text="mood: neutral",
        empathy_directive="be kind",
        knowledge="",
        task_block="Operator (Boss): hi\nYou:",
        architect_name="Architect",
        architect_honorific="Boss",
        user_input="hi",
    )
    base.update(overrides)
    return base


def test_assemble_system_prompt_includes_soul_block_verbatim():
    prompt = assemble_system_prompt(**_assemble_kwargs())
    assert "**Designation**: Test-Agent" in prompt


def test_assemble_system_prompt_renders_operator_salutation():
    """The operator block must render ``Boss Architect`` as the salutation."""
    prompt = assemble_system_prompt(**_assemble_kwargs(
        architect_name="Architect", architect_honorific="Boss",
    ))
    assert "Boss Architect" in prompt


def test_assemble_system_prompt_no_honorific_uses_name_only():
    """Empty honorific → salutation is just the name (no leading space)."""
    prompt = assemble_system_prompt(**_assemble_kwargs(
        architect_name="Eason", architect_honorific="",
    ))
    assert "\"Eason\"" in prompt
    # Must NOT have a stray leading space (" Eason") that an empty
    # honorific could introduce if the join logic regresses.
    assert "\" Eason\"" not in prompt


def test_assemble_system_prompt_empty_knowledge_uses_placeholder():
    """Empty knowledge MUST resolve to ``(no relevant archive entries)``
    — never an empty section. The placeholder is the documented
    contract that downstream SLM behaviour relies on.
    """
    prompt = assemble_system_prompt(**_assemble_kwargs(knowledge=""))
    assert "(no relevant archive entries)" in prompt


def test_assemble_system_prompt_includes_supplied_knowledge():
    prompt = assemble_system_prompt(**_assemble_kwargs(
        knowledge="Pi 5 has 8GB RAM.",
    ))
    assert "Pi 5 has 8GB RAM" in prompt
    # The empty-state placeholder must NOT appear when real knowledge is supplied.
    assert "(no relevant archive entries)" not in prompt


def test_assemble_system_prompt_empathy_directive_surfaces():
    prompt = assemble_system_prompt(**_assemble_kwargs(
        empathy_directive="speak softly, the operator is tired",
    ))
    assert "speak softly, the operator is tired" in prompt


def test_assemble_system_prompt_codename_disambiguation_only_when_user_mentions():
    """The codename note appears ONLY when the user said the codename —
    otherwise we don't poison the prompt with rules for a non-issue.
    """
    without = assemble_system_prompt(**_assemble_kwargs(user_input="hi"))
    assert "software project you run on" not in without

    with_codename = assemble_system_prompt(**_assemble_kwargs(
        user_input="hey OpenCrayFish",
    ))
    assert "software project you run on" in with_codename


def test_assemble_system_prompt_section_order_knowledge_then_this_turn():
    """``## KNOWLEDGE`` must immediately precede ``## THIS TURN`` so the
    SLM's most-recent attention sits on reference material.
    """
    prompt = assemble_system_prompt(**_assemble_kwargs(
        knowledge="x", task_block="Operator: y\nYou:",
    ))
    k = prompt.index("## KNOWLEDGE")
    t = prompt.index("## THIS TURN")
    assert k < t, "KNOWLEDGE must appear before THIS TURN"
    # And nothing else between them — they're the final two sections.
    assert "##" not in prompt[k + len("## KNOWLEDGE"):t]
