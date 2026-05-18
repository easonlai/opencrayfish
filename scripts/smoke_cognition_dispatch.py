"""Smoke test: dynamic PLAN menu + ACT dispatch through `SkillRegistry`.

Boots a minimal CognitiveLoop with a stub provider that hands back
canned SLM output, plus a stub Monitor + tripped-circuit Provider, and
verifies:

  1. `_active_plan_entries()` tightens the cap when vitals are stressed
     and excludes network skills when the provider circuit is tripped.
  2. `_stage_plan` renders the dynamic menu, parses the SLM output, and
     resolves verbs to skill_names via the registry.
  3. `_run_step` dispatches by skill_name (legacy fast-paths for
     research/recall/direct_answer + generic dispatch for any other
     registered Skill with a plan_verb).
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.cognition import CognitiveLoop, PlanStep
from core.provider import ChatMessage
from core.skills import (
    SkillContext,
    SkillRegistry,
    SkillResult,
)
from core.skills.direct_answer import DirectAnswerSkill
from core.skills.recall import RecallSkill
from core.skills.research import ResearchSkill

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

@dataclass
class _StubVitals:
    is_stressed: bool


class _StubMonitor:
    def __init__(self, *, stressed: bool) -> None:
        self._stressed = stressed

    async def sample(self) -> _StubVitals:
        return _StubVitals(is_stressed=self._stressed)


class _StubProvider:
    """Returns canned text per stage so we can pin PLAN output.

    Stage detection sniffs distinctive substrings inside `system_prompt`
    (since cognition uses bare system + user messages, no `request_id`).
    """

    def __init__(self, *, tripped: bool, canned: dict[str, str] | None = None) -> None:
        self.is_tripped = tripped
        self.active_backend = "stub"
        self._canned = canned or {}
        self.calls: list[tuple[str, str]] = []

    async def generate(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
    ) -> str:
        user_msg = messages[0].content if messages else ""
        stage = "UNKNOWN"
        if "PLAN stage" in system_prompt:
            stage = "PLAN"
        elif "THINK stage" in system_prompt or "decompose" in system_prompt.lower():
            stage = "THINK"
        elif "REFINE stage" in system_prompt:
            stage = "REFINE"
        self.calls.append((stage, user_msg[:80]))
        if stage in self._canned:
            return self._canned[stage]
        if stage == "THINK":
            return "INTENT: dummy intent\nQ1: dummy sub q one"
        if stage == "PLAN":
            return "Q1: RECALL"
        if stage == "REFINE":
            return "OK"
        return ""


class _CountingSkill:
    """Tiny extra Skill so we can prove generic dispatch works."""

    name = "stamp"
    description = "Counts how many times it was invoked."
    cost_tier = "cheap"
    requires_network = False
    requires_confirmation = False
    plan_verb: str | None = "STAMP"
    plan_arg_hint: str | None = ""

    def __init__(self) -> None:
        self.invocations = 0

    async def execute(self, ctx: SkillContext, **kwargs: object) -> SkillResult:
        self.invocations += 1
        return SkillResult(
            ok=True,
            summary=f"stamped {self.invocations} time(s)",
            evidence=[{"call": str(self.invocations)}],
        )

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def _build_registry(stamp: _CountingSkill | None = None) -> SkillRegistry:
    sr = SkillRegistry()
    sr.register(RecallSkill())
    sr.register(ResearchSkill())
    sr.register(DirectAnswerSkill())
    if stamp is not None:
        sr.register(stamp)  # type: ignore[arg-type]
    return sr


def _make_loop(
    *,
    provider: _StubProvider,
    registry: SkillRegistry,
    monitor: _StubMonitor | None = None,
    dispatch_answer_via_skill: bool = False,
) -> CognitiveLoop:
    # SkillContext for stub use — we only invoke direct_answer/recall
    # off the smoke loop via _run_step paths that hit research/recall
    # legacy code, AND the stamp generic skill which doesn't touch ctx
    # fields. Pass None-typed placeholders past the dataclass since
    # frozen() still permits None at runtime.
    ctx = SkillContext(
        tools=None,       # type: ignore[arg-type]
        soul=None,        # type: ignore[arg-type]
        stm=None,         # type: ignore[arg-type]
        monitor=None,     # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        archive_path="/tmp/_cognition_dispatch_smoke_archive.md",
        designation="TestAgent",
        architect_name="Tester",
        architect_honorific="Friend",
    )
    return CognitiveLoop(
        provider=provider,  # type: ignore[arg-type]
        skill_registry=registry,
        skill_ctx=ctx,
        monitor=monitor,    # type: ignore[arg-type]
        cost_tier_cap="expensive",
        auto_offline_filter=True,
        dispatch_answer_via_skill=dispatch_answer_via_skill,
        feed_path=Path("/tmp/_cognition_dispatch_smoke_feed.jsonl"),
        timezone="UTC",
    )


async def test_active_entries_offline() -> None:
    provider = _StubProvider(tripped=True)
    loop = _make_loop(provider=provider, registry=_build_registry())
    entries = await loop._active_plan_entries()
    verbs = [e.verb for e in entries]
    assert "SEARCH" not in verbs, f"SEARCH should be dropped offline; got {verbs}"
    assert "RECALL" in verbs
    assert "ANSWER" in verbs
    print(f"  offline → verbs={verbs} ✓")


async def test_active_entries_stressed() -> None:
    provider = _StubProvider(tripped=False)
    monitor = _StubMonitor(stressed=True)
    loop = _make_loop(provider=provider, registry=_build_registry(), monitor=monitor)
    entries = await loop._active_plan_entries()
    verbs = [e.verb for e in entries]
    assert "SEARCH" not in verbs, f"SEARCH (expensive) should be dropped when stressed; got {verbs}"
    print(f"  stressed → verbs={verbs} ✓")


async def test_active_entries_healthy() -> None:
    provider = _StubProvider(tripped=False)
    monitor = _StubMonitor(stressed=False)
    loop = _make_loop(provider=provider, registry=_build_registry(), monitor=monitor)
    entries = await loop._active_plan_entries()
    verbs = sorted(e.verb for e in entries)
    assert verbs == ["ANSWER", "RECALL", "SEARCH"], f"got {verbs}"
    print(f"  healthy → verbs={verbs} ✓")


async def test_stage_plan_dynamic_menu() -> None:
    """SLM picks STAMP verb from registered _CountingSkill."""
    stamp = _CountingSkill()
    registry = _build_registry(stamp=stamp)
    provider = _StubProvider(
        tripped=False,
        canned={"PLAN": "Q1: STAMP\nQ2: RECALL"},
    )
    loop = _make_loop(provider=provider, registry=registry)
    plan = await loop._stage_plan(
        intent="dummy",
        subqs=["first sub q", "second sub q"],
        user_input="first sub q second sub q",
    )
    assert len(plan) == 2, f"expected 2 steps, got {len(plan)}: {plan}"
    assert plan[0].verb == "STAMP" and plan[0].skill_name == "stamp", f"step 0 = {plan[0]}"
    assert plan[1].verb == "RECALL" and plan[1].skill_name == "recall", f"step 1 = {plan[1]}"
    print(f"  stage_plan → {[(s.verb, s.skill_name) for s in plan]} ✓")


async def test_run_step_dispatches_generic_skill() -> None:
    stamp = _CountingSkill()
    registry = _build_registry(stamp=stamp)
    provider = _StubProvider(tripped=False)
    loop = _make_loop(provider=provider, registry=registry)
    step = PlanStep(
        sub_q="please stamp",
        verb="STAMP",
        query="",
        skill_name="stamp",
    )
    ev = await loop._run_step(step)
    assert "stamped 1 time(s)" in ev.content, f"unexpected content: {ev.content!r}"
    assert ev.hits == 1, f"expected hits=1, got {ev.hits}"
    assert stamp.invocations == 1, f"stamp called {stamp.invocations} times"
    print(f"  run_step generic dispatch → content={ev.content!r} ✓")


async def test_run_step_answer_legacy_noop() -> None:
    registry = _build_registry()
    provider = _StubProvider(tripped=False)
    loop = _make_loop(provider=provider, registry=registry, dispatch_answer_via_skill=False)
    step = PlanStep(sub_q="what is 2+2", verb="ANSWER", query="", skill_name="direct_answer")
    ev = await loop._run_step(step)
    assert "no retrieval performed" in ev.content
    assert ev.hits == 0
    print("  run_step ANSWER (legacy) → noop marker ✓")


async def test_run_step_unknown_skill_degrades() -> None:
    registry = _build_registry()
    provider = _StubProvider(tripped=False)
    loop = _make_loop(provider=provider, registry=registry)
    step = PlanStep(sub_q="??", verb="GHOST", query="", skill_name="ghost")
    ev = await loop._run_step(step)
    assert "no retrieval performed" in ev.content
    print("  run_step unknown skill → degrades to ANSWER marker ✓")


async def main() -> int:
    tests = [
        ("active entries offline", test_active_entries_offline),
        ("active entries stressed", test_active_entries_stressed),
        ("active entries healthy", test_active_entries_healthy),
        ("stage_plan dynamic menu", test_stage_plan_dynamic_menu),
        ("run_step generic skill", test_run_step_dispatches_generic_skill),
        ("run_step ANSWER legacy", test_run_step_answer_legacy_noop),
        ("run_step unknown skill", test_run_step_unknown_skill_degrades),
    ]
    for label, fn in tests:
        print(f"[{label}]")
        await fn()
    print("\nALL COGNITION-DISPATCH SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
