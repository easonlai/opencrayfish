"""tools.base — Minimal Tool plugin contract.

Goal of this module:
  Provide the smallest possible seam so that future capabilities (RAG over
  archive, calendar lookup, HTTP fetcher, weather, MCP bridge, …) can be
  added without re-threading new constructor kwargs through Brain /
  CognitiveLoop / Heartbeat / TaskScheduler.

Design notes
------------
* `Tool` is a runtime-checkable Protocol. Concrete tools don't have to
  inherit from it — they just need the right surface (`name`,
  `description`, `args_schema`, async `call`, async `aclose`). This keeps
  the existing `SearXNG` class lightweight: it gains a few attributes and
  a `call(...)` adapter, nothing else.

* `ToolResult` is a frozen dataclass so call sites can pattern-match on
  `ok` / `error` without juggling tuple positions. `data` is `Any`
  because each tool returns its own shape (search hits, recall lines,
  weather payload, …); the consumer that asked for the tool knows what
  to expect.

* `args_schema` is a tiny JSON-Schema-ish dict, NOT a full validator.
  Its only job is to be rendered into a PLAN-stage SLM prompt later
  ("here are the tools you may pick, with their argument shapes"). We
  deliberately don't pull in `pydantic` or `jsonschema` for this — the
  SLM's prompt budget is too small for a real schema and we already
  validate inputs at each tool's `call()` boundary.

* `side_effects` and `requires_confirmation` are forward-looking flags
  for a future PositiveFilter / Architect-ack gate. SearXNG and other
  read-only tools default to `False`/`False`. A future
  home-automation actuator would set both to `True`.

This file intentionally has NO dependency on the rest of the codebase
so it stays cheap to import and reuse.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolResult:
    """Uniform return shape for every Tool.call().

    `ok=True`  → `data` holds the tool's payload (whatever shape that tool
                 documents). `error` is empty.
    `ok=False` → `data` is None and `error` carries a short human-readable
                 reason. Callers should NOT raise on a failed ToolResult;
                 they should degrade (log + continue) the same way the
                 existing SearXNG call sites already do.

    `latency_ms` is recorded by `ToolRegistry.call(...)` (not by tools
    themselves) so every tool gets uniform timing for free.

    `meta` is a small dict for tool-specific telemetry (hit counts,
    backend used, cache hit, …) that should NOT be inlined into `data`.
    """

    ok: bool
    data: Any = None
    error: str = ""
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContext:
    """Stable handles passed to Tools that opt into runtime context.

    Mirrors ``core.skills.base.SkillContext`` so the Tool layer has
    symmetric access to cross-cutting subsystems (operator
    ``cfg.plugins.<key>`` slice, persona handle, vitals, …) WITHOUT
    every existing Tool having to learn a new ``call()`` signature.

    Opt-in model
    ------------
    The ``Tool`` protocol's ``call(**kwargs)`` is unchanged. A Tool
    that wants context implements an OPTIONAL ``bind_context(ctx)``
    method; ``ToolRegistry.bind_context(ctx)`` calls it once per Tool
    (and again on any newly-registered Tool). The Tool stashes
    whatever it needs on ``self`` and uses it from ``call()``. Tools
    that don't implement ``bind_context`` are left alone \u2014 zero
    impact on the existing in-tree Tools.

    The recommended pattern is::

        class MyTool:
            manifest = ToolManifest(name="mytool", description="...",
                                    config_key="mytool")
            ...

            def bind_context(self, ctx: ToolContext) -> None:
                self._cfg = ctx.plugins_config.get(
                    self.manifest.config_key or self.manifest.name, {}
                )

    Frozen so Tools cannot mutate shared state through the context.
    All collaborators (``soul``, ``stm``, ``monitor``, ``provider``)
    carry their own locking; the ``Mapping`` fields use
    ``MappingProxyType`` for read-only-by-construction safety.

    NOT included on purpose:
      * ``ToolRegistry`` itself \u2014 would be self-referential, and a
        Tool that wants to call another Tool should compose through
        a Skill instead (the Skill IS the orchestration layer).
      * ``Brain`` / ``Emotions`` / ``Heartbeat`` / ``Scheduler``
        \u2014 same orchestrator-not-collaborator rule as ``SkillContext``.
    """

    soul: Any
    """SoulHandler. ``Any`` here to avoid a hard import cycle (tools is
    imported VERY early); concrete type is ``core.soul_handler.SoulHandler``."""

    stm: Any
    """ShortTermMemory handle. Same Any-typing reason as ``soul``."""

    monitor: Any
    """Monitor handle (vitals snapshot, stress flag). Same Any-typing."""

    provider: Any
    """Provider handle for tools that want to make an SLM call."""

    archive_path: str
    """Filesystem path to ``memory/archive.md`` for tools that need
    direct LTM read access (most should go through the ``archive_read``
    Tool wrapper instead)."""

    designation: str
    """The agent's own identity string (mirrors ``SkillContext.designation``)."""

    architect_name: str
    """Operator's name (e.g. ``"Eason"``)."""

    architect_honorific: str
    """Operator's salutation prefix (e.g. ``"Boss"``)."""

    extras: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    """Free-form extension slot for cross-cutting subsystems that don't
    warrant their own typed field yet. Same convention as
    ``SkillContext.extras``."""

    plugins_config: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    """Per-plugin configuration namespace from ``cfg.plugins``. A Tool
    retrieves ITS slice via
    ``ctx.plugins_config.get(self.manifest.config_key or self.manifest.name, {})``.
    This is the seam that lets third-party authors take operator config
    without ever touching ``core/config.py``."""


@runtime_checkable
class Tool(Protocol):
    """The plugin contract.

    A class satisfies `Tool` purely by shape — no inheritance required.
    See `tools/searxng.py` for the canonical example.
    """

    # Stable identifier used by the registry AND injected into PLAN-stage
    # SLM prompts. Convention: lowercase, snake_case, verb-y
    # (`web_search`, `archive_recall`, `http_fetch`, …).
    name: str

    # One-line human/SLM-readable purpose. Kept short because it goes
    # into the model's context budget.
    description: str

    # JSON-Schema-ish argument descriptor. Example for web_search:
    #   {
    #     "query": {"type": "string", "required": True,
    #               "desc": "3-8 keywords; not a sentence."},
    #     "limit": {"type": "int",    "required": False, "default": 5},
    #   }
    args_schema: dict[str, dict[str, Any]]

    # Read-only tools default these to False. Anything that mutates the
    # outside world (sends a message, toggles a switch, writes to a
    # shared store) MUST set side_effects=True. requires_confirmation
    # gates the call behind a future Architect-ack prompt.
    side_effects: bool
    requires_confirmation: bool

    async def call(self, **kwargs: Any) -> ToolResult:
        """Execute the tool. MUST never raise — wrap failures in
        `ToolResult(ok=False, error=...)`. Implementations should validate
        their kwargs at the top and return an `ok=False` ToolResult on
        bad input rather than raising."""
        ...

    async def aclose(self) -> None:
        """Release any held resources (HTTP clients, sockets, files).
        Called once at agent shutdown by main.py via the registry."""
        ...
