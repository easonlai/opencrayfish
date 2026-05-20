"""Unit tests for ``tools.mcp_bridge``.

These tests exercise the bridge entirely against stub sessions so they
run offline in CI. End-to-end behaviour against the public Microsoft
Learn MCP server is covered separately by
``scripts/smoke_mcp_bridge.py`` (network-dependent, not run in unit CI).
"""
from __future__ import annotations

from typing import Any

import pytest

from tools import ToolRegistry
from tools.base import ToolResult
from tools.mcp_bridge import (
    McpBridge,
    McpRemoteTool,
    McpServerSpec,
    json_schema_to_args_schema,
    make_mcp_bridge,
    mcp_result_to_payload,
    parse_server_specs,
)


# ---------------------------------------------------------------------------
# parse_server_specs
# ---------------------------------------------------------------------------


def test_parse_server_specs_empty_returns_empty_list():
    assert parse_server_specs({}) == []
    assert parse_server_specs({"mcp_bridge": {}}) == []
    assert parse_server_specs({"mcp_bridge": {"servers": []}}) == []


def test_parse_server_specs_picks_up_minimal_entry():
    specs = parse_server_specs(
        {"mcp_bridge": {"servers": [
            {"name": "mslearn", "url": "https://learn.microsoft.com/api/mcp"}
        ]}}
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "mslearn"
    assert spec.url == "https://learn.microsoft.com/api/mcp"
    assert spec.transport == "streamable_http"
    assert spec.tool_prefix is None
    assert spec.effective_prefix == "mslearn"
    assert spec.timeout_seconds == pytest.approx(30.0)
    assert spec.headers == {}


def test_parse_server_specs_applies_optional_knobs():
    specs = parse_server_specs(
        {"mcp_bridge": {"servers": [
            {
                "name": "remote",
                "url": "https://x.example/mcp",
                "tool_prefix": "x",
                "timeout_seconds": "12.5",
                "headers": {"Authorization": "Bearer abc"},
            }
        ]}}
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.tool_prefix == "x"
    assert spec.effective_prefix == "x"
    assert spec.timeout_seconds == pytest.approx(12.5)
    assert spec.headers == {"Authorization": "Bearer abc"}


def test_parse_server_specs_skips_bad_entries():
    specs = parse_server_specs(
        {"mcp_bridge": {"servers": [
            "not-a-dict",
            {"url": "https://x"},               # missing name
            {"name": "", "url": "https://x"},   # blank name
            {"name": "x"},                       # missing url
            {"name": "y", "url": "https://y", "transport": "stdio"},  # unsupported transport
            {"name": "good", "url": "https://good"},
        ]}}
    )
    assert [s.name for s in specs] == ["good"]


def test_parse_server_specs_rejects_non_dict_top_level():
    assert parse_server_specs({"mcp_bridge": "broken"}) == []
    assert parse_server_specs({"mcp_bridge": {"servers": "broken"}}) == []


def test_make_mcp_bridge_returns_none_when_no_servers():
    assert make_mcp_bridge({}) is None
    assert make_mcp_bridge({"mcp_bridge": {"servers": []}}) is None


def test_make_mcp_bridge_returns_bridge_when_configured():
    bridge = make_mcp_bridge(
        {"mcp_bridge": {"servers": [{"name": "x", "url": "https://x"}]}}
    )
    assert isinstance(bridge, McpBridge)
    assert bridge.server_names == ["x"]


# ---------------------------------------------------------------------------
# json_schema_to_args_schema
# ---------------------------------------------------------------------------


def test_json_schema_to_args_schema_basic():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "search query"},
            "limit": {"type": "integer", "description": "max results"},
        },
        "required": ["query"],
    }
    out = json_schema_to_args_schema(schema)
    assert out == {
        "query": {"type": "string", "required": True, "desc": "search query"},
        "limit": {"type": "int", "required": False, "desc": "max results"},
    }


def test_json_schema_to_args_schema_handles_unknown_type():
    schema = {
        "type": "object",
        "properties": {
            "x": {"type": "weird-custom-thing"},
        },
    }
    out = json_schema_to_args_schema(schema)
    assert out["x"]["type"] == "any"


def test_json_schema_to_args_schema_handles_type_list():
    schema = {
        "type": "object",
        "properties": {
            "x": {"type": ["null", "string"]},
        },
    }
    out = json_schema_to_args_schema(schema)
    assert out["x"]["type"] == "string"


def test_json_schema_to_args_schema_preserves_default():
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 5},
        },
    }
    out = json_schema_to_args_schema(schema)
    assert out["limit"]["default"] == 5


def test_json_schema_to_args_schema_invalid_input_returns_empty():
    assert json_schema_to_args_schema(None) == {}
    assert json_schema_to_args_schema("not-a-dict") == {}
    assert json_schema_to_args_schema({"type": "object"}) == {}
    assert json_schema_to_args_schema(
        {"type": "object", "properties": "not-a-dict"}
    ) == {}


