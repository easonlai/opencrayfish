"""core.skills.self_reflect — Post-turn self-critique as a Skill.

Thin wrapper around `core.reflection.ReflectionEngine.reflect(...)` so
Brain / Heartbeat dispatch reflection through the SkillRegistry
(`skill_registry.invoke("self_reflect", ctx, ...)`) instead of
calling the engine directly. Heartbeat's `_consolidate_reflections`
still calls `reflection.read_recent(...)` directly because the
read-side has no SLM/network involvement — it's a plain JSONL tail
read that doesn't benefit from registry instrumentation.

The engine is injected at construction time (not via SkillContext)
because:
  * Not every Skill needs a ReflectionEngine — putting it on
    SkillContext would force unrelated Skills to carry a reference
    they never use.
  * Reflection has its own persistence layer (state/reflection.jsonl)
    and dropped-output feed that ARE part of ReflectionEngine, not
    the Skill — so wrapping vs. reimplementing keeps the source of
    truth single.

When `engine` is None (cfg.reflection.enabled=false) the Skill returns
ok=True with a "(reflection disabled)" summary rather than failing, so
callers can branch on `result.meta["disabled"]` without an error path.

Cost tier: cheap (one local SLM critique call). Network: not required.
Side effects: True (appends one line to state/reflection.jsonl on
success, one line to state/reflection_dropped.jsonl on parse failure).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import SkillContext, SkillResult

if TYPE_CHECKING:
    from core.reflection import ReflectionEngine


class SelfReflectSkill:
    name: str = "self_reflect"
    description: str = (
        "Score the agent's own reply for quality and extract a one-line "
        "lesson + interest topic. Persists to state/reflection.jsonl."
    )
    trigger_hints: list[str] = [
        "an interaction has just completed (user turn or proactive thought)",
        "the agent wants to consolidate a lesson for future use",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "kind": {
            "type": "string",
            "required": True,
            "desc": "'user' (post-reply) or 'proactive' (post-heartbeat).",
        },
        "input_text": {
            "type": "string",
            "required": True,
            "desc": "What triggered the reply (user message or proactive topic).",
        },
        "response": {
            "type": "string",
            "required": True,
            "desc": "The agent's filtered final reply text.",
        },
        "web_searched": {
            "type": "bool",
            "required": False,
            "default": False,
            "desc": "Whether SearXNG was consulted for this turn.",
        },
        "backend": {
            "type": "string",
            "required": False,
            "default": "",
            "desc": "Model id that produced the reply (e.g. 'qwen2.5-instruct:1.5b').",
        },
    }
    cost_tier = "cheap"
    requires_network: bool = False
    side_effects: bool = True   # writes to state/reflection.jsonl
    requires_confirmation: bool = False

    def __init__(self, *, engine: ReflectionEngine | None = None) -> None:
        # `engine` is None when cfg.reflection.enabled is false. In that
        # case execute() returns a graceful no-op rather than an error.
        self._engine = engine

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        if self._engine is None:
            return SkillResult(
                ok=True,
                summary="(reflection disabled)",
                meta={"disabled": True},
            )

        kind = str(kwargs.get("kind", "")).strip()
        input_text = str(kwargs.get("input_text", "") or "")
        response = str(kwargs.get("response", "") or "")
        if kind not in {"user", "proactive"}:
            return SkillResult(
                ok=False, error=f"'kind' must be 'user' or 'proactive', got {kind!r}"
            )
        if not response.strip():
            return SkillResult(
                ok=False, error="missing or empty 'response' argument"
            )

        web_searched = bool(kwargs.get("web_searched", False))
        backend = str(
            kwargs.get("backend") or getattr(ctx.provider, "active_backend", "?")
        )

        entry = await self._engine.reflect(
            kind=kind,
            input_text=input_text,
            response=response,
            web_searched=web_searched,
            backend=backend,
        )
        if entry is None:
            return SkillResult(
                ok=False,
                error="reflection produced no entry (dropped or LLM failure)",
                meta={"kind": kind},
            )
        return SkillResult(
            ok=True,
            summary=f"quality={entry.quality} lesson={entry.lesson[:80]!r}",
            evidence=[{
                "quality": entry.quality,
                "critique": entry.critique,
                "lesson": entry.lesson,
                "interest": entry.interest,
            }],
            tools_used=[],
            meta={
                "kind": kind,
                "backend": backend,
                "interest": entry.interest,
            },
        )

    async def aclose(self) -> None:
        # ReflectionEngine has no aclose; nothing to release here.
        return None
