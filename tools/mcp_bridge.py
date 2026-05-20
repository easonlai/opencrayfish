"""tools.mcp_bridge — Connect to remote MCP servers and expose each
upstream MCP tool as a first-class OpenCrayFish ``Tool``.

WHAT THIS IS
------------
The `Model Context Protocol <https://modelcontextprotocol.io>`_ is the
emerging open spec for letting an agent talk to a third-party "tool
server" over a stable wire format (Streamable HTTP today; stdio + SSE
also defined). Servers publish a typed catalogue (``tools/list``) and
each tool is invoked via ``tools/call`` with a JSON-Schema-described
argument shape. The Microsoft Learn MCP Server
(https://learn.microsoft.com/api/mcp) is the worked example bundled
in the README — it exposes ``microsoft_docs_search``,
``microsoft_code_sample_search`` and ``microsoft_docs_fetch`` over
Streamable HTTP with no authentication.

This module is the "MCP bridge" the README/CONTRIBUTING.md call out
as the canonical multi-server plug-in. For every configured server:

  1. Open a long-lived ``ClientSession`` over Streamable HTTP.
  2. Call ``tools/list`` once and translate each remote tool's
     ``inputSchema`` (JSON Schema) into an OpenCrayFish ``args_schema``.
  3. Register one ``McpRemoteTool`` per remote tool in the shared
     ``ToolRegistry`` so the rest of the agent (Skills, Cognitive
     Loop PLAN menu, dashboard) sees them like any in-tree Tool.
  4. Hold the session open via an ``AsyncExitStack`` so subsequent
     ``call()`` invocations reuse the same TLS / framing handshake.
  5. Close the stack on agent shutdown.

DESIGN CHOICES
--------------
* **One Tool per upstream MCP tool** (not one bridge Tool with a
  ``tool=`` arg). This way the SLM's PLAN menu sees concrete,
  individually-described capabilities — same UX as ``web_search``.
* **Naming convention:** ``mcp__<prefix>__<tool>``. ``<prefix>``
  defaults to the server's ``name`` from config so an operator can
  publish multiple servers without collisions. Example:
  ``mcp__mslearn__microsoft_docs_search``.
* **Schema translation is permissive.** JSON Schema covers more
  shapes than the simplified OpenCrayFish ``args_schema`` (anyOf,
  pattern, etc.). We extract type + required + description and pass
  unknown shapes through as ``"any"`` rather than dropping them —
  the upstream server still validates on its side.
* **Errors degrade, never raise.** ``McpRemoteTool.call`` always
  returns a ``ToolResult``. Network / protocol / decode failures
  become ``ok=False, error="..."``.
* **Lifecycle owned by main.py.** ``McpBridge.aclose()`` is called
  explicitly in the shutdown block BEFORE ``tool_registry.aclose_all()``,
  so the per-tool ``aclose`` is a no-op (avoids double-close of the
  shared session).
* **Optional dependency.** ``mcp`` is imported only inside
  ``connect_all``; an operator who never configures ``cfg.plugins.mcp_bridge``
  is unaffected if the SDK isn't installed.

CONFIG
------
``cfg.plugins.mcp_bridge`` shape (all per-server fields except
``name`` and ``url`` are optional)::

    plugins:
      mcp_bridge:
        servers:
          - name: mslearn
            url: https://learn.microsoft.com/api/mcp
            # transport: streamable_http   # only one supported today
            # tool_prefix: mslearn         # overrides the registered-name prefix
            # timeout_seconds: 30
            # headers: { Authorization: "Bearer ..." }

SECURITY NOTE
-------------
An MCP server you ``url:`` here can return arbitrary tool definitions
that the agent then executes on the operator's behalf. Only point
``mcp_bridge`` at servers you trust. The bridge does NOT sandbox
upstream tool calls; ``side_effects`` is conservatively reported as
True for every registered MCP tool so the future Architect-ack gate
clamps blast radius until per-tool annotations propagate.
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import ToolResult
from .manifest import ToolManifest
from .registry import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from mcp import ClientSession

log = logging.getLogger(__name__)


# Registered-name template: ``mcp__<prefix>__<tool>``. Double-underscore
# is a visual separator that survives the manifest's "no whitespace"
# check and makes the source server obvious in dashboard listings and
# audit logs.
_NAME_TEMPLATE = "mcp__{prefix}__{tool}"


@dataclass(frozen=True)
class McpServerSpec:
    """One MCP server entry from ``cfg.plugins.mcp_bridge.servers``."""

    name: str
    url: str
    transport: str = "streamable_http"
    tool_prefix: str | None = None  # falls back to ``name``
    timeout_seconds: float = 30.0
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def effective_prefix(self) -> str:
        return self.tool_prefix or self.name


def parse_server_specs(plugins_cfg: dict[str, Any]) -> list[McpServerSpec]:
    """Parse ``cfg.plugins.mcp_bridge.servers`` into typed specs.

    Returns an empty list when the namespace is missing or empty. Bad
    entries (missing ``name`` / ``url``) are skipped with a WARNING log
    line — one broken server must not poison the others.
    """
    bridge_cfg = plugins_cfg.get("mcp_bridge") or {}
    if not isinstance(bridge_cfg, dict):
        log.warning(
            "MCP cfg.plugins.mcp_bridge is %s, expected dict — skipping",
            type(bridge_cfg).__name__,
        )
        return []
    raw_servers = bridge_cfg.get("servers") or []
    if not isinstance(raw_servers, list):
        log.warning(
            "MCP cfg.plugins.mcp_bridge.servers is %s, expected list — skipping",
            type(raw_servers).__name__,
        )
        return []

    specs: list[McpServerSpec] = []
    for idx, entry in enumerate(raw_servers):
        if not isinstance(entry, dict):
            log.warning(
                "MCP cfg.plugins.mcp_bridge.servers[%d] is %s, expected dict — skipping",
                idx, type(entry).__name__,
            )
            continue
        name = entry.get("name")
        url = entry.get("url")
        if not isinstance(name, str) or not name.strip():
            log.warning(
                "MCP server entry %d missing/invalid 'name' — skipping", idx,
            )
            continue
        if not isinstance(url, str) or not url.strip():
            log.warning(
                "MCP server entry %r missing/invalid 'url' — skipping", name,
            )
            continue
        transport = str(entry.get("transport") or "streamable_http").lower()
        if transport != "streamable_http":
            log.warning(
                "MCP server %r requests transport=%r; only "
                "'streamable_http' is supported today — skipping",
                name, transport,
            )
            continue
        headers_raw = entry.get("headers") or {}
        if not isinstance(headers_raw, dict):
            log.warning(
                "MCP server %r 'headers' is %s, expected dict — ignoring",
                name, type(headers_raw).__name__,
            )
            headers_raw = {}
        try:
            timeout = float(entry.get("timeout_seconds", 30.0))
        except (TypeError, ValueError):
            timeout = 30.0
        prefix = entry.get("tool_prefix")
        if prefix is not None and (not isinstance(prefix, str) or not prefix.strip()):
            prefix = None
        specs.append(McpServerSpec(
            name=name.strip(),
            url=url.strip(),
            transport=transport,
            tool_prefix=prefix.strip() if isinstance(prefix, str) else None,
            timeout_seconds=timeout,
            headers={str(k): str(v) for k, v in headers_raw.items()},
        ))
    return specs


# ---------------------------------------------------------------------------
# JSON Schema → OpenCrayFish args_schema translation
# ---------------------------------------------------------------------------


# JSON Schema "type" → OpenCrayFish args_schema "type" token (see
# core/skills/argspec.py::_TYPE_NATIVE for the accepted set).
_JSON_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "null": "any",
}


def json_schema_to_args_schema(input_schema: Any) -> dict[str, dict[str, Any]]:
    """Translate an MCP tool's JSON-Schema ``inputSchema`` into the
    flat OpenCrayFish ``args_schema`` shape.

    Returns an empty dict when ``input_schema`` isn't a usable object
    schema. Unknown property types map to ``"any"`` so the upstream
    server still gets the kwarg verbatim — its own validator is the
    authority for fine-grained shapes (anyOf, pattern, format, …)
    we don't model.
    """
    if not isinstance(input_schema, dict):
        return {}
    properties = input_schema.get("properties") or {}
    if not isinstance(properties, dict):
        return {}
    required = input_schema.get("required") or []
    required_set: set[str] = (
        set(required) if isinstance(required, list) else set()
    )

    out: dict[str, dict[str, Any]] = {}
    for arg_name, spec in properties.items():
        if not isinstance(arg_name, str) or not isinstance(spec, dict):
            continue
        raw_type = spec.get("type")
        if isinstance(raw_type, list):
            # JSON Schema permits a list of types; pick the first
            # non-null one for our simplified surface.
            picked = next(
                (t for t in raw_type if isinstance(t, str) and t != "null"),
                None,
            )
            type_token = _JSON_TYPE_MAP.get(str(picked or "").lower(), "any")
        elif isinstance(raw_type, str):
            type_token = _JSON_TYPE_MAP.get(raw_type.lower(), "any")
        else:
            type_token = "any"
        desc = spec.get("description") or spec.get("title") or ""
        entry: dict[str, Any] = {
            "type": type_token,
            "required": arg_name in required_set,
            "desc": str(desc).strip(),
        }
        if "default" in spec and spec["default"] is not None:
            entry["default"] = spec["default"]
        out[arg_name] = entry
    return out


# ---------------------------------------------------------------------------
# MCP CallToolResult → ToolResult.data payload
# ---------------------------------------------------------------------------


def mcp_result_to_payload(call_result: Any) -> dict[str, Any]:
    """Render an MCP ``CallToolResult`` into a JSON-serialisable dict.

    Shape::

        {
          "text": "<concatenated TextContent blocks>",
          "blocks": [{"type": "...", ...}, ...],
          "structured": <structuredContent dict | None>,
        }

    ``text`` is the most useful field for consumers that just want the
    natural-language answer (Skills, the Cognitive Loop synth pass,
    the dashboard). ``blocks`` preserves the full typed content list
    so future Skills can pick out ImageContent / ResourceLink.
    """
    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []
    raw_blocks = getattr(call_result, "content", None) or []
    for block in raw_blocks:
        block_type = getattr(block, "type", None) or block.__class__.__name__
        entry: dict[str, Any] = {"type": str(block_type)}
        text_val = getattr(block, "text", None)
        if isinstance(text_val, str):
            entry["text"] = text_val
            text_parts.append(text_val)
        # Generic best-effort: pull a few well-known optional fields if
        # the SDK populated them. We avoid model_dump() because it can
        # produce non-trivial structures we'd then have to clean.
        for opt in ("data", "mimeType", "uri", "resource", "annotations"):
            val = getattr(block, opt, None)
            if val is not None:
                # Pydantic models → dict for JSON safety; primitives pass through.
                if hasattr(val, "model_dump"):
                    try:
                        val = val.model_dump(exclude_none=True)
                    except Exception:
                        val = str(val)
                entry[opt] = val
        blocks.append(entry)

    structured = getattr(call_result, "structuredContent", None)
    if hasattr(structured, "model_dump"):
        try:
            structured = structured.model_dump(exclude_none=True)
        except Exception:
            structured = None

    return {
        "text": "\n".join(text_parts).strip(),
        "blocks": blocks,
        "structured": structured,
    }


# ---------------------------------------------------------------------------
# Remote Tool wrapper
# ---------------------------------------------------------------------------


class McpRemoteTool:
    """A single upstream MCP tool, presented as an OpenCrayFish Tool.

    Holds a reference back to the owning ``McpBridge`` and the remote
    tool's original name; ``call`` forwards through the bridge so all
    Tools sharing a server reuse the same long-lived session.

    ``aclose`` is intentionally a no-op: the underlying transport is
    owned by ``McpBridge.aclose()`` which main.py calls explicitly
    before ``tool_registry.aclose_all()``.
    """

    def __init__(
        self,
        *,
        bridge: McpBridge,
        server_name: str,
        remote_name: str,
        registered_name: str,
        description: str,
        args_schema: dict[str, dict[str, Any]],
    ) -> None:
        self._bridge = bridge
        self._server_name = server_name
        self._remote_name = remote_name
        # Conservative safety posture: MCP servers may expose
        # arbitrary side-effecting tools (file writes, network calls,
        # ticket creation, …) and the protocol's per-tool
        # ``annotations.destructive_hint`` / ``read_only_hint`` are
        # optional and rarely populated. Treat every MCP tool as
        # side-effecting so a future Architect-ack gate clamps it
        # until per-tool propagation lands.
        self.manifest = ToolManifest(
            name=registered_name,
            description=description,
            compat_version="tool-protocol/1",
            args_schema=args_schema,
            side_effects=True,
            requires_confirmation=False,
            requires_caps=("network.outbound",),
            extras={
                "mcp_server": server_name,
                "mcp_tool_name": remote_name,
            },
        )
        self.name = registered_name
        self.description = description
        self.args_schema = args_schema
        self.side_effects = True
        self.requires_confirmation = False

    async def call(self, **kwargs: Any) -> ToolResult:
        try:
            payload = await self._bridge.call_remote(
                self._server_name, self._remote_name, kwargs,
            )
        except _BridgeNotConnectedError as exc:
            return ToolResult(ok=False, error=f"mcp_bridge: {exc}")
        except Exception as exc:  # defensive — never let the tool raise
            return ToolResult(
                ok=False,
                error=f"{exc.__class__.__name__}: {exc}",
            )
        return payload

    async def aclose(self) -> None:
        # See class docstring — bridge owns the transport.
        return None


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class _BridgeNotConnectedError(RuntimeError):
    """Raised by ``McpBridge.call_remote`` when the session was never
    established (or has been closed). Internal — converted to a
    ``ToolResult(ok=False)`` by ``McpRemoteTool.call``."""


class McpBridge:
    """Owns MCP sessions for every configured server and registers one
    ``McpRemoteTool`` per discovered upstream tool.

    Lifecycle (driven by ``main.py``):

      1. ``McpBridge(specs)`` — cheap; no network I/O.
      2. ``await bridge.connect_all(tool_registry)`` — opens every
         session, lists tools, registers them in the registry.
      3. … agent runs, Tools dispatch through ``call_remote`` …
      4. ``await bridge.aclose()`` — closes every session.

    Failures during step 2 (one server unreachable, one tool listing
    malformed, …) are isolated per server: a single broken server
    only loses ITS tools, the rest still come up.
    """

    def __init__(self, specs: list[McpServerSpec]) -> None:
        self._specs = list(specs)
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._registered_names: list[str] = []
        self._closed = False

    @property
    def server_names(self) -> list[str]:
        return [s.name for s in self._specs]

    @property
    def registered_tool_names(self) -> list[str]:
        """Names of tools the bridge has registered in the ToolRegistry.

        Only populated after ``connect_all`` succeeds for at least one
        server.
        """
        return list(self._registered_names)

    async def connect_all(self, registry: ToolRegistry) -> list[str]:
        """Open every session, list its tools, register them.

        Returns the list of newly-registered tool names. Per-server
        failures are logged and skipped; the method itself never
        raises (so a single bad server doesn't abort agent boot).
        """
        if self._closed:
            raise RuntimeError(
                "McpBridge.connect_all called after aclose — construct a new bridge"
            )
        if not self._specs:
            log.info("MCP bridge: no servers configured — nothing to connect")
            return []

        # Lazy import so an operator who never configures MCP can skip
        # installing the SDK. A missing dependency surfaces here with
        # a clear, actionable error log — not at agent import time.
        try:
            from mcp import ClientSession  # noqa: F401  (re-exported below)
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            log.error(
                "MCP bridge: 'mcp' package not installed. "
                "Run `pip install mcp` (or remove cfg.plugins.mcp_bridge "
                "to disable). Skipping every configured server."
            )
            return []

        before = len(self._registered_names)
        for spec in self._specs:
            try:
                count = await self._connect_one(
                    spec, registry, streamablehttp_client, ClientSession,
                )
            except Exception:
                log.exception(
                    "MCP bridge: server %r connect/list/register failed — skipping",
                    spec.name,
                )
                continue
            log.info(
                "MCP bridge: server %r connected; registered %d tool(s)",
                spec.name, count,
            )
        return list(self._registered_names[before:])

    async def _connect_one(
        self,
        spec: McpServerSpec,
        registry: ToolRegistry,
        streamablehttp_client_fn: Any,
        client_session_cls: Any,
    ) -> int:
        """Open one server's session through the shared AsyncExitStack
        and register every advertised tool. Returns the registered count."""
        transport_ctx = streamablehttp_client_fn(
            spec.url,
            headers=spec.headers or None,
            timeout=spec.timeout_seconds,
        )
        read, write, _get_session_id = await self._stack.enter_async_context(
            transport_ctx,
        )
        session_ctx = client_session_cls(read, write)
        session = await self._stack.enter_async_context(session_ctx)
        init_result = await session.initialize()
        server_info = getattr(init_result, "serverInfo", None)
        log.info(
            "MCP bridge: server %r initialised (remote=%s v%s)",
            spec.name,
            getattr(server_info, "name", "?"),
            getattr(server_info, "version", "?"),
        )
        self._sessions[spec.name] = session

        tools_resp = await session.list_tools()
        registered = 0
        for remote in getattr(tools_resp, "tools", []) or []:
            remote_name = getattr(remote, "name", None)
            if not isinstance(remote_name, str) or not remote_name.strip():
                log.warning(
                    "MCP bridge: server %r returned tool with no name — skipping",
                    spec.name,
                )
                continue
            description = (
                getattr(remote, "description", None)
                or getattr(remote, "title", None)
                or f"MCP tool {remote_name!r} on server {spec.name!r}"
            )
            input_schema = getattr(remote, "inputSchema", None)
            args_schema = json_schema_to_args_schema(input_schema)
            registered_name = _NAME_TEMPLATE.format(
                prefix=spec.effective_prefix,
                tool=remote_name,
            )
            tool = McpRemoteTool(
                bridge=self,
                server_name=spec.name,
                remote_name=remote_name,
                registered_name=registered_name,
                description=str(description).strip(),
                args_schema=args_schema,
            )
            try:
                registry.register(tool)
            except ValueError as exc:
                # Duplicate name (e.g. operator misconfigured two
                # servers with the same prefix). Loud but isolated.
                log.warning(
                    "MCP bridge: registration of %r failed: %s — skipping",
                    registered_name, exc,
                )
                continue
            self._registered_names.append(registered_name)
            registered += 1
        return registered

    async def call_remote(
        self,
        server_name: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Forward a Tool call to the upstream MCP server.

        Returns a ToolResult — never raises. Surfaces protocol-level
        errors (``isError=True``) as ``ok=False`` so the caller's
        contract is uniform.
        """
        session = self._sessions.get(server_name)
        if session is None:
            raise _BridgeNotConnectedError(
                f"no live session for server {server_name!r}"
            )
        try:
            call_result = await session.call_tool(
                remote_name, arguments if arguments else None,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                error=f"mcp call_tool failed: {exc.__class__.__name__}: {exc}",
                meta={"mcp_server": server_name, "mcp_tool": remote_name},
            )

        payload = mcp_result_to_payload(call_result)
        is_error = bool(getattr(call_result, "isError", False))
        if is_error:
            # Surface the upstream error message (often in the first
            # text block) as ToolResult.error so callers can degrade
            # uniformly.
            err_text = payload.get("text") or "upstream MCP tool reported isError=True"
            return ToolResult(
                ok=False,
                error=err_text[:240],
                data=payload,
                meta={"mcp_server": server_name, "mcp_tool": remote_name},
            )
        return ToolResult(
            ok=True,
            data=payload,
            meta={
                "mcp_server": server_name,
                "mcp_tool": remote_name,
                "blocks": len(payload.get("blocks") or []),
            },
        )

    async def aclose(self) -> None:
        """Close every session opened in ``connect_all``.

        Idempotent — repeated calls after the first are no-ops. Safe
        to call even when ``connect_all`` was never invoked.
        """
        if self._closed:
            return
        self._closed = True
        try:
            await self._stack.aclose()
        except Exception:
            log.exception("MCP bridge: aclose() encountered an error (continuing)")
        finally:
            self._sessions.clear()


def make_mcp_bridge(plugins_cfg: dict[str, Any]) -> McpBridge | None:
    """Convenience factory used by ``main.py``.

    Returns ``None`` when no MCP servers are configured so the caller
    can skip the wiring entirely. Bad entries are logged + skipped by
    ``parse_server_specs``; an empty result returns ``None``.
    """
    specs = parse_server_specs(plugins_cfg or {})
    if not specs:
        return None
    return McpBridge(specs)
