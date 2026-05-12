"""core.empathy — User-sentiment analyzer (PROMPT_ASSEMBLY step 4).

Lightweight, dependency-free heuristic. Real production deployments may swap
this for an SLM call, but the contract returns a single empathy directive
string injected into the system prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

_NEGATIVE_LEX: Final = {
    "tired", "stressed", "angry", "sad", "frustrated", "broken", "fail",
    "hate", "worried", "anxious", "overwhelmed", "exhausted", "lonely",
    "累", "煩", "氣", "傷心", "失敗", "焦慮",
}
_POSITIVE_LEX: Final = {
    "happy", "great", "love", "excited", "proud", "grateful", "wonderful",
    "thank", "thanks", "thx", "amazing", "awesome", "appreciate", "appreciated",
    "開心", "謝謝", "棒", "讚", "興奮",
}
_URGENT_LEX: Final = {"urgent", "now", "asap", "immediately", "emergency", "critical", "立即", "緊急"}


@dataclass(frozen=True)
class EmpathyReading:
    sentiment: str        # positive | negative | neutral | mixed
    urgency: bool
    directive: str        # Natural-language guidance for the Brain.


class EmpathyEngine:
    """Resilient Empathy: surfaces user state without colouring the response."""

    def analyze(self, user_text: str) -> EmpathyReading:
        text = user_text.lower()
        tokens = set(_tokenize(text))

        neg_hits = len(tokens & _NEGATIVE_LEX)
        pos_hits = len(tokens & _POSITIVE_LEX)
        urgent = bool(tokens & _URGENT_LEX)

        # Score-based classification: lets us distinguish "I'm tired but thanks"
        # (mixed) from "I'm tired" (negative). The previous boolean AND/NOT
        # logic collapsed any pos+neg co-occurrence into neutral, dropping the
        # empathy signal entirely.
        if neg_hits > pos_hits:
            sentiment = "negative"
            directive = (
                "The Architect appears stressed or burdened. Be more supportive, "
                "gentle, and offer concrete next steps."
            )
        elif pos_hits > neg_hits:
            sentiment = "positive"
            directive = (
                "The Architect is in good spirits. Match the energy while "
                "remaining respectful and concise."
            )
        elif neg_hits > 0 and pos_hits > 0:
            # Equal hits on both sides — genuine mixed feelings.
            sentiment = "mixed"
            directive = (
                "The Architect's tone is mixed: there are both burdens and "
                "bright spots. Acknowledge both gently before answering."
            )
        else:
            sentiment = "neutral"
            directive = (
                "Sentiment is neutral. Default to a logical, respectful, and "
                "empathetically resonant tone."
            )

        if urgent:
            directive += " Treat this as time-critical; lead with the answer."

        return EmpathyReading(sentiment=sentiment, urgency=urgent, directive=directive)


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf.clear()
            # Treat each CJK char as its own token.
            if "\u4e00" <= ch <= "\u9fff":
                out.append(ch)
    if buf:
        out.append("".join(buf))
    return out
