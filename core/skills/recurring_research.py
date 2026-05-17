"""core.skills.recurring_research — Scheduler-triggered multi-query research.

Wraps the GATHER phase of `core/scheduler.py::_fire` — given a list of
SearXNG queries for one recurring task, run them sequentially and
return a per-query brief.

Dispatch: TaskScheduler invokes
`skill_registry.invoke("recurring_research", ctx, queries=[...])`.
The downstream steps (synthesis via `brain.synthesize_task_report`,
broadcast to bound delivers, registry persistence, reschedule) stay
in TaskScheduler because they touch Brain and connectors directly.

Cost tier: expensive (N network round-trips). Network: required.
"""
from __future__ import annotations

from typing import Any

from .base import SkillContext, SkillResult


class RecurringResearchSkill:
    name: str = "recurring_research"
    description: str = (
        "Scheduler-triggered multi-query research. Runs each of N SearXNG "
        "queries sequentially and returns a per-query brief."
    )
    trigger_hints: list[str] = [
        "a recurring task has reached its next_run_at",
        "the operator created a multi-query monitoring task",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "queries": {
            "type": "list[string]",
            "required": True,
            "desc": "List of SearXNG queries to run sequentially.",
        },
        "results_per_query": {
            "type": "int",
            "required": False,
            "default": 5,
            "desc": "Max results per query (Scheduler uses 5 by default).",
        },
    }
    cost_tier = "expensive"
    requires_network: bool = True
    side_effects: bool = False
    requires_confirmation: bool = False

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        queries = kwargs.get("queries")
        if not isinstance(queries, list) or not queries:
            return SkillResult(
                ok=False, error="missing or empty 'queries' argument"
            )
        cleaned: list[str] = []
        for q in queries:
            if isinstance(q, str) and q.strip():
                cleaned.append(q.strip())
        if not cleaned:
            return SkillResult(
                ok=False, error="all 'queries' entries are empty after cleanup"
            )
        try:
            limit = int(kwargs.get("results_per_query", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(10, limit))

        # Sequential — same as TaskScheduler._fire — to avoid hammering
        # SearXNG with N concurrent requests on a Pi-class deployment.
        gathered: list[dict[str, Any]] = []
        brief_parts: list[str] = []
        any_hit = False
        tools_used: list[str] = ["web_search"]
        for q in cleaned:
            tool_result = await ctx.tools.call("web_search", query=q, limit=limit)
            # Failures degrade — record and continue, like the legacy
            # Scheduler does. The synthesis layer sees the empty section
            # and disambiguates ("no results for query X").
            if not tool_result.ok or not isinstance(tool_result.data, list):
                gathered.append({
                    "query": q,
                    "hits": [],
                    "error": tool_result.error or "",
                })
                brief_parts.append(f"### Query: {q}")
                brief_parts.append("(no results)")
                continue
            hits = tool_result.data
            gathered.append({"query": q, "hits": list(hits), "error": ""})
            brief_parts.append(f"### Query: {q}")
            if not hits:
                brief_parts.append("(no results)")
                continue
            any_hit = True
            for h in hits:
                title = (h.get("title") or "(untitled)").strip()
                snippet = (h.get("snippet") or "(no snippet)").strip()
                url = (h.get("url") or "").strip()
                brief_parts.append(f"- **{title}** — {snippet}\n  {url}")

        return SkillResult(
            ok=True,
            summary="\n".join(brief_parts),
            evidence=gathered,
            tools_used=tools_used,
            meta={
                "query_count": len(cleaned),
                "any_hit": any_hit,
                "total_hits": sum(len(g["hits"]) for g in gathered),
            },
        )

    async def aclose(self) -> None:
        return None
