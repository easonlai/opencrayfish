"""tools.registry — Lookup + lifecycle for `Tool` plugins.

Single owning collection so subsystems can ask `registry.get("web_search")`
instead of having a typed `searxng=` constructor kwarg threaded through
every layer.

Design notes
------------
* Stateless wrt. the tools themselves — the registry holds references but
  does not mutate tool internals. Each tool owns its own resources
  (HTTP client, file handles, …) and exposes `aclose()` for cleanup.

* `call(name, **kwargs)` is the ONLY way callers should invoke a tool
  through the registry. It guarantees:
    - unknown-tool returns a uniform `ToolResult(ok=False, error=...)`
    - latency_ms is measured by the registry, not by each tool
    - one structured log line per call (tool name, ok flag, latency,
      first 80 chars of error if any) so we get uniform observability
      without each tool re-implementing logging
    - any unexpected exception inside `tool.call(...)` is caught and
      converted to `ToolResult(ok=False, ...)` so a buggy plugin can
      never crash the calling subsystem

* `aclose_all()` is fire-and-forget per tool — one bad shutdown does
  not block the others. main.py calls this once at SIGINT/SIGTERM.

* `available_for_prompt()` produces the compact tool catalogue we'll
  feed into the PLAN-stage SLM prompt later (when the CognitiveLoop's
  hard-coded SEARCH/RECALL/ANSWER verbs migrate to a true tool-router).
  Format is intentionally markdown-ish and brief because the SLM's
  context budget is tight.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

# Argspec lives under core.skills because it's the same validator on
# both layers — kept in one place to avoid drift.
from core.skills.argspec import validate_args

from .base import Tool, ToolContext, ToolResult
from .manifest import (
    SUPPORTED_TOOL_PROTOCOL_VERSIONS,
    WELL_KNOWN_TOOL_CAPABILITIES,
    ToolManifest,
    resolve_tool_manifest,
)

log = logging.getLogger(__name__)


class ToolRegistry:
    """Owns the live set of `Tool` instances for one agent process."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Parallel map: tool name -> resolved ToolManifest. Populated
        # at register() so consumers (PLAN-stage prompt, dashboard
        # catalogue, bootstrap_validate) read uniform manifest objects
        # rather than poking scattered class attributes. Legacy Tools
        # that don't declare a `manifest` attribute get one synthesized
        # from their scattered fields via `resolve_tool_manifest`.
        self._manifests: dict[str, ToolManifest] = {}
        # Optional callback invoked AFTER any register/unregister so a
        # dashboard publisher (state/tools.json) can re-snapshot the
        # catalogue without polling. Mirrors SkillRegistry's pattern.
        self._change_listener: Callable[[], None] | None = None
        # Shared ToolContext for Tools that opt into runtime context
        # via ``bind_context(ctx)``. Set once by main.py after the
        # registry is constructed (see ``bind_context`` below). Stays
        # None for unit tests and other callers that don't need it,
        # in which case Tools just won't get a bind_context() call.
        self._context: ToolContext | None = None

    # ---------- registration --------------------------------------------------

    def set_change_listener(self, cb: Callable[[], None] | None) -> None:
        """Install (or clear with None) a fn called after every
        register/unregister. Replaces any previous listener — single
        owner pattern; main.py is currently the only caller."""
        self._change_listener = cb

    def _notify_change(self) -> None:
        cb = self._change_listener
        if cb is None:
            return
        try:
            cb()
        except Exception:
            # A buggy listener must NEVER break registration itself.
            log.exception("TOOL change_listener raised (continuing)")

    # ---------- registration --------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Add a tool. Raises ValueError on duplicate name (we'd rather fail
        loudly at boot than silently shadow a previously-registered tool).

        Resolves the Tool's manifest (declared or synthesized) and
        stores it alongside the instance. A Tool whose manifest fails
        validation (e.g. unsupported ``compat_version``) is rejected
        here rather than at first invocation — fail loud.
        """
        # Resolve manifest FIRST so a malformed Tool is rejected before
        # it enters the live registry. Any ValueError surfaces directly
        # to the caller (main.py at boot, or the discovery loop).
        manifest = resolve_tool_manifest(tool)
        name = manifest.name
        # Cross-check the legacy `name` attribute (if present) agrees
        # with the manifest. A mismatch is a packaging error.
        attr_name = getattr(tool, "name", None)
        if isinstance(attr_name, str) and attr_name and attr_name != name:
            raise ValueError(
                f"Tool {tool!r}: manifest.name={name!r} disagrees with "
                f"class attribute name={attr_name!r}. Pick one source of "
                "truth (manifest wins, but the legacy attribute should "
                "either match or be removed)."
            )
        if name in self._tools:
            raise ValueError(
                f"Tool name {name!r} is already registered. "
                "Pick a unique name or unregister first."
            )
        self._tools[name] = tool
        self._manifests[name] = manifest
        log.info(
            "TOOL registered name=%s protocol=%s side_effects=%s "
            "requires_confirmation=%s requires_caps=%s config_key=%s",
            name,
            manifest.compat_version,
            manifest.side_effects,
            manifest.requires_confirmation,
            ",".join(manifest.requires_caps) or "-",
            manifest.config_key or "-",
        )
        # If a ToolContext is already bound on the registry, deliver
        # it to this newly-registered Tool (third-party discovery
        # happens AFTER main.py's bind_context call, so most tools
        # arrive here with a context already waiting). A failing
        # bind_context is isolated so a broken plug-in can't poison
        # registration of the rest.
        self._maybe_bind(name, tool)
        self._notify_change()

    def unregister(self, name: str) -> Tool | None:
        """Remove and return the tool with this name, or None if absent.
        Does NOT call `aclose()` — caller decides what to do with it."""
        removed = self._tools.pop(name, None)
        self._manifests.pop(name, None)
        if removed is not None:
            self._notify_change()
        return removed

    # ---------- context binding ----------------------------------------------

    def bind_context(self, ctx: ToolContext | None) -> None:
        """Install a ToolContext for Tools that opt into runtime context.

        Each registered Tool that exposes a ``bind_context(ctx)``
        method receives a single call with this context object;
        Tools without that method are silently skipped (existing
        in-tree Tools all fall in this bucket and are unaffected).
        Subsequent ``register()`` calls also deliver the context
        immediately, so a Tool discovered via entry-points after the
        first ``bind_context()`` still gets one.

        Pass ``None`` to clear the bound context (mainly for tests).
        A failing ``bind_context`` on one Tool does NOT block the
        rest \u2014 same fail-isolation contract as the other lifecycle
        hooks on this registry.
        """
        self._context = ctx
        if ctx is None:
            return
        for name, tool in list(self._tools.items()):
            self._maybe_bind(name, tool)

    def _maybe_bind(self, name: str, tool: Tool) -> None:
        """Internal helper: deliver the bound context to one Tool if
        it implements ``bind_context``. Exception-isolated."""
        ctx = self._context
        if ctx is None:
            return
        binder = getattr(tool, "bind_context", None)
        if binder is None or not callable(binder):
            return
        try:
            binder(ctx)
        except Exception:
            log.exception(
                "TOOL bind_context name=%s raised (continuing)", name,
            )

    # ---------- manifest accessors -------------------------------------------

    def manifest(self, name: str) -> ToolManifest | None:
        """Return the resolved manifest for ``name``, or None if unknown."""
        return self._manifests.get(name)

    def manifests(self) -> dict[str, ToolManifest]:
        """Return a defensive copy of the name -> manifest map."""
        return dict(self._manifests)

    # ---------- lookup --------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        """Return the tool with this name, or None. Callers that want a
        hard failure on missing should branch on the None themselves so
        the failure mode is explicit at the call site."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    # ---------- invocation ----------------------------------------------------

    async def call(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute a tool by name. ALWAYS returns a ToolResult — never
        raises. Use this from subsystems instead of `tool.call(...)` so
        we get uniform timing + logging + crash isolation for free."""
        tool = self._tools.get(name)
        if tool is None:
            log.warning("TOOL call name=%s status=unknown", name)
            return ToolResult(ok=False, error=f"unknown tool: {name!r}")

        # Argspec boundary check (see core.skills.argspec). Reject
        # type-mismatch / missing-required kwargs BEFORE invoking the
        # tool body; safe coercions (str→int, etc.) are applied.
        manifest = self._manifests.get(name)
        if manifest is not None and manifest.args_schema:
            normalized, errors, unknowns = validate_args(
                manifest.args_schema, kwargs
            )
            if errors:
                log.warning(
                    "TOOL call name=%s status=argspec_invalid errors=%s",
                    name, errors,
                )
                meta: dict[str, Any] = {"argspec_errors": errors}
                if unknowns:
                    meta["argspec_unknowns"] = unknowns
                return ToolResult(
                    ok=False,
                    error=f"argspec: {'; '.join(errors)}",
                    meta=meta,
                )
            kwargs = normalized

        t0 = time.perf_counter()
        try:
            result = await tool.call(**kwargs)
        except Exception as exc:  # belt-and-braces: a misbehaving plugin
            ms = int((time.perf_counter() - t0) * 1000)
            log.exception(
                "TOOL call name=%s status=raised latency_ms=%d kwargs_keys=%s",
                name,
                ms,
                sorted(kwargs.keys()),
            )
            return ToolResult(
                ok=False,
                error=f"{exc.__class__.__name__}: {exc}",
                latency_ms=ms,
            )
        ms = int((time.perf_counter() - t0) * 1000)
        # Normalize: a plugin that returns the wrong type gets coerced to a
        # failure ToolResult rather than poisoning the caller.
        if not isinstance(result, ToolResult):
            log.error(
                "TOOL call name=%s status=bad_return_type type=%s latency_ms=%d",
                name,
                type(result).__name__,
                ms,
            )
            return ToolResult(
                ok=False,
                error=f"tool {name!r} returned {type(result).__name__}, expected ToolResult",
                latency_ms=ms,
            )
        # Stamp registry-measured latency unless the tool already set one.
        if result.latency_ms == 0:
            result = ToolResult(
                ok=result.ok,
                data=result.data,
                error=result.error,
                latency_ms=ms,
                meta=result.meta,
            )
        log.info(
            "TOOL call name=%s status=%s latency_ms=%d%s",
            name,
            "ok" if result.ok else "fail",
            result.latency_ms,
            f" error={result.error[:80]!r}" if not result.ok else "",
        )
        return result

    # ---------- prompt surface ------------------------------------------------

    def available_for_prompt(self) -> str:
        """Render the tool catalogue for inclusion in an SLM PLAN prompt.

        Output is short markdown — keep it under a few hundred tokens
        even with a dozen tools, because the SLM's context budget is
        tight on edge hardware.
        """
        if not self._tools:
            return "(no tools available)"
        lines: list[str] = []
        for name in sorted(self._tools.keys()):
            t = self._tools[name]
            lines.append(f"- {name}: {t.description}")
            for arg_name, arg_meta in t.args_schema.items():
                req = "required" if arg_meta.get("required", False) else "optional"
                arg_type = arg_meta.get("type", "any")
                desc = arg_meta.get("desc", "")
                tail = f" — {desc}" if desc else ""
                lines.append(f"    - {arg_name} ({arg_type}, {req}){tail}")
        return "\n".join(lines)

    # ---------- bootstrap validation -----------------------------------------

    def bootstrap_validate(
        self,
        *,
        plugins_config: dict[str, Any] | None = None,
        strict: bool = True,
    ) -> list[str]:
        """Fail-loud cross-validation called once after all Tools have
        been registered in ``main.py`` (and after entry-points discovery
        has run).

        Mirrors ``SkillRegistry.bootstrap_validate``. Checks per Tool:
          1. ``compat_version`` in ``SUPPORTED_TOOL_PROTOCOL_VERSIONS``
             (also enforced at ``register()`` \u2014 re-checked here as a
             belt-and-braces guard).
          2. If ``config_key`` is set, the matching ``cfg.plugins.<key>``
             namespace exists when ``plugins_config`` is supplied.
             Skipped when ``plugins_config`` is ``None`` (e.g. unit
             tests that don't wire config).
          3. Capability tokens in ``requires_caps`` are logged when
             unknown so third-party packages can ship their own
             conventions without breaking validation.

        Returns:
            A list of human-readable problem descriptions. Empty list
            means everything checks out.

        Raises:
            RuntimeError: when ``strict=True`` and at least one problem
                was found. The exception message lists every problem
                so the operator gets the full picture in one go.
        """
        problems: list[str] = []
        for name, manifest in self._manifests.items():
            # (1) Protocol version
            if manifest.compat_version not in SUPPORTED_TOOL_PROTOCOL_VERSIONS:
                problems.append(
                    f"tool {name!r}: compat_version "
                    f"{manifest.compat_version!r} not in "
                    f"{sorted(SUPPORTED_TOOL_PROTOCOL_VERSIONS)}"
                )
            # (2) Config namespace presence
            if manifest.config_key and plugins_config is not None:
                if manifest.config_key not in plugins_config:
                    problems.append(
                        f"tool {name!r}: declares config_key="
                        f"{manifest.config_key!r} but cfg.plugins has "
                        f"no such key (available: "
                        f"{sorted(plugins_config.keys())})"
                    )
            # (3) Capability tokens \u2014 warning only
            for cap in manifest.requires_caps:
                if cap not in WELL_KNOWN_TOOL_CAPABILITIES:
                    log.info(
                        "TOOL bootstrap_validate name=%s unknown "
                        "capability token %r (allowed but undocumented)",
                        name, cap,
                    )

        if problems and strict:
            raise RuntimeError(
                "Tool bootstrap validation failed:\n  - "
                + "\n  - ".join(problems)
            )
        if problems:
            for p in problems:
                log.warning("TOOL bootstrap_validate problem: %s", p)
        else:
            log.info(
                "TOOL bootstrap_validate ok tools=%d",
                len(self._manifests),
            )
        return problems

    # ---------- lifecycle -----------------------------------------------------

    async def aclose_all(self) -> None:
        """Close every registered tool, isolating failures so one bad
        shutdown can't block the rest. Called once by main.py at exit."""
        for name, tool in list(self._tools.items()):
            try:
                await tool.aclose()
            except Exception:
                log.exception("TOOL aclose name=%s failed (continuing)", name)
