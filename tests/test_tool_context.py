"""Tests for ToolContext + ToolRegistry.bind_context (SMELL B fix).

Mirrors the SkillContext contract for Tools: an opt-in
``bind_context`` hook that lets a third-party Tool read its
``cfg.plugins.<key>`` slice without a factory-closure dance.
"""
from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any

import pytest

from tools import ToolContext, ToolRegistry
from tools.base import ToolResult


def _make_ctx(plugins: dict[str, dict[str, Any]] | None = None) -> ToolContext:
    return ToolContext(
        soul=object(),
        stm=object(),
        monitor=object(),
        provider=object(),
        archive_path="/tmp/archive.md",
        designation="TEST",
        architect_name="Eason",
        architect_honorific="Architect",
        plugins_config=MappingProxyType(plugins or {}),
    )


class _BoundTool:
    """Tool that opts into bind_context."""
    name = "bound_tool"
    description = "x"
    args_schema = {}
    side_effects = False
    requires_confirmation = False

    def __init__(self):
        self.ctx: ToolContext | None = None
        self.bind_calls = 0

    def bind_context(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.bind_calls += 1

    async def call(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True)

    async def aclose(self) -> None:
        pass


class _PlainTool:
    """Tool that does NOT implement bind_context (legacy/first-party)."""
    name = "plain_tool"
    description = "x"
    args_schema = {}
    side_effects = False
    requires_confirmation = False

    async def call(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True)

    async def aclose(self) -> None:
        pass


class _CrashingTool:
    name = "crash_tool"
    description = "x"
    args_schema = {}
    side_effects = False
    requires_confirmation = False

    def bind_context(self, ctx: ToolContext) -> None:
        raise RuntimeError("simulated bind failure")

    async def call(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True)

    async def aclose(self) -> None:
        pass


def test_tool_context_is_frozen_dataclass():
    ctx = _make_ctx()
    assert dataclasses.is_dataclass(ctx)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.designation = "NOPE"  # type: ignore[misc]


def test_tool_context_plugins_config_is_mapping_proxy():
    ctx = _make_ctx({"k": {"x": 1}})
    # MappingProxyType is read-only
    with pytest.raises(TypeError):
        ctx.plugins_config["k"] = {}  # type: ignore[index]


def test_bind_context_delivers_to_already_registered_tools():
    reg = ToolRegistry()
    t = _BoundTool()
    reg.register(t)
    assert t.ctx is None
    ctx = _make_ctx({"bound_tool": {"foo": "bar"}})
    reg.bind_context(ctx)
    assert t.ctx is ctx
    assert t.bind_calls == 1


def test_bind_context_skips_tools_without_bind_method():
    reg = ToolRegistry()
    plain = _PlainTool()
    reg.register(plain)
    # Should not raise even though plain has no bind_context.
    reg.bind_context(_make_ctx())


def test_register_after_bind_context_delivers_to_new_tool():
    reg = ToolRegistry()
    ctx = _make_ctx({"bound_tool": {"k": 1}})
    reg.bind_context(ctx)
    t = _BoundTool()
    reg.register(t)
    assert t.ctx is ctx
    assert t.bind_calls == 1


def test_bind_context_none_clears_stored_context():
    reg = ToolRegistry()
    ctx = _make_ctx()
    reg.bind_context(ctx)
    reg.bind_context(None)
    # New registrations after clear should NOT receive a context.
    t = _BoundTool()
    reg.register(t)
    assert t.ctx is None


def test_bind_context_exception_in_tool_is_isolated():
    reg = ToolRegistry()
    crash = _CrashingTool()
    good = _BoundTool()
    reg.register(crash)
    reg.register(good)
    ctx = _make_ctx()
    # The crashing tool must not break the bind loop for the good tool.
    reg.bind_context(ctx)
    assert good.ctx is ctx


def test_bind_context_idempotent_for_re_registration():
    reg = ToolRegistry()
    ctx = _make_ctx()
    reg.bind_context(ctx)
    t = _BoundTool()
    reg.register(t)
    # Manual second bind reflects the latest context.
    ctx2 = _make_ctx({"x": {"y": 2}})
    reg.bind_context(ctx2)
    assert t.ctx is ctx2
    assert t.bind_calls == 2
