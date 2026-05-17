"""core.skills.identity — Soul-template identity replies.

Wraps the soul-block parsing + reply templating that used to live
inline in `core/brain.py::_cycle` (the `backend="identity_shortcut"`
branch). The trigger logic (regex pre-check on "what's your name",
"who made you", …) stays in Brain — this Skill only owns the RENDER
step, invoked by `Brain._try_identity_skill` via
`skill_registry.invoke("identity", ctx, kind=...)`. Brain still keeps
an inline fallback so an unregistered or failing skill never blocks
an identity reply.

Cost tier: free (pure compute, no I/O beyond the already-cached soul
block). Network: not required.
"""
from __future__ import annotations

import re
from typing import Any

from .base import SkillContext, SkillResult

# Mirrors the regex pattern in `core/brain.py::_extract_identity` so the
# two implementations agree until Phase 2 unifies them.
_DESIGNATION_RE = re.compile(r"\*\*Designation\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)
_CODENAME_RE = re.compile(r"\*\*Codename\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)
_CREATOR_RE = re.compile(r"\*\*Creator\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE)


def _strip_paren(text: str) -> str:
    """Strip a trailing parenthetical hint, e.g. 'Cray-01 (set by ...)'."""
    return re.sub(r"\s*\(.*?\)\s*$", "", text).strip()


class IdentitySkill:
    name: str = "identity"
    description: str = (
        "Compose a templated reply for identity questions (name, codename, "
        "creator) sourced from soul.md IMMUTABLE_CORE — no SLM call."
    )
    trigger_hints: list[str] = [
        "the user asks 'what's your name' / 'who are you' / '你叫咩名'",
        "the user asks 'who made you' / 'who created you'",
        "the user asks for the agent's codename",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "kind": {
            "type": "string",
            "required": False,
            "default": "name",
            "desc": "Which identity facet to render: 'name', 'codename', 'creator', or 'full'.",
        },
    }
    cost_tier = "free"
    requires_network: bool = False
    side_effects: bool = False
    requires_confirmation: bool = False

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        kind = str(kwargs.get("kind", "name")).strip().lower() or "name"
        if kind not in {"name", "codename", "creator", "full"}:
            return SkillResult(ok=False, error=f"unknown kind: {kind!r}")

        try:
            soul_block = await ctx.soul.render_identity_block()
        except Exception as exc:
            return SkillResult(
                ok=False,
                error=f"soul read failed: {exc.__class__.__name__}: {exc}",
            )

        designation = ctx.designation or "the Agent"
        codename = ""
        creator = ""
        if (m := _DESIGNATION_RE.search(soul_block)):
            candidate = _strip_paren(m.group("value").strip())
            if candidate and "{{" not in candidate:
                designation = candidate
        if (m := _CODENAME_RE.search(soul_block)):
            candidate = m.group("value").strip()
            if candidate and "{{" not in candidate:
                codename = candidate
        if (m := _CREATOR_RE.search(soul_block)):
            candidate = _strip_paren(m.group("value").strip())
            if candidate and "{{" not in candidate:
                creator = candidate

        if kind == "name":
            summary = f"I am {designation}."
        elif kind == "codename":
            summary = f"My codename is {codename}." if codename else (
                f"I don't carry a separate codename — I'm just {designation}."
            )
        elif kind == "creator":
            summary = (
                f"I was created by {creator}." if creator
                else "My creator isn't recorded in my soul."
            )
        else:  # full
            parts = [f"I am {designation}."]
            if codename:
                parts.append(f"My codename is {codename}.")
            if creator:
                parts.append(f"I was created by {creator}.")
            summary = " ".join(parts)

        return SkillResult(
            ok=True,
            summary=summary,
            evidence=[{
                "designation": designation,
                "codename": codename,
                "creator": creator,
                "kind": kind,
            }],
            tools_used=[],
            meta={"kind": kind},
        )

    async def aclose(self) -> None:
        return None
