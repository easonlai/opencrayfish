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

from .base import Tool, ToolResult

log = logging.getLogger(__name__)


class ToolRegistry:
    """Owns the live set of `Tool` instances for one agent process."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Optional callback invoked AFTER any register/unregister so a
        # dashboard publisher (state/tools.json) can re-snapshot the
        # catalogue without polling. Mirrors SkillRegistry's pattern.
        self._change_listener: Callable[[], None] | None = None

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
        loudly at boot than silently shadow a previously-registered tool)."""
        name = getattr(tool, "name", None)
        if not name or not isinstance(name, str):
            raise ValueError(
                f"Tool {tool!r} must expose a non-empty string `name` attribute."
            )
        if name in self._tools:
            raise ValueError(
                f"Tool name {name!r} is already registered. "
                "Pick a unique name or unregister first."
            )
        self._tools[name] = tool
        log.info(
            "TOOL registered name=%s side_effects=%s requires_confirmation=%s",
            name,
            getattr(tool, "side_effects", False),
            getattr(tool, "requires_confirmation", False),
        )
        self._notify_change()

    def unregister(self, name: str) -> Tool | None:
        """Remove and return the tool with this name, or None if absent.
        Does NOT call `aclose()` — caller decides what to do with it."""
        removed = self._tools.pop(name, None)
        if removed is not None:
            self._notify_change()
        return removed

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

    # ---------- lifecycle -----------------------------------------------------

    async def aclose_all(self) -> None:
        """Close every registered tool, isolating failures so one bad
        shutdown can't block the rest. Called once by main.py at exit."""
        for name, tool in list(self._tools.items()):
            try:
                await tool.aclose()
            except Exception:
                log.exception("TOOL aclose name=%s failed (continuing)", name)