# ---------------------------------------------------------------------------
# mcp_result_to_payload
# ---------------------------------------------------------------------------


class _StubTextBlock:
    """Mimics mcp.types.TextContent enough for the renderer."""
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _StubResourceBlock:
    """Mimics a resource link block enough for the renderer."""
    def __init__(self, uri: str):
        self.type = "resource_link"
        self.uri = uri
        self.text = None


class _StubCallResult:
    def __init__(
        self,
        *,
        content: list[Any],
        is_error: bool = False,
        structured: Any = None,
    ):
        self.content = content
        self.isError = is_error
        self.structuredContent = structured


def test_mcp_result_to_payload_concatenates_text_blocks():
    res = _StubCallResult(content=[
        _StubTextBlock("hello"),
        _StubTextBlock("world"),
    ])
    payload = mcp_result_to_payload(res)
    assert payload["text"] == "hello\nworld"
    assert len(payload["blocks"]) == 2
    assert payload["blocks"][0]["type"] == "text"
    assert payload["blocks"][0]["text"] == "hello"
    assert payload["structured"] is None


def test_mcp_result_to_payload_picks_up_resource_uri():
    res = _StubCallResult(content=[_StubResourceBlock("https://x/y")])
    payload = mcp_result_to_payload(res)
    assert payload["text"] == ""
    assert payload["blocks"][0]["uri"] == "https://x/y"


def test_mcp_result_to_payload_carries_structured_content():
    res = _StubCallResult(content=[], structured={"k": "v"})
    payload = mcp_result_to_payload(res)
    assert payload["structured"] == {"k": "v"}


# ---------------------------------------------------------------------------
# McpRemoteTool + McpBridge (with stub session)
# ---------------------------------------------------------------------------


class _StubTool:
    """Mimics an mcp.types.Tool entry from list_tools()."""
    def __init__(
        self, name: str, description: str, input_schema: dict[str, Any]
    ):
        self.name = name
        self.description = description
        self.title = None
        self.inputSchema = input_schema


class _StubListToolsResp:
    def __init__(self, tools: list[_StubTool]):
        self.tools = tools


class _StubInitResult:
    class _Info:
        def __init__(self):
            self.name = "stub-server"
            self.version = "0.0.1"
    def __init__(self):
        self.serverInfo = self._Info()


class _StubSession:
    """In-memory ClientSession stand-in for tests."""
    def __init__(
        self,
        tools: list[_StubTool],
        *,
        call_result: _StubCallResult | None = None,
        call_raises: Exception | None = None,
    ):
        self._tools = tools
        self._call_result = call_result
        self._call_raises = call_raises
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.initialized = False
        self.closed = False

    async def initialize(self) -> _StubInitResult:
        self.initialized = True
        return _StubInitResult()

    async def list_tools(self) -> _StubListToolsResp:
        return _StubListToolsResp(self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None,
    ) -> _StubCallResult:
        self.calls.append((name, arguments))
        if self._call_raises is not None:
            raise self._call_raises
        assert self._call_result is not None, "test forgot to set call_result"
        return self._call_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False


