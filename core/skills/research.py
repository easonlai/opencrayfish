"""core.skills.research — Live web search as a Skill.

Wraps the `web_search` Tool (SearXNG) with the conventions the
CognitiveLoop's `SEARCH` verb and Brain's `_do_search` use today:
formatted markdown summary block + structured evidence records.

Dispatch: Brain / CognitiveLoop / Heartbeat / TaskScheduler all
invoke `skill_registry.invoke("research", ctx, query=...)` — none of
them hold a direct reference to SearXNG anymore. The registry call
gives us per-invocation timing, append-only audit in
`state/skills.jsonl`, and crash isolation for free.

Cost tier: expensive (network round-trip + remote indexer cost).
Network: required.
"""
from __future__ import annotations

from typing import Any

from .base import SkillContext, SkillResult


class ResearchSkill:
    name: str = "research"
    description: str = (
        "Live web search via SearXNG. Use for time-sensitive, niche, or "
        "post-training-cutoff facts the SLM cannot reliably answer alone."
    )
    trigger_hints: list[str] = [
        "the user explicitly asked to 'search' / '搜尋'",
        "the question is about a current event or fresh data",
        "the topic is niche or post-training-cutoff",
        "the SLM is not confident it knows the answer first-hand",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "query": {
            "type": "string",
            "required": True,
            "desc": "3-8 keywords (NOT a full sentence) to search the web for.",
        },
        "limit": {
            "type": "int",
            "required": False,
            "default": 5,
            "desc": "Maximum number of results to return (1-10).",
        },
    }
    cost_tier = "expensive"
    requires_network: bool = True
    side_effects: bool = False
    requires_confirmation: bool = False
    # Phase 3 PLAN-menu exposure. `SEARCH "<3-8 keywords>"` is the
    # historical SLM verb so we keep it byte-identical; the parser
    # extracts the quoted query and we dispatch through this Skill.
    plan_verb: str | None = "SEARCH"
    plan_arg_hint: str | None = '"<3-8 keywords>"'

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return SkillResult(ok=False, error="missing or empty 'query' argument")
        try:
            limit = int(kwargs.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(10, limit))
        q = query.strip()

        tool_result = await ctx.tools.call("web_search", query=q, limit=limit)
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
                summary=f"(no results for {q!r})",
                evidence=[],
                tools_used=["web_search"],
                meta={"hits": 0, "query": q},
            )

        # Mirror the formatting Brain._do_search and cognition._do_search
        # use today so Phase 2 swap is visually identical in the system
        # prompt.
        lines = [f"Query: {q!r}"]
        for h in hits:
            title = (h.get("title") or "").strip()
            url = (h.get("url") or "").strip()
            snippet = (h.get("snippet") or "").strip().replace("\n", " ")[:240]
            lines.append(f"- {title} ({url})\n  {snippet}")

        return SkillResult(
            ok=True,
            summary="\n".join(lines),
            evidence=list(hits),
            tools_used=["web_search"],
            meta={
                "hits": len(hits),
                "query": q,
                "first_url": (hits[0].get("url") or "") if hits else "",
            },
        )

    async def aclose(self) -> None:
        return None
