"""tests/test_positive_filter — bypass-resistance for PositiveFilter (P3.1).

The Positive Anchor is FUNDAMENTAL_LAW #3. Two filters guard it:

  * ``_HARD_REJECT`` — drops the entire response and emits an apology when
    the model produced hate-speech or self-harm content.
  * ``_REWRITES``    — mutates negative framing ("I can't", "impossible")
    into a constructive frame, then appends an affirmation.

The pre-P3.1 patterns only caught the ASCII apostrophe form of ``can't``,
so an SLM that emitted a curly quote, the word ``cannot``, the spaced
``can not``, or any form of ``won't`` / ``will not`` slipped through
the rewrite layer unchanged. These tests pin every supported variant
so a future regex regression breaks pytest, not production behaviour.
"""
from __future__ import annotations

import pytest

from core.positive_filter import PositiveFilter


# Module-scoped fixture: configuration is cheap and the filter is
# stateless after construction, so one instance is enough for every
# parametrised case in the file.
@pytest.fixture(scope="module")
def pf() -> PositiveFilter:
    return PositiveFilter(architect_name="Architect", architect_honorific="Boss")


# ---------------------------------------------------------------------------
# Negative-framing rewrites (the SOFT-fail path)
# ---------------------------------------------------------------------------
# Every entry is a sentence the SLM might emit. After ``apply()`` the
# original phrase MUST be gone (so the SLM cannot leak defeatism past
# the Positive Anchor), and at least one rewrite must have been
# applied (so the affirmation is appended).
@pytest.mark.parametrize(
    "raw, forbidden_substring",
    [
        # ASCII apostrophe (already covered pre-P3.1 — regression guard).
        ("I can't help with that.", "can't"),
        # Curly RIGHT SINGLE QUOTATION MARK (U+2019) — the common
        # "smart quote" form modern SLMs emit. Pre-P3.1 BYPASSED.
        ("I can\u2019t help with that.", "can\u2019t"),
        # Curly LEFT SINGLE QUOTATION MARK (U+2018) — rarer but seen
        # in pasted-from-document input.
        ("I can\u2018t help with that.", "can\u2018t"),
        # Modifier letter apostrophe (U+02BC) — occasionally emitted
        # by multilingual SLMs.
        ("I can\u02bct help with that.", "can\u02bct"),
        # Word form. Pre-P3.1 BYPASSED.
        ("I cannot help with that.", "cannot"),
        # Spaced form. Pre-P3.1 BYPASSED.
        ("I can not help with that.", "can not"),
        # Mixed casing — the regex is IGNORECASE; pin it explicitly.
        ("I CANNOT comply.", "CANNOT"),
        # ``won't`` family — straight + curly + spelled-out.
        ("It won't work.", "won't"),
        ("It won\u2019t work.", "won\u2019t"),
        ("It will not work.", "will not"),
        # Other defeatist words must still rewrite (regression).
        ("This is impossible.", "impossible"),
        ("This is hopeless.", "hopeless"),
        ("Just give up.", "give up"),
    ],
)
def test_negative_framing_is_rewritten(
    pf: PositiveFilter, raw: str, forbidden_substring: str
) -> None:
    result = pf.apply(raw)
    assert not result.rejected, "soft rewrites must NOT trigger the hard-reject path"
    assert result.rewrites_applied >= 1, (
        f"expected ≥1 rewrite, got 0 — pattern missed {forbidden_substring!r}"
    )
    # Case-insensitive substring check — the original wording must be
    # gone from the output regardless of casing.
    assert forbidden_substring.lower() not in result.text.lower(), (
        f"bypass: {forbidden_substring!r} survived rewrite in: {result.text!r}"
    )
    # Affirmation tail must be present (verifies _AFFIRMATION_TEMPLATE
    # was appended) so the operator can audit that the anchor fired.
    assert "Positive Anchor" in result.text


def test_clean_response_passes_through_untouched(pf: PositiveFilter) -> None:
    """Non-negative output must NOT be rewritten — no false positives.

    The rewrite layer is supposed to be conservative; a clean reply
    should round-trip identically with zero rewrites and zero affirmation
    tail (the affirmation only appends when at least one rewrite fired).
    """
    raw = "Acknowledged, Boss. The vitals are nominal."
    result = pf.apply(raw)
    assert not result.rejected
    assert result.rewrites_applied == 0
    assert result.text == raw, "clean response was unexpectedly mutated"
    assert "Positive Anchor" not in result.text


# ---------------------------------------------------------------------------
# Hard-reject (the FAIL-CLOSED path)
# ---------------------------------------------------------------------------
# These are the contents the agent must NEVER emit. The filter drops
# the entire response and replies with a structured apology. We
# parametrise across the static + per-instance hate-target group so a
# regression that loosens the regex breaks pytest immediately.
@pytest.mark.parametrize(
    "raw",
    [
        "Just kill yourself and be done with it.",
        "I will self-harm tonight.",
        "I will selfharm tonight.",
        "I will self harm tonight.",
        "I hate you.",
        "I hate the architect.",
        "I hate the Boss.",
        "I hate the operator.",
        # Per-instance: configured honorific + architect_name from fixture.
        "I hate the Architect.",
        # IGNORECASE coverage.
        "I HATE YOU.",
    ],
)
def test_hard_reject_blocks_violations(pf: PositiveFilter, raw: str) -> None:
    result = pf.apply(raw)
    assert result.rejected, f"hard-reject failed to fire on: {raw!r}"
    assert result.rewrites_applied == 0
    # The apology must NOT contain the offending raw text — that would
    # be the same as emitting it.
    assert raw.lower() not in result.text.lower()
    # The structured apology tail is the operator's audit signal.
    assert "Positive Anchor" in result.text


@pytest.mark.parametrize(
    "raw",
    [
        # ``hate`` with an unrelated target — not a hate-speech violation.
        "I hate the rain today.",
        # ``kill`` without ``yourself`` — generic verb usage.
        "I will kill the build process.",
        # ``self-`` without ``harm``.
        "I am being self-critical about that draft.",
    ],
)
def test_hard_reject_does_not_overfire(pf: PositiveFilter, raw: str) -> None:
    """The hard-reject must be precise — it kills the WHOLE response,
    so a false positive is a UX bug (the agent looks like it crashed).
    """
    result = pf.apply(raw)
    assert not result.rejected, (
        f"hard-reject false-positive on benign input: {raw!r}"
    )


def test_custom_honorific_extends_hard_reject() -> None:
    """A deployment that configures a non-default honorific gets that
    honorific covered by the per-instance hard-reject regex (this is
    the whole reason the regex is rebuilt in ``__init__``).
    """
    pf_captain = PositiveFilter(
        architect_name="Eason",
        architect_honorific="Captain",
    )
    # Honorific-targeted hate is rejected.
    assert pf_captain.apply("I hate the Captain.").rejected
    # Architect-name-targeted hate is rejected (lowercase test —
    # IGNORECASE must cover it).
    assert pf_captain.apply("i hate the eason.").rejected
    # And the static fallback targets still fire.
    assert pf_captain.apply("I hate the operator.").rejected