class _StubTransport:
    """Async context manager that yields the (read, write, get_id) triple
    streamablehttp_client returns."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return ("READ", "WRITE", lambda: "session-id")

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


def _make_bridge_with_stub(
    *, tools: list[_StubTool], call_result: _StubCallResult | None = None,
    call_raises: Exception | None = None,
    spec_name: str = "stub",
) -> tuple[McpBridge, _StubSession, _StubTransport]:
    """Construct a bridge whose connect_all() uses stub transport + session."""
    spec = McpServerSpec(name=spec_name, url="http://stub/mcp")
    bridge = McpBridge([spec])
    transport = _StubTransport()
    session = _StubSession(
        tools, call_result=call_result, call_raises=call_raises,
    )

    def fake_streamablehttp_client(url, headers=None, timeout=30.0):
        assert url == spec.url
        return transport

    class FakeClientSession:
        def __new__(cls, read, write):
            # Ignore the read/write streams (the stub doesn't care).
            assert read == "READ"
            assert write == "WRITE"
            return session

    # Patch internal helpers by calling the private connect path directly.
    return bridge, session, transport, fake_streamablehttp_client, FakeClientSession  # type: ignore[return-value]


async def test_connect_all_registers_remote_tools():
    tools = [
        _StubTool(
            "microsoft_docs_search",
            "Search Microsoft docs.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    ]
    bridge, session, transport, sh_client, cs_cls = _make_bridge_with_stub(
        tools=tools,
        call_result=_StubCallResult(content=[_StubTextBlock("hello")]),
        spec_name="mslearn",
    )
    registry = ToolRegistry()
    # Drive the private connect path directly with the stubs.
    await bridge._connect_one(
        bridge._specs[0], registry, sh_client, cs_cls,
    )
    assert session.initialized
    assert registry.has("mcp__mslearn__microsoft_docs_search")
    manifest = registry.manifest("mcp__mslearn__microsoft_docs_search")
    assert manifest is not None
    assert manifest.side_effects is True  # conservative posture
    assert manifest.requires_caps == ("network.outbound",)
    assert "query" in manifest.args_schema
    assert manifest.args_schema["query"]["required"] is True
    assert manifest.extras["mcp_server"] == "mslearn"
    assert manifest.extras["mcp_tool_name"] == "microsoft_docs_search"
    await bridge.aclose()
    assert transport.exited is True


async def test_remote_tool_call_forwards_to_session():
    tools = [
        _StubTool("ping", "ping",
                  {"type": "object", "properties": {"msg": {"type": "string"}}}),
    ]
    bridge, session, _t, sh_client, cs_cls = _make_bridge_with_stub(
        tools=tools,
        call_result=_StubCallResult(content=[_StubTextBlock("pong")]),
    )
    registry = ToolRegistry()
    await bridge._connect_one(bridge._specs[0], registry, sh_client, cs_cls)
    # Round-trip via registry.call so argspec + latency stamping run.
    res = await registry.call("mcp__stub__ping", msg="hi")
    assert res.ok is True
    assert isinstance(res, ToolResult)
    assert res.data["text"] == "pong"
    assert res.meta["mcp_server"] == "stub"
    assert res.meta["mcp_tool"] == "ping"
    assert session.calls == [("ping", {"msg": "hi"})]
    await bridge.aclose()


async def test_remote_tool_surface_is_error_as_ok_false():
    tools = [
        _StubTool("flaky", "f",
                  {"type": "object", "properties": {}}),
    ]
    err_result = _StubCallResult(
        content=[_StubTextBlock("upstream blew up")], is_error=True,
    )
    bridge, _s, _t, sh_client, cs_cls = _make_bridge_with_stub(
        tools=tools, call_result=err_result,
    )
    registry = ToolRegistry()
    await bridge._connect_one(bridge._specs[0], registry, sh_client, cs_cls)
    res = await registry.call("mcp__stub__flaky")
    assert res.ok is False
    assert "upstream blew up" in res.error
    assert res.data is not None
    await bridge.aclose()


async def test_remote_tool_catches_session_exception():
    tools = [
        _StubTool("flaky", "f",
                  {"type": "object", "properties": {}}),
    ]
    bridge, _s, _t, sh_client, cs_cls = _make_bridge_with_stub(
        tools=tools, call_raises=RuntimeError("boom"),
    )
    registry = ToolRegistry()
    await bridge._connect_one(bridge._specs[0], registry, sh_client, cs_cls)
    res = await registry.call("mcp__stub__flaky")
    assert res.ok is False
    assert "RuntimeError" in res.error
    assert "boom" in res.error
    await bridge.aclose()


async def test_remote_tool_call_with_no_live_session_returns_error():
    spec = McpServerSpec(name="never_connected", url="http://x")
    bridge = McpBridge([spec])
    tool = McpRemoteTool(
        bridge=bridge,
        server_name="never_connected",
        remote_name="anything",
        registered_name="mcp__never_connected__anything",
        description="x",
        args_schema={},
    )
    res = await tool.call()
    assert res.ok is False
    assert "no live session" in res.error
    await bridge.aclose()


async def test_connect_all_with_no_specs_returns_empty_list():
    bridge = McpBridge([])
    registry = ToolRegistry()
    out = await bridge.connect_all(registry)
    assert out == []
    await bridge.aclose()


async def test_aclose_is_idempotent():
    bridge = McpBridge([])
    await bridge.aclose()
    # Second call must not raise.
    await bridge.aclose()


async def test_connect_all_after_aclose_raises():
    bridge = McpBridge([McpServerSpec(name="x", url="http://x")])
    await bridge.aclose()
    with pytest.raises(RuntimeError, match="aclose"):
        await bridge.connect_all(ToolRegistry())


async def test_duplicate_prefix_registration_collision_is_isolated():
    """Two upstream tools that produce the same registered name (only
    realistic if an operator misconfigures two servers with the same
    prefix) MUST not abort the connect loop."""
    tools = [
        _StubTool("dup", "first",
                  {"type": "object", "properties": {}}),
        _StubTool("dup", "second",
                  {"type": "object", "properties": {}}),
    ]
    bridge, _s, _t, sh_client, cs_cls = _make_bridge_with_stub(
        tools=tools,
        call_result=_StubCallResult(content=[_StubTextBlock("x")]),
    )
    registry = ToolRegistry()
    # Should not raise even though the second `dup` collides.
    count = await bridge._connect_one(
        bridge._specs[0], registry, sh_client, cs_cls,
    )
    assert count == 1
    assert registry.has("mcp__stub__dup")
    await bridge.aclose()
