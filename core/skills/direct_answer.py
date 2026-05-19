"""core.skills.direct_answer — Single SLM call as a Skill.

Wraps the CognitiveLoop's `ANSWER` verb (and any "the SLM already knows
this, just answer" path). A pure passthrough to `ctx.provider.generate`
with a minimal system prompt — no archive read, no web search, no
multi-turn reasoning.

Dispatch: Opt-in via `cfg.cognition.dispatch_answer_via_skill`. When
true, the CognitiveLoop's ANSWER verb runs this Skill (one extra SLM
call per ANSWER step, surfacing the reply as evidence). When false
(legacy default), ANSWER is a marker that tells the final synth
"no retrieval needed" — no extra SLM call. Brain's main synthesize
step is intentionally NOT routed through here because it has richer
context (soul + emotions + STM) that a generic Skill can't carry.

Cost tier: cheap (one local SLM call). Network: not required (Provider
itself is local Ollama / Hailo-Ollama).
"""
from __future__ import annotations

from typing import Any

from .base import SkillContext, SkillResult
from .manifest import SkillManifest


class DirectAnswerSkill:
    # Declarative manifest (see ResearchSkill for the rationale). The
    # ``plan_guidance`` block teaches the PLAN-stage SLM when to pick
    # ANSWER (and, importantly, when NOT to — small models over-pick
    # ANSWER and hallucinate, so the guidance steers them toward
    # SEARCH when in doubt).
    manifest = SkillManifest(
        name="direct_answer",
        description=(
            "Ask the local SLM directly with no grounding. Use when the topic "
            "is general knowledge the model is expected to already know."
        ),
        plan_verb="ANSWER",
        plan_arg_hint="",
        plan_guidance=(
            "ANSWER ONLY for: stable textbook facts (arithmetic, basic "
            "definitions, mainstream programming syntax) that DO NOT "
            "depend on dates or versions. When in doubt, prefer SEARCH "
            "over ANSWER. The local model is small."
        ),
        plan_example="Q3: ANSWER",
        trigger_hints=(
            "the question is general / timeless / not domain-specific",
            "no recall or web search is needed for an adequate answer",
            "the user wants a conversational reply",
        ),
        args_schema={
            "query": {
                "type": "string",
                "required": True,
                "desc": "The user question or sub-question to answer.",
            },
            "system": {
                "type": "string",
                "required": False,
                "default": "",
                "desc": "Optional system-prompt override. Empty = default minimal prompt.",
            },
        },
        cost_tier="cheap",
        requires_network=False,
        requires_caps=("provider",),
    )

    name: str = "direct_answer"
    description: str = (
        "Ask the local SLM directly with no grounding. Use when the topic "
        "is general knowledge the model is expected to already know."
    )
    trigger_hints: list[str] = [
        "the question is general / timeless / not domain-specific",
        "no recall or web search is needed for an adequate answer",
        "the user wants a conversational reply",
    ]
    args_schema: dict[str, dict[str, Any]] = {
        "query": {
            "type": "string",
            "required": True,
            "desc": "The user question or sub-question to answer.",
        },
        "system": {
            "type": "string",
            "required": False,
            "default": "",
            "desc": "Optional system-prompt override. Empty = default minimal prompt.",
        },
    }
    cost_tier = "cheap"
    requires_network: bool = False
    side_effects: bool = False
    requires_confirmation: bool = False
    # PLAN-menu exposure. `ANSWER` is a marker verb: by default the
    # CognitiveLoop treats it as a no-op (synth leans on SLM training
    # data) for byte-identical legacy behavior. Operators can opt in
    # (`cfg.cognition.dispatch_answer_via_skill = true`) to have ACT
    # invoke this Skill and surface the reply as evidence.
    plan_verb: str | None = "ANSWER"
    plan_arg_hint: str | None = ""

    # Default minimal system prompt. Kept short on purpose — the
    # Cognitive Loop / Brain will compose richer prompts at the
    # orchestration layer when needed.
    _DEFAULT_SYSTEM: str = (
        "You are a concise assistant. Answer the user's question in 1-3 "
        "sentences. If you don't know, say so plainly — do NOT invent facts."
    )

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return SkillResult(ok=False, error="missing or empty 'query' argument")
        system = str(kwargs.get("system", "") or "").strip() or self._DEFAULT_SYSTEM

        # Local import: avoid forcing a Provider import at module load
        # time for environments that import this Skill purely for
        # registration / introspection (e.g. unit tests).
        from core.provider import ChatMessage

        try:
            text = await ctx.provider.generate(
                system,
                [ChatMessage(role="user", content=query.strip())],
            )
        except Exception as exc:
            return SkillResult(
                ok=False,
                error=f"provider.generate failed: {exc.__class__.__name__}: {exc}",
            )

        text = (text or "").strip()
        if not text:
            return SkillResult(
                ok=False,
                error="provider returned empty text",
            )

        return SkillResult(
            ok=True,
            summary=text,
            evidence=[],
            tools_used=[],
            meta={"backend": getattr(ctx.provider, "active_backend", "?")},
        )

    async def aclose(self) -> None:
        return None
