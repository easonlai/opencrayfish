"""core.skills.base — Skill plugin contract.

A `Skill` is the AGENT-FACING capability layer that sits above the
mechanical `Tool` layer:

  * A **Tool** (see `tools.base.Tool`) is a single mechanism — one HTTP
    call, one file read, one GPIO write. It validates kwargs and
    returns a `ToolResult`. It does NOT decide policy.

  * A **Skill** is a capability the agent can pick to satisfy one
    sub-question in the Cognitive Loop, or that the Heartbeat /
    Scheduler / Architect can invoke directly. A Skill composes 0..N
    Tool calls plus its own policy (which Tool to pick, how to format
    results, whether to fall back when offline, …). It returns a
    `SkillResult` whose `summary` field is rendered into the SLM's
    synth prompt and whose `evidence` field feeds the deliberation
    audit trail.

The split lets us add a new capability (weather, home control, MCP
bridge) by writing one Skill class + optionally one Tool class — with
ZERO changes to Brain / CognitiveLoop / Heartbeat / TaskScheduler.

Design notes
------------
* `Skill` is a runtime-checkable Protocol. Concrete skills don't have
  to inherit from it — they just need the right surface (name,
  description, trigger_hints, args_schema, cost_tier flags, and an
  async `execute(...)`). This mirrors the `Tool` contract for
  consistency and keeps registration cheap.

* `SkillResult` is a frozen dataclass so call sites can pattern-match
  on `ok` / `error` without juggling tuple positions. `summary` is the
  short, prompt-ready string; `evidence` is the structured payload
  (search hits, archive lines, sensor readings) that downstream
  consumers (Reflection, dashboard, deliberation audit) read by key.

* `SkillContext` carries the SHARED collaborators every Skill might
  need — ToolRegistry, soul, STM, monitor, provider, archive path,
  designation, architect identity. It is constructed ONCE at boot in
  `main.py` and reused across every `invoke()`; subsystems may pass
  the same context object to many calls. Frozen → safe to share.

* `cost_tier` lets the CognitiveLoop's PLAN-stage filter the menu
  (`cheap` only when vitals are stressed, etc.). `requires_network`
  lets it filter out skills the Provider's circuit breaker can't
  serve right now. `side_effects` + `requires_confirmation` gate a
  future Architect-ack prompt for actuator skills.

* No dependency on Brain / CognitiveLoop / Heartbeat so this module
  stays cheap to import and reusable.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.monitor import Monitor
    from core.provider import Provider
    from core.soul_handler import SoulHandler
    from core.stm import ShortTermMemory
    from tools.registry import ToolRegistry


# Cost tiers for the PLAN-stage menu filter:
#   free      = pure compute / no I/O (e.g. identity templating)
#   cheap     = local I/O only (archive read, single local SLM call)
#   expensive = network or multi-SLM-call (web search, deep research)
CostTier = Literal["free", "cheap", "expensive"]


@dataclass(frozen=True)
class SkillResult:
    """Uniform return shape for every Skill.execute().

    Skills MUST NEVER raise — wrap failures in
    `SkillResult(ok=False, error=...)`. Callers (CognitiveLoop, Heartbeat,
    TaskScheduler, dashboard /test endpoint) treat `ok=False` as
    degrade-and-continue, not crash.

    Fields
    ------
    ok           True iff the skill produced a usable result.
    summary      Short prompt-ready string. This is what gets rendered
                 into the SLM's synth context. Keep it tight — the small
                 SLMs we ship on have <2k token budgets.
    evidence     Structured payload: list of homogeneous dicts. Each
                 Skill documents its own evidence shape (search hits =
                 [{"title","url","snippet"}], recall = [{"line","score"}],
                 …). Consumers read by key, not positionally.
    tools_used   Names of every Tool this invocation called. Surfaced in
                 the deliberation audit and on the dashboard. Empty list
                 for skills that pure-compute (identity, direct_answer).
    latency_ms   Wall-clock for the whole execute(). Set by SkillRegistry
                 (not by the Skill itself) so we get uniform timing.
    meta         Free-form per-skill telemetry (hit count, cache hit,
                 backend chosen, …). Surfaced in skills.jsonl audit.
    error        Short human-readable failure reason when ok=False.
                 Empty on success.
    """

    ok: bool
    summary: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class SkillContext:
    """Stable handles passed to every Skill.execute().

    Built ONCE at boot in `main.py` and reused across every `invoke()`.
    Frozen so Skills can't accidentally mutate shared state through
    the context — they go through the proper subsystem APIs
    (`ctx.stm.add(...)`, `ctx.soul.read(...)`, `ctx.tools.call(...)`,
    etc.) which carry their own locking.

    NOT included here on purpose:
      * `Brain` — would create a cycle Skill → Brain → Skill; Brain
        is the orchestrator, not a collaborator.
      * `Emotions` / `Empathy` / `PositiveFilter` — mood/filter is a
        Brain concern (it shapes the final reply); Skills produce
        evidence, Brain decides how to surface it.
      * `Heartbeat` / `Scheduler` — same orchestrator-not-collaborator
        rule. A Skill is invoked BY them, it does not call back.
      * `ReflectionEngine` — passed to `SelfReflectSkill` directly via
        its constructor; not every Skill needs it.
    """

    tools: ToolRegistry
    soul: SoulHandler
    stm: ShortTermMemory
    monitor: Monitor
    provider: Provider
    # Path to memory/archive.md. Skills that need to read LTM use this
    # (or, better, dispatch through the `archive_read` Tool which wraps
    # the read with its own validation + telemetry).
    archive_path: str
    # Identity (read from cfg.system at boot — these never change at
    # runtime; soul.md's designation is itself injected from this value
    # so they always agree).
    designation: str
    architect_name: str
    architect_honorific: str

    # Optional extension slot for cross-cutting subsystems that don't
    # warrant their own typed field yet (emotions snapshot, empathy
    # directive, deliberation budget, future RAG retriever, …). Keeping
    # this as a ``Mapping[str, Any]`` rather than a typed dataclass
    # field means new collaborators can be wired through to a Skill
    # WITHOUT a coordinated refactor of every existing Skill — only the
    # Skill that needs the new key reads it. Once a key proves stable
    # across multiple Skills it should graduate to a typed field of its
    # own (and be removed from ``extras``).
    #
    # Frozen-by-construction: ``main.py`` should pass a ``MappingProxyType``
    # wrapping the underlying dict so Skills cannot mutate the original
    # via the context (the field itself is on a frozen dataclass, so
    # ``ctx.extras = ...`` is blocked, but the wrapped dict would
    # otherwise still be mutable). The default factory below uses an
    # EMPTY read-only mapping so the no-extras case is also immutable.
    extras: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
    )

    # Per-plugin configuration namespace. Mirrors ``cfg.plugins`` from
    # ``core/config.py``: each top-level key matches a Skill or Tool's
    # ``manifest.config_key`` (or its ``name`` when ``config_key`` is
    # unset). A Skill retrieves ITS slice via
    # ``ctx.plugins_config.get(self.manifest.config_key or self.manifest.name, {})``.
    # This is the seam that lets third-party authors take operator
    # config without ever touching ``core/config.py``. Same MappingProxy
    # discipline as ``extras`` \u2014 the default is an empty read-only map
    # so unit tests don't need to wire config to construct a context.
    plugins_config: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({}),
    )


@runtime_checkable
class Skill(Protocol):
    """The plugin contract.

    A class satisfies `Skill` purely by shape — no inheritance required.
    See `core/skills/research.py` for the canonical example.
    """

    # Stable identifier used by the registry AND injected into the
    # PLAN-stage SLM prompt as the verb name. Convention: lowercase,
    # snake_case, verb-y (`research`, `recall`, `direct_answer`,
    # `home_control`, …). Reserved aliases: SEARCH→research,
    # RECALL→recall, ANSWER→direct_answer (handled by Cognition).
    name: str

    # One-line human/SLM-readable purpose. Goes into the PLAN-stage
    # prompt — keep it ≤ 60 tokens to preserve SLM context budget.
    description: str

    # Free-form hints for the SLM about WHEN to pick this skill.
    # Rendered as bullet sub-lines under the description in
    # `SkillRegistry.available_for_prompt()`. Example for `research`:
    #   ["the question is about a current event",
    #    "the user explicitly asked to search",
    #    "the topic is post-training-cutoff"]
    trigger_hints: list[str]

    # JSON-Schema-ish descriptor of `execute(...)` kwargs (same shape
    # as `Tool.args_schema`). Rendered under the trigger hints so the
    # SLM knows what to emit. Example for `research`:
    #   {"query": {"type":"string","required":True,
    #              "desc":"3-8 keywords (NOT a sentence)"}}
    args_schema: dict[str, dict[str, Any]]

    # CognitiveLoop PLAN-stage filter knobs. See module docstring.
    cost_tier: CostTier
    requires_network: bool

    # Actuator gating. Read-only skills default False/False. A skill
    # that toggles a switch / sends an email / writes to a shared
    # resource sets side_effects=True; if Architect ack is required
    # BEFORE the call lands, also set requires_confirmation=True.
    side_effects: bool
    requires_confirmation: bool

    # OPTIONAL PLAN-stage surface. Skills that should appear in the
    # CognitiveLoop's verb menu (the SLM picks from these every turn)
    # MUST also expose a class-level `plan_verb` (ALL CAPS token, e.g.
    # "SEARCH") and MAY expose `plan_arg_hint` (placeholder rendered
    # next to the verb, e.g. '"<3-8 keywords>"'). Skills WITHOUT a
    # `plan_verb` are still invocable directly via the registry but
    # stay hidden from the PLAN menu — useful for orchestrator-only
    # capabilities (`identity` triggered by Brain's regex shortcut)
    # and background-only ones (`self_reflect`, `proactive_learning`,
    # `recurring_research` fired by Heartbeat / TaskScheduler).
    #
    # These are NOT formal Protocol members because the registry reads
    # them via `getattr(skill, "plan_verb", None)` — that way a Skill
    # author who doesn't care about the PLAN menu writes ZERO extra
    # lines. Document the convention here so contributors discover it.
    #
    # plan_verb: str | None = None        # ALL CAPS token or None
    # plan_arg_hint: str | None = None    # e.g. '"<3-8 keywords>"' or None

    async def execute(
        self,
        ctx: SkillContext,
        **kwargs: Any,
    ) -> SkillResult:
        """Run the skill. MUST never raise.

        Implementations should validate kwargs at the top and return a
        `SkillResult(ok=False, error=...)` on bad input rather than
        raising. The SkillRegistry catches anything that DOES escape
        and converts it to a failure result, but defending at the
        boundary is the contract.
        """
        ...

    async def aclose(self) -> None:
        """Release any held resources. Called once at agent shutdown by
        main.py via the registry. Skills that hold no resources should
        provide an empty implementation."""
        ...
