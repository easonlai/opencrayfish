"""core.skills.proactive_learning — Heartbeat-triggered topical research.

Wraps the GATHER phase of `core/heartbeat.py::_proactive_research` —
i.e. given a topic, run a short web search and return a digest.

Dispatch: Heartbeat invokes
`skill_registry.invoke("proactive_learning", ctx, topic=..., limit=3)`.
The orchestration steps that surround that call (topic selection via
STM-gap extraction + LEARNED_PREFERENCES fallback, synthesis through
`brain.proactive_thought`, optional REFINE, journaling to
`state/proactive.jsonl`) stay in Heartbeat because they touch
multiple subsystems (STM + Soul + Brain) and the orchestrator-vs-
collaborator rule keeps the Skill focused on the single capability
it owns.

Cost tier: expensive (network round-trip). Network: required.
"""
from __future__ import annotations

from typing import Any

from .base import SkillContext, SkillResult


class ProactiveLearningSkill:
    name: str = "proactive_learning"
    description: str = (
        "Heartbeat-triggered autonomous research on a single topic. "
        "Runs a short SearXNG query and returns a 3-hit digest."
    )
    trigger_hints: list[str] = [
        "the agent has been idle past the proactive threshold",
        "a recent STM gap surfaced an unknown topic",
        "a long-term LEARNED_PREFERENCES interest deserves a refresh",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "topic": {
            "type": "string",
            "required": True,
            "desc": "The topic to research (free-form short phrase).",
        },
        "limit": {
            "type": "int",
            "required": False,
            "default": 3,
            "desc": "Maximum number of results (Heartbeat uses 3 by default).",
        },
    }
    cost_tier = "expensive"
    requires_network: bool = True
    side_effects: bool = False
    requires_confirmation: bool = False

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        topic = kwargs.get("topic", "")
        if not isinstance(topic, str) or not topic.strip():
            return SkillResult(ok=False, error="missing or empty 'topic' argument")
        try:
            limit = int(kwargs.get("limit", 3))
        except (TypeError, ValueError):
            limit = 3
        limit = max(1, min(10, limit))
        t = topic.strip()

        tool_result = await ctx.tools.call("web_search", query=t, limit=limit)
        if not tool_result.ok:
            return SkillResult(
                ok=False,
                error=tool_result.error or "web_search failed",
                tools_used=["web_search"],
            )

        hits = tool_result.data if isinstance(tool_result.data, list) else []
        if not hits:
            return SkillResult(
                ok=True,
                summary="(no results)",
                evidence=[],
                tools_used=["web_search"],
                meta={"hits": 0, "topic": t},
            )

        # Mirror the digest format Heartbeat._proactive_research builds
        # today: "- title: snippet" lines, suitable for direct inclusion
        # in the proactive mission prompt.
        digest_lines = [
            f"- {(h.get('title') or '').strip()}: "
            f"{(h.get('snippet') or '').strip()}"
            for h in hits
        ]
        return SkillResult(
            ok=True,
            summary="\n".join(digest_lines),
            evidence=list(hits),
            tools_used=["web_search"],
            meta={
                "hits": len(hits),
                "topic": t,
                "first_url": (hits[0].get("url") or "") if hits else "",
            },
        )

    async def aclose(self) -> None:
        return None
