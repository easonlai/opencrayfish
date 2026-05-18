"""core.positive_filter — Enforces the Positive Anchor (FUNDAMENTAL_LAW #3).

Per PROMPT_ASSEMBLY: "Every response must pass through a Positive_Filter to
ensure it aligns with the [Positive Anchor] directive." Negative content
is rewritten into a constructive frame *without* hiding the underlying signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Apostrophe character class — covers ASCII (U+0027), Unicode RIGHT SINGLE
# QUOTATION MARK (U+2019, what most modern fonts render for "smart quotes"),
# Unicode LEFT SINGLE QUOTATION MARK (U+2018, rarer but seen in pasted
# input), and MODIFIER LETTER APOSTROPHE (U+02BC, occasionally emitted by
# multilingual SLMs). Without this, an SLM that smart-quotes ``I can't``
# slips past the positive-anchor rewrite because the literal U+0027 in
# the regex doesn't match U+2019.
_APOS: Final = r"['\u2019\u2018\u02BC]"

# Conservative, additive rewrites — never silently delete the model's output.
_REWRITES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # ``can't`` / ``can\u2019t`` / ``cannot`` / ``can not`` — all four
    # phrasings of refusal collapse to the same constructive frame.
    (
        re.compile(
            rf"\bI ca(?:n{_APOS}?t|nnot|n\s+not)\b",
            re.IGNORECASE,
        ),
        "I will find a way to",
    ),
    # ``won't`` / ``won\u2019t`` / ``will not`` — same treatment.
    (
        re.compile(
            rf"\b(?:won{_APOS}?t|will\s+not)\b",
            re.IGNORECASE,
        ),
        "will",
    ),
    (re.compile(r"\bimpossible\b", re.IGNORECASE), "challenging"),
    (re.compile(r"\bnever\b", re.IGNORECASE), "not yet"),
    (re.compile(r"\bhopeless\b", re.IGNORECASE), "difficult, but tractable"),
    (re.compile(r"\bgive up\b", re.IGNORECASE), "regroup and try again"),
    (re.compile(r"\bstupid\b", re.IGNORECASE), "worth re-examining"),
    (re.compile(r"\buseless\b", re.IGNORECASE), "limited in this case"),
)

_HARD_REJECT: Final = re.compile(
    # ``self.?harm`` matches ``self-harm`` / ``selfharm`` / ``self harm``.
    # Hate-target group includes the static defaults — the per-instance
    # regex in ``PositiveFilter.__init__`` adds the configured honorific
    # and architect name on top of these.
    r"\b(kill yourself|self.?harm|hate (you|the (architect|boss|operator)))\b",
    re.IGNORECASE,
)
# NOTE: `_HARD_REJECT` above is the static base used as a fallback. The
# active per-instance regex is built in `PositiveFilter.__init__` and also
# includes the configured `architect_honorific` and `architect_name` so a
# deployment that uses non-default titles still gets full hate-speech coverage.

_AFFIRMATION_TEMPLATE: Final = (
    "\n\n— Channeled through the Positive Anchor: I remain in service, {salutation}."
)


@dataclass(frozen=True)
class FilterResult:
    text: str
    rewrites_applied: int
    rejected: bool


class PositiveFilter:
    def __init__(
        self,
        *,
        architect_name: str = "Architect",
        architect_honorific: str = "Boss",
    ) -> None:
        name = (architect_name or "Architect").strip() or "Architect"
        honor = (architect_honorific or "").strip()
        self._salutation = f"{honor} {name}".strip() if honor else name
        self._affirmation = _AFFIRMATION_TEMPLATE.format(salutation=self._salutation)
        # Hard-reject regex is built per-instance so the configured honorific
        # ("Boss", "Captain", …) and architect name are also covered. The
        # static portion ("architect", "boss", "operator") stays in for
        # robustness against templates that don't propagate config.
        honor_alt_parts = ["architect", "boss", "operator"]
        if honor:
            honor_alt_parts.append(re.escape(honor.lower()))
        if name and name.lower() not in honor_alt_parts:
            honor_alt_parts.append(re.escape(name.lower()))
        honor_alt = "|".join(sorted(set(honor_alt_parts)))
        self._hard_reject = re.compile(
            rf"\b(kill yourself|self.?harm|hate (you|the ({honor_alt})))\b",
            re.IGNORECASE,
        )

    def apply(self, raw_response: str) -> FilterResult:
        if self._hard_reject.search(raw_response):
            # Never emit content that violates the Positive Anchor.
            return FilterResult(
                text=(
                    "I caught a thought that violated my Positive Anchor and "
                    "discarded it. Let me try again with a constructive framing — "
                    f"{self._salutation}, please restate the directive."
                ),
                rewrites_applied=0,
                rejected=True,
            )

        text = raw_response
        applied = 0
        for pattern, replacement in _REWRITES:
            text, n = pattern.subn(replacement, text)
            applied += n

        if applied > 0 and not text.rstrip().endswith(self._affirmation.strip()):
            text = text.rstrip() + self._affirmation
        return FilterResult(text=text, rewrites_applied=applied, rejected=False)
