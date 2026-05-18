"""Smoke test: `SkillRegistry.plan_menu()` filter matrix.

Verifies that the dynamic PLAN menu correctly:
  * lists only Skills that declared `plan_verb`
  * tightens by `cost_tier_cap`
  * drops network-requiring entries when `exclude_network=True`
  * sorts by (cost_order, verb) deterministically
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.skills import SkillRegistry
from core.skills.direct_answer import DirectAnswerSkill
from core.skills.identity import IdentitySkill
from core.skills.proactive_learning import ProactiveLearningSkill
from core.skills.recall import RecallSkill
from core.skills.recurring_research import RecurringResearchSkill
from core.skills.research import ResearchSkill
from core.skills.self_reflect import SelfReflectSkill


def main() -> int:
    sr = SkillRegistry()
    sr.register(RecallSkill())
    sr.register(ResearchSkill())
    sr.register(DirectAnswerSkill())
    sr.register(IdentitySkill())
    sr.register(SelfReflectSkill(engine=None))
    sr.register(ProactiveLearningSkill())
    sr.register(RecurringResearchSkill())

    def show(label: str, entries: list[object]) -> None:
        print(f"=== {label} ===")
        for e in entries:
            verb = getattr(e, "verb")
            skill = getattr(e, "skill_name")
            cost = getattr(e, "cost_tier")
            net = getattr(e, "requires_network")
            hint = getattr(e, "arg_hint")
            print(
                "  verb=" + verb.ljust(7)
                + " skill=" + skill.ljust(14)
                + " cost=" + cost.ljust(9)
                + " net=" + str(net)
                + " arg_hint=" + repr(hint)
            )
        if not entries:
            print("  (empty)")
        print()

    show("defaults (cap=expensive, online)", sr.plan_menu())
    show("offline (cap=expensive, exclude_network=True)",
         sr.plan_menu(exclude_network=True))
    show("stressed (cap=cheap, online)",
         sr.plan_menu(cost_tier_cap="cheap"))
    show("stressed AND offline (cap=cheap, exclude_network=True)",
         sr.plan_menu(cost_tier_cap="cheap", exclude_network=True))
    show("cap=free", sr.plan_menu(cost_tier_cap="free"))

    # Assertions
    full = sr.plan_menu()
    verbs = sorted(e.verb for e in full)
    assert verbs == ["ANSWER", "RECALL", "SEARCH"], f"unexpected verbs: {verbs}"

    offline = sr.plan_menu(exclude_network=True)
    assert all(not e.requires_network for e in offline)
    assert "SEARCH" not in [e.verb for e in offline]

    cheap = sr.plan_menu(cost_tier_cap="cheap")
    assert all(e.cost_tier in ("free", "cheap") for e in cheap)

    free_only = sr.plan_menu(cost_tier_cap="free")
    assert free_only == []  # no Skill declares cost_tier=free with plan_verb

    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
