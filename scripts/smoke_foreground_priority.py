"""Smoke test: foreground-priority signalling between Brain and Heartbeat.

Verifies the Architect-priority mechanism added to give live `think()`
cycles precedence over background autonomous research:

  1. `Brain.is_foreground_busy()` is False at construction, True while
     `think()` is in flight, False again after it returns (success path).
  2. `Brain.is_foreground_busy()` is also False after `think()` raises
     (the finally-block must clear the counter on exceptions too).
  3. Concurrent `think()` calls increment the depth counter so the
     foreground signal stays asserted until ALL of them finish —
     critical when Telegram + Web Chat both have a live turn.
  4. `Heartbeat._yield_to_foreground()` returns False when Brain is
     idle and True when Brain is busy, emitting the documented log
     line so operators can see the yield in `state/logs/agent.log`.
  5. The proactive cycle bails OUT at the `topic_selection` checkpoint
     when foreground becomes busy BEFORE the cycle starts (no work
     wasted).

The test uses stubs only — no real SLM, SearXNG, or filesystem state.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal Provider surface Brain.__init__ inspects."""
    is_tripped = False
    active_backend = "stub"

    async def generate(self, system_prompt: str, messages: list) -> str:  # pragma: no cover
        return ""


# ---------------------------------------------------------------------------
# Test 1-3: Brain.is_foreground_busy + depth counter
# ---------------------------------------------------------------------------


async def test_brain_foreground_lifecycle() -> None:
    """think() sets the flag on entry, clears it on exit (success + error)."""
    from core.brain import Brain, ThoughtTrace
    from core.empathy import EmpathyReading
    from core.monitor import VitalSigns
    from core.positive_filter import FilterResult

    # Build a Brain with all stub dependencies. We monkey-patch _cycle to
    # avoid wiring the entire prompt-assembly pipeline.
    brain = Brain.__new__(Brain)
    brain._foreground_depth = 0
    brain._inflight = set()
    brain._reflection_enabled = False  # skip reflection fire-and-forget

    # Synthetic trace returned by the stub _cycle.
    def _make_trace() -> ThoughtTrace:
        return ThoughtTrace(
            system_prompt="",
            raw_response="ok",
            filtered=FilterResult(text="ok", rewrites_applied=0, rejected=False),
            vitals=VitalSigns(
                cpu_percent=0.0, ram_percent=0.0, temperature_c=None,
                is_stressed=False, brain_online=True, brain_backend="stub",
                brain_last_error=None, brain_recovery_seconds=None,
            ),
            empathy=EmpathyReading(sentiment="neutral", urgency=False, directive=""),
            backend="stub",
        )

    # --- 1. Idle at construction.
    assert not brain.is_foreground_busy(), \
        "Brain should not be foreground-busy before any think() call."

    # --- 2. Busy during think(), idle after.
    in_flight_seen = asyncio.Event()
    release = asyncio.Event()

    async def _slow_cycle(*, user_input, mission):  # noqa: ARG001
        in_flight_seen.set()
        await release.wait()
        return _make_trace()

    brain._cycle = _slow_cycle  # type: ignore[assignment]
    task = asyncio.create_task(brain.think("hi"))
    await in_flight_seen.wait()
    assert brain.is_foreground_busy(), \
        "Brain should be foreground-busy while think() is in flight."
    release.set()
    await task
    assert not brain.is_foreground_busy(), \
        "Brain should clear foreground-busy after think() completes."

    # --- 3. Busy cleared even when _cycle raises.
    async def _failing_cycle(*, user_input, mission):  # noqa: ARG001
        raise RuntimeError("simulated cognition failure")

    brain._cycle = _failing_cycle  # type: ignore[assignment]
    try:
        await brain.think("hi")
    except RuntimeError:
        pass
    assert not brain.is_foreground_busy(), \
        "Brain must clear foreground-busy even when think() raises."

    print("  [1] Brain.is_foreground_busy() lifecycle PASS")


async def test_brain_concurrent_depth() -> None:
    """Two concurrent think() calls keep foreground asserted until BOTH finish."""
    from core.brain import Brain, ThoughtTrace
    from core.empathy import EmpathyReading
    from core.monitor import VitalSigns
    from core.positive_filter import FilterResult

    brain = Brain.__new__(Brain)
    brain._foreground_depth = 0
    brain._inflight = set()
    brain._reflection_enabled = False

    def _make_trace() -> ThoughtTrace:
        return ThoughtTrace(
            system_prompt="",
            raw_response="ok",
            filtered=FilterResult(text="ok", rewrites_applied=0, rejected=False),
            vitals=VitalSigns(
                cpu_percent=0.0, ram_percent=0.0, temperature_c=None,
                is_stressed=False, brain_online=True, brain_backend="stub",
                brain_last_error=None, brain_recovery_seconds=None,
            ),
            empathy=EmpathyReading(sentiment="neutral", urgency=False, directive=""),
            backend="stub",
        )

    release_a = asyncio.Event()
    release_b = asyncio.Event()
    started_a = asyncio.Event()
    started_b = asyncio.Event()

    async def _cycle(*, user_input, mission):  # noqa: ARG001
        if user_input == "A":
            started_a.set()
            await release_a.wait()
        else:
            started_b.set()
            await release_b.wait()
        return _make_trace()

    brain._cycle = _cycle  # type: ignore[assignment]

    t_a = asyncio.create_task(brain.think("A"))
    t_b = asyncio.create_task(brain.think("B"))
    await started_a.wait()
    await started_b.wait()
    assert brain.is_foreground_busy(), "Both think() calls in flight → busy."
    # Depth must reflect both — we don't expose it, so we infer by releasing
    # only one and checking the flag is still True.
    release_a.set()
    await t_a
    assert brain.is_foreground_busy(), \
        "After one of two think() calls completes, foreground must remain busy."
    release_b.set()
    await t_b
    assert not brain.is_foreground_busy(), \
        "After both think() calls complete, foreground must clear."

    print("  [2] Brain depth counter handles concurrent think() PASS")


