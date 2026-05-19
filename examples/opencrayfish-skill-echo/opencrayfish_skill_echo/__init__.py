"""OpenCrayFish reference Skill: echo.

This package is the **canonical example** of a third-party Skill for
OpenCrayFish. It is intentionally trivial — it just echoes whatever
``query`` the operator passes in — so a third-party author can copy
this directory verbatim, run a quick sed, and ship a working plugin.

WHAT THIS SHOWS
---------------
1. The full ``SkillManifest`` surface (name, description, plan
   verb/hint/guidance/example, trigger_hints, args_schema, cost_tier,
   network/tool/cap requirements, compat_version). Every field is
   filled in so authors can see what the registry inspects.
2. The async ``execute`` Skill Protocol verb signature, with
   defensive arg validation and ``SkillResult(ok=False, ...)``
   never-raise discipline.
3. Optional ``aclose()`` lifecycle hook so the registry can clean
   up on agent shutdown.

WHAT THIS DOES *NOT* SHOW
-------------------------
* Tool invocation via ``ctx.tools.call(...)`` — the ``research``
  skill in OpenCrayFish's own source covers that pattern.
* PLAN-stage menu participation across multiple verbs from one
  package — that's also covered by the first-party skills.

The boot trace for a successful registration looks like:

    SKILL registered name=echo protocol=skill-protocol/1 ...
    SKILL Discovered 1 external skill(s) via entry-points: echo
"""
from __future__ import annotations

from typing import Any

from core.skills import SkillContext, SkillManifest, SkillResult


class EchoSkill:
    """Echo the operator's query back, prefixed with ``"echo: "``."""

    manifest = SkillManifest(
        name="echo",
        description=(
            "Reference plugin that echoes the operator's query back "
            "verbatim. Use as a copy-paste starting point for new "
            "third-party Skills."
        ),
        # plan_verb is what the PLAN-stage SLM writes in its plan.
        # Leave it None if this skill is invoked only programmatically
        # (e.g. by an orchestrator) and should NOT appear in the menu.
        plan_verb="ECHO",
        plan_arg_hint='"<text>"',
        plan_guidance=(
            "ECHO for: explicit operator requests to repeat or "
            "rephrase a piece of text back. NEVER use ECHO when the "
            "user is asking a question — pick DIRECT_ANSWER instead."
        ),
        plan_example='Q1: ECHO "hello world"',
        trigger_hints=(
            "operator says 'echo X' or 'repeat after me X'",
            "operator wants a literal repeat of provided text",
        ),
        args_schema={
            "query": {
                "type": "string",
                "required": True,
                "desc": "The text to echo back. Required, non-empty.",
            },
        },
        cost_tier="free",
        requires_network=False,
        requires_tools=(),
        requires_caps=(),
        compat_version="skill-protocol/1",
    )

    async def execute(
        self, ctx: SkillContext, **kwargs: Any
    ) -> SkillResult:
        query = kwargs.get("query", "")
        if not isinstance(query, str) or not query.strip():
            # Skills MUST NEVER raise. Wrap argument problems in an
            # ``ok=False`` result so the orchestrator can route the
            # failure into its REFINE phase instead of crashing.
            return SkillResult(
                ok=False,
                error="missing or empty 'query' argument",
            )
        return SkillResult(
            ok=True,
            summary=f"echo: {query}",
            evidence=[],
            tools_used=[],
        )

    async def aclose(self) -> None:
        """No resources held — no-op."""
        return None


__all__ = ["EchoSkill"]
