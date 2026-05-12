"""core.emotions — Five-dimension emotional vector (Joy, Anger, Sorrow, Excitement, Calm).

Per README §2 "Resilient Empathy" and PROMPT_ASSEMBLY step 3, the Brain must
inject the *current* emotional vector into every system prompt. Emotions drift
toward a baseline over time (exponential decay — strong stimuli fade quickly,
weak residue lingers); external stimuli (user sentiment, vital stress) nudge
specific channels.

Design notes (the v2 rewrite):
  - Decay is **exponential** (half-life parameterised), not linear-with-snap.
    The previous linear `step=0.05` matched the per-event nudge magnitude,
    which meant any single stimulus was wiped on the very next pulse — i.e.
    emotions effectively did not persist. Now a `+0.15` sorrow takes ~5-7
    pulses (≈3 minutes) to fade.
  - All tuning constants live on `MoodTuning` so brain.py / heartbeat.py read
    a single source of truth instead of sprinkling magic numbers.
  - `nudge_many()` is provided for atomic multi-channel updates (e.g. empathy
    feedback wants to bump sorrow AND drop calm in the same lock window so
    the heartbeat's decay() cannot interleave between them).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Literal, Mapping

log = logging.getLogger(__name__)

Channel = Literal["joy", "anger", "sorrow", "excitement", "calm"]
_CHANNELS: tuple[Channel, ...] = ("joy", "anger", "sorrow", "excitement", "calm")

# Threshold for noteworthy non-baseline activity. When the strongest non-calm
# channel exceeds this, the agent's mood is considered "active" and we want
# the dashboard / log to surface it. Below this it's just baseline noise.
ACTIVE_MOOD_THRESHOLD: float = 0.15


@dataclass(frozen=True)
class EmotionVector:
    joy: float = 0.2
    anger: float = 0.0
    sorrow: float = 0.0
    excitement: float = 0.2
    calm: float = 0.6

    def dominant(self) -> Channel:
        items: list[tuple[Channel, float]] = [
            ("joy", self.joy),
            ("anger", self.anger),
            ("sorrow", self.sorrow),
            ("excitement", self.excitement),
            ("calm", self.calm),
        ]
        return max(items, key=lambda kv: kv[1])[0]

    def dominant_excluding_baseline(self) -> tuple[Channel, float]:
        """Most-active non-calm channel — useful for dashboards.

        `calm` sits at 0.6 baseline so `dominant()` is almost always `calm`.
        This helper surfaces what's actually moving the agent right now.
        """
        items = [
            ("joy", self.joy),
            ("anger", self.anger),
            ("sorrow", self.sorrow),
            ("excitement", self.excitement),
        ]
        return max(items, key=lambda kv: kv[1])  # type: ignore[return-value]

    def describe(self) -> str:
        return (
            f"Mood vector — joy:{self.joy:.2f} anger:{self.anger:.2f} "
            f"sorrow:{self.sorrow:.2f} excitement:{self.excitement:.2f} "
            f"calm:{self.calm:.2f}. Dominant: {self.dominant()}."
        )


@dataclass(frozen=True)
class MoodTuning:
    """Single source of truth for every mood-channel delta.

    Design intent: nudge magnitudes are intentionally LARGER than the per-pulse
    decay, so a stimulus survives several heartbeats before fading. With
    `half_life_pulses=6` (≈3 min at 30 s/pulse), a +0.15 nudge decays to
    ≈+0.075 after 6 pulses and to ≈+0.04 after 12 pulses.
    """

    # Exponential decay: each pulse, channel value moves toward its baseline
    # by a factor of (1 - 2^(-1/half_life)). 6 pulses ≈ 3 minutes half-life.
    half_life_pulses: float = 6.0

    # Per-channel baselines the decay drifts toward.
    baseline: Mapping[Channel, float] = field(
        default_factory=lambda: {
            "joy": 0.2,
            "anger": 0.0,
            "sorrow": 0.0,
            "excitement": 0.2,
            "calm": 0.6,
        }
    )

    # User-sentiment driven nudges (applied atomically via nudge_many).
    user_negative: Mapping[Channel, float] = field(
        default_factory=lambda: {"sorrow": +0.15, "calm": -0.08}
    )
    user_positive: Mapping[Channel, float] = field(
        default_factory=lambda: {"joy": +0.15, "excitement": +0.08}
    )
    user_neutral: Mapping[Channel, float] = field(
        default_factory=lambda: {"calm": +0.02}
    )
    user_urgent: Mapping[Channel, float] = field(
        default_factory=lambda: {"excitement": +0.12, "calm": -0.05}
    )
    # Mixed sentiment (both pos+neg lex hits) — half-magnitude on both sides.
    user_mixed: Mapping[Channel, float] = field(
        default_factory=lambda: {"sorrow": +0.07, "joy": +0.07}
    )

    # Hardware-stress driven nudges (heartbeat path).
    vitals_stress: Mapping[Channel, float] = field(
        default_factory=lambda: {
            "anger": +0.12,
            "sorrow": +0.06,
            "excitement": -0.10,
        }
    )


class Emotions:
    """Asyncio-safe holder for the agent's current 5-D mood."""

    def __init__(self, tuning: MoodTuning | None = None) -> None:
        self._tuning = tuning or MoodTuning()
        # Initialise the vector at the configured baselines so the construct
        # is internally consistent if a caller customised them.
        b = self._tuning.baseline
        self._vec = EmotionVector(
            joy=b.get("joy", 0.2),
            anger=b.get("anger", 0.0),
            sorrow=b.get("sorrow", 0.0),
            excitement=b.get("excitement", 0.2),
            calm=b.get("calm", 0.6),
        )
        self._lock = asyncio.Lock()
        # Track the last-seen "active" channel so we can log transitions only
        # when the agent's emotional surface actually changes — avoids spamming
        # the log every pulse during a slow exponential fade.
        self._last_active_channel: str = "none"
        self._last_active_intensity: float = 0.0

    @property
    def tuning(self) -> MoodTuning:
        return self._tuning

    async def snapshot(self) -> EmotionVector:
        async with self._lock:
            return self._vec

    async def nudge(self, channel: Channel, delta: float) -> EmotionVector:
        """Single-channel nudge. Prefer `nudge_many` for multi-channel events
        so decay() cannot interleave between the writes.
        """
        async with self._lock:
            current = self._vec.__dict__[channel]
            updated = max(0.0, min(1.0, current + delta))
            self._vec = replace(self._vec, **{channel: updated})
            return self._vec

    async def nudge_many(
        self,
        deltas: Mapping[Channel, float],
        *,
        source: str = "unknown",
    ) -> EmotionVector:
        """Atomically apply multiple channel deltas in one lock window.

        `source` is a short label (e.g. "empathy_negative", "vitals_stress")
        used by the structured log so an operator can trace mood movements
        back to their cause in `state/logs/agent.log`.

        Critical for empathy and stress paths that bump several channels at
        once — the previous "await nudge x; await nudge y" pattern allowed
        the heartbeat's decay() to slip between the two awaits and partially
        undo the first write.
        """
        if not deltas:
            return await self.snapshot()
        async with self._lock:
            updates: dict[str, float] = {}
            for ch, d in deltas.items():
                if ch not in _CHANNELS:
                    continue
                cur = self._vec.__dict__[ch]
                updates[ch] = max(0.0, min(1.0, cur + float(d)))
            if not updates:
                return self._vec
            self._vec = replace(self._vec, **updates)
            new = self._vec
        # Log OUTSIDE the lock so a slow handler can't stall other coroutines.
        # Format: MOOD nudge source=<src> deltas=joy:+0.15,calm:-0.08 vec=joy:0.35,...
        delta_str = ",".join(
            f"{k}:{('+' if float(v) >= 0 else '')}{float(v):.2f}"
            for k, v in deltas.items()
        )
        log.info(
            "MOOD nudge source=%s deltas=%s vec=%s dominant=%s active=%s:%.2f",
            source,
            delta_str,
            _vec_short(new),
            new.dominant(),
            *new.dominant_excluding_baseline(),
        )
        return new

    async def decay(self) -> EmotionVector:
        """Exponential drift toward each channel's baseline.

        Called by the heartbeat once per pulse. The fraction of the gap to
        baseline that's closed each pulse is `1 - 2^(-1/half_life)`. This
        means strong stimuli fade fast but small residual stays around
        for several pulses — the desired biological feel.
        """
        async with self._lock:
            t = self._tuning
            half_life = max(0.5, float(t.half_life_pulses))
            keep = 0.5 ** (1.0 / half_life)  # ≈0.89 for half_life=6
            b = t.baseline

            def _drift(val: float, target: float) -> float:
                # `target + (val - target) * keep` — exponential toward target.
                # No snap-to-target: floats settle naturally near baseline.
                return target + (val - target) * keep

            new = EmotionVector(
                joy=_drift(self._vec.joy, b.get("joy", 0.2)),
                anger=_drift(self._vec.anger, b.get("anger", 0.0)),
                sorrow=_drift(self._vec.sorrow, b.get("sorrow", 0.0)),
                excitement=_drift(self._vec.excitement, b.get("excitement", 0.2)),
                calm=_drift(self._vec.calm, b.get("calm", 0.6)),
            )
            self._vec = new
            # Detect transitions of the "active" non-baseline channel so we
            # can log only when the agent's emotional surface meaningfully
            # changes (saves the log from per-pulse decay noise).
            ch, intensity = new.dominant_excluding_baseline()
            now_active = ch if intensity >= ACTIVE_MOOD_THRESHOLD else "none"
            transition_event: tuple[str, str, float] | None = None
            if now_active != self._last_active_channel:
                transition_event = (self._last_active_channel, now_active, intensity)
                self._last_active_channel = now_active
                self._last_active_intensity = intensity
        if transition_event is not None:
            prev, curr, val = transition_event
            log.info(
                "MOOD transition prev=%s -> active=%s intensity=%.2f vec=%s",
                prev,
                curr,
                val,
                _vec_short(new),
            )
        return new


def _vec_short(v: EmotionVector) -> str:
    """Compact one-line representation for log lines.

    e.g. ``joy:0.20,ang:0.00,sor:0.15,exc:0.18,clm:0.55``
    """
    return (
        f"joy:{v.joy:.2f},ang:{v.anger:.2f},sor:{v.sorrow:.2f},"
        f"exc:{v.excitement:.2f},clm:{v.calm:.2f}"
    )
