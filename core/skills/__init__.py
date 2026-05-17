"""core.skills — Capability layer above tools.

A Skill is the AGENT-FACING capability the Cognitive Loop / Heartbeat /
Scheduler picks to satisfy one sub-question or fire one autonomous
cycle. Each Skill composes 0..N Tool calls plus its own policy and
returns a uniform `SkillResult` for downstream consumption.

See `core/skills/base.py` for the contract and the "Adding a new Skill"
section of the README for a step-by-step author guide. Every shipping
subsystem (Brain, CognitiveLoop, Heartbeat, TaskScheduler) routes its
outbound capability calls through `skill_registry.invoke(...)` so we
get uniform timing, JSONL audit, and crash isolation for free — and so
an operator can add a new capability by writing one Skill class with
zero changes to the orchestrators.
"""
from __future__ import annotations

from .base import CostTier, Skill, SkillContext, SkillResult
from .registry import SKILLS_AUDIT_FEED, PlanMenuEntry, SkillRegistry

__all__ = [
    "CostTier",
    "PlanMenuEntry",
    "Skill",
    "SkillContext",
    "SkillResult",
    "SkillRegistry",
    "SKILLS_AUDIT_FEED",
]