# ---------------------------------------------------------------------------
# Test 4: Heartbeat._yield_to_foreground emits log and returns correct bool
# ---------------------------------------------------------------------------


async def test_heartbeat_yield_helper(caplog_records: list[logging.LogRecord]) -> None:
    """_yield_to_foreground returns the brain's busy flag and logs on yield."""
    from core.heartbeat import Heartbeat

    hb = Heartbeat.__new__(Heartbeat)
    hb._brain = MagicMock()

    # --- Idle brain → False, no log line.
    hb._brain.is_foreground_busy.return_value = False
    pre_count = sum(
        1 for r in caplog_records if "PROACTIVE yield_to_foreground" in r.getMessage()
    )
    assert hb._yield_to_foreground("topic_selection") is False, \
        "Idle brain → _yield_to_foreground must return False."
    post_count = sum(
        1 for r in caplog_records if "PROACTIVE yield_to_foreground" in r.getMessage()
    )
    assert post_count == pre_count, \
        "Idle brain → no yield log line should be emitted."

    # --- Busy brain → True, log line emitted with stage + topic.
    hb._brain.is_foreground_busy.return_value = True
    assert hb._yield_to_foreground("pre_search", topic="hailo benchmarks") is True, \
        "Busy brain → _yield_to_foreground must return True."
    yield_msgs = [
        r.getMessage() for r in caplog_records
        if "PROACTIVE yield_to_foreground" in r.getMessage()
    ]
    assert any(
        "stage=pre_search" in m and "hailo benchmarks" in m for m in yield_msgs
    ), f"Expected yield log with stage+topic, got: {yield_msgs}"

    print("  [3] Heartbeat._yield_to_foreground helper PASS")


# ---------------------------------------------------------------------------
# Test 5: _proactive_research bails at topic_selection when foreground busy
# ---------------------------------------------------------------------------


async def test_proactive_bails_when_foreground_busy() -> None:
    """If brain is busy before the cycle starts, no SLM work happens."""
    from core.heartbeat import Heartbeat

    hb = Heartbeat.__new__(Heartbeat)
    hb._brain = MagicMock()
    hb._brain.is_foreground_busy.return_value = True
    # Sentinels that MUST NOT be called once we bail early.
    hb._select_research_topic = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("topic selection must NOT run when yielding")
    )
    hb._skill_registry = MagicMock()
    hb._skill_registry.invoke = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("skill invoke must NOT run when yielding")
    )

    result = await hb._proactive_research(topic_override=None)
    assert result is None, "Yielded cycle must return None."
    print("  [4] _proactive_research bails at topic_selection when busy PASS")


# ---------------------------------------------------------------------------
# Test 6: Manual /research bypass — topic_override skips the yield
# ---------------------------------------------------------------------------


async def test_manual_research_does_not_yield() -> None:
    """Operator-initiated `/research [topic]` must run even if foreground busy."""
    from core.heartbeat import Heartbeat
    from core.skills.base import SkillResult

    hb = Heartbeat.__new__(Heartbeat)
    hb._brain = MagicMock()
    hb._brain.is_foreground_busy.return_value = True  # would normally yield
    # We expect the cycle to PROGRESS past topic_selection (because override
    # is provided) and past the pre_search yield (which is skipped when
    # topic_override is set), then call the skill registry.
    hb._skill_registry = MagicMock()
    hb._skill_ctx = MagicMock()
    invoke_called = asyncio.Event()

    async def _fake_invoke(*args, **kwargs):  # noqa: ANN001
        invoke_called.set()
        # Return a failed search so the cycle bails before SLM work
        # (we just need to prove invoke was REACHED).
        return SkillResult(ok=False, evidence=[], error="search-stub-fail")

    hb._skill_registry.invoke = _fake_invoke

    result = await hb._proactive_research(topic_override="manual probe")
    assert invoke_called.is_set(), \
        "Manual /research must NOT yield even when Architect is busy."
    assert result is None, "Search failure still returns None — that's fine."
    print("  [5] Manual /research bypasses the yield PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def _main() -> None:
    # Capture log records emitted by core.heartbeat for assertion in test 3.
    hb_logger = logging.getLogger("core.heartbeat")
    hb_logger.setLevel(logging.INFO)
    handler = _ListHandler()
    hb_logger.addHandler(handler)
    try:
        await test_brain_foreground_lifecycle()
        await test_brain_concurrent_depth()
        await test_heartbeat_yield_helper(handler.records)
        await test_proactive_bails_when_foreground_busy()
        await test_manual_research_does_not_yield()
    finally:
        hb_logger.removeHandler(handler)
    print("\nALL FOREGROUND-PRIORITY SMOKE CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(_main())
