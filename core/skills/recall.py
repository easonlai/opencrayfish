"""core.skills.recall — Keyword-overlap LTM lookup as a Skill.

Wraps the `archive_read` Tool with the conventions the Cognitive Loop's
RECALL verb expects: formatted bullet-list summary + per-line evidence
records that the deliberation audit feed can persist verbatim.

Dispatch: CognitiveLoop's `_do_recall` and Brain's
`_retrieve_relevant` both invoke
`skill_registry.invoke("recall", ctx, query=...)` — the archive file
is no longer read directly from those subsystems. All reads go
through the `archive_read` Tool inside this Skill.

Cost tier: cheap (one local file read). Network: not required.
"""
from __future__ import annotations

from typing import Any

from .base import SkillContext, SkillResult


class RecallSkill:
    name: str = "recall"
    description: str = (
        "Look up relevant facts in the agent's LTM (archive.md) by "
        "keyword overlap. Use when the answer might already be in memory."
    )
    trigger_hints: list[str] = [
        "the user references a topic discussed before",
        "the agent should check what it already knows",
        "the question is timeless / not freshness-sensitive",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "query": {
            "type": "string",
            "required": True,
            "desc": "Query whose terms are matched against archive lines.",
        },
        "limit": {
            "type": "int",
            "required": False,
            "default": 5,
            "desc": "Maximum number of archive lines to return.",
        },
    }
    cost_tier = "cheap"
    requires_network: bool = False
    side_effects: bool = False
    requires_confirmation: bool = False
    # Phase 3 PLAN-menu exposure. The CognitiveLoop renders the menu
    # from `SkillRegistry.plan_menu(...)`; this verb is the SLM-facing
    # token (kept SHORT and SHOUTY because small SLMs anchor on stable
    # all-caps verbs better than lowercase Skill names). The verb maps
    # back to this Skill's `name` at dispatch time.
    plan_verb: str | None = "RECALL"
    # No `<query>` placeholder in the menu line — RECALL operates on the
    # full sub-question text the PLAN parser already has.
    plan_arg_hint: str | None = ""

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return SkillResult(ok=False, error="missing or empty 'query' argument")
        try:
            limit = int(kwargs.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(20, limit))

        tool_result = await ctx.tools.call(
            "archive_read", query=query.strip(), limit=limit
        )
        # archive_read returns ok=True with empty data when nothing
        # matches — preserve that semantic at the Skill boundary so
        # callers can branch on `hits == 0` without a special error path.
        if not tool_result.ok:
            return SkillResult(
                ok=False,
                error=tool_result.error or "archive_read failed",
                tools_used=["archive_read"],
            )

        payload = tool_result.data if isinstance(tool_result.data, list) else []
        if not payload:
            reason = tool_result.meta.get("reason", "no_match")
            return SkillResult(
                ok=True,
                summary=f"(no archive matches — {reason})",
                evidence=[],
                tools_used=["archive_read"],
                meta={"hits": 0, "reason": reason},
            )

        lines = [f"- {entry['line']}" for entry in payload]
        summary = "\n".join(lines)
        return SkillResult(
            ok=True,
            summary=summary,
            evidence=payload,
            tools_used=["archive_read"],
            meta={
                "hits": len(payload),
                "top_score": tool_result.meta.get("top_score", 0),
            },
        )

    async def aclose(self) -> None:
        return None
