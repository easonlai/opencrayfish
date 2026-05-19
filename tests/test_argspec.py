"""tests/test_argspec.py — Regression guard for the runtime argspec validator.

Covers both layers (Skill + Tool) since one validator powers both
registries (see core/skills/argspec.py). The unit tests exercise the
validator directly; the integration tests cover the wired hook points
inside `SkillRegistry.invoke()` and `ToolRegistry.call()`.

When this file fails, do NOT relax the assertions — the validator is
a boundary check that the rest of the framework relies on (e.g. the
PLAN-stage SLM can ship `"42"` for an `int` slot and the Skill body
will receive `42`).
"""
from __future__ import annotations

from typing import Any

import pytest

from core.skills.argspec import _coerce, validate_args
from core.skills.base import CostTier, SkillContext, SkillResult
from core.skills.registry import SkillRegistry
from tools.base import Tool, ToolResult
from tools.registry import ToolRegistry


# ---------- unit: validate_args -----------------------------------------------


def test_empty_schema_is_passthrough() -> None:
    n, e, u = validate_args(None, {"a": 1, "b": "x"})
    assert n == {"a": 1, "b": "x"}
    assert e == []
    assert u == []

    n, e, u = validate_args({}, {"a": 1})
    assert n == {"a": 1}
    assert e == []
    assert u == []


def test_default_is_injected_when_missing() -> None:
    schema = {"limit": {"type": "int", "default": 5}}
    n, e, u = validate_args(schema, {})
    assert n == {"limit": 5}
    assert e == []


def test_missing_required_arg_surfaces_error() -> None:
    schema = {"query": {"type": "string", "required": True}}
    n, e, u = validate_args(schema, {})
    assert n == {}
    assert len(e) == 1
    assert "query" in e[0]
    assert "missing required" in e[0]


def test_string_to_int_coercion() -> None:
    schema = {"limit": {"type": "int", "required": True}}
    n, e, u = validate_args(schema, {"limit": "42"})
    assert n == {"limit": 42}
    assert e == []


def test_string_to_float_coercion() -> None:
    schema = {"score": {"type": "float", "required": True}}
    n, e, u = validate_args(schema, {"score": "0.7"})
    assert n["score"] == pytest.approx(0.7)
    assert e == []


def test_string_to_bool_coercion() -> None:
    schema = {"flag": {"type": "bool", "required": True}}
    for raw, want in (("true", True), ("false", False), ("1", True),
                      ("0", False), ("yes", True), ("no", False),
                      ("TRUE", True), ("False", False)):
        n, e, _ = validate_args(schema, {"flag": raw})
        assert e == [], f"{raw!r} should coerce to {want!r}"
        assert n["flag"] is want


def test_bool_rejected_for_int_slot() -> None:
    """``bool`` IS-A ``int`` in Python — silently allowing it would
    mask programming errors. The validator must reject it explicitly."""
    schema = {"limit": {"type": "int", "required": True}}
    n, e, _ = validate_args(schema, {"limit": True})
    assert n == {}
    assert len(e) == 1
    assert "bool" in e[0].lower()


def test_unknown_type_token_is_passthrough() -> None:
    """Third-party schemas may use type tokens we don't model yet —
    don't reject those; just pass the value through."""
    schema = {"x": {"type": "custom_thing"}}
    n, e, u = validate_args(schema, {"x": object()})
    assert "x" in n
    assert e == []


def test_unknown_kwarg_is_surfaced_but_passes() -> None:
    schema = {"q": {"type": "string"}}
    n, e, u = validate_args(schema, {"q": "hi", "hint": "bonus"})
    assert n == {"q": "hi", "hint": "bonus"}
    assert e == []
    assert u == ["hint"]


def test_malformed_schema_entry_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    """A non-dict spec entry must NOT crash dispatch — log and skip."""
    schema = {"q": "not a dict"}  # type: ignore[dict-item]
    with caplog.at_level("WARNING", logger="core.skills.argspec"):
        n, e, u = validate_args(schema, {"q": "hi"})  # type: ignore[arg-type]
    assert n == {"q": "hi"}
    assert e == []
    assert any("not a dict" in rec.message for rec in caplog.records)


def test_cannot_coerce_garbage_string_to_int() -> None:
    schema = {"limit": {"type": "int", "required": True}}
    n, e, _ = validate_args(schema, {"limit": "abc"})
    assert n == {}
    assert len(e) == 1
    assert "cannot coerce" in e[0]


def test_coerce_helper_numeric_to_string() -> None:
    coerced, was, err = _coerce("string", 42)
    assert coerced == "42"
    assert was is True
    assert err is None


def test_coerce_helper_list_native() -> None:
    coerced, was, err = _coerce("list", [1, 2, 3])
    assert coerced == [1, 2, 3]
    assert was is False
    assert err is None


# ---------- integration: SkillRegistry.invoke() -------------------------------


class _FakeSkillWithSchema:
    """Skill stub that records the kwargs it actually received."""

    name = "stub_skill"
    description = "test"
    trigger_hints: list[str] = []
    args_schema: dict[str, dict[str, Any]] = {
        "limit": {"type": "int", "required": True},
        "tag":   {"type": "string", "default": "x"},
    }
    cost_tier: CostTier = "cheap"
    requires_network = False
    side_effects = False
    requires_confirmation = False

    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        self.received = kwargs
        return SkillResult(ok=True, summary=f"got {kwargs!r}")

    async def aclose(self) -> None:
        return None


async def test_skill_registry_rejects_missing_required(tmp_path) -> None:
    reg = SkillRegistry(audit_feed=tmp_path / "skills.jsonl")
    skill = _FakeSkillWithSchema()
    reg.register(skill)

    # The stub Skill never reads from ctx (drift-prevention, mirrors
    # the pattern in tests/test_example_echo_integration.py).
    ctx: Any = object()
    result = await reg.invoke("stub_skill", ctx)
    assert result.ok is False
    assert "argspec" in (result.error or "")
    assert "limit" in (result.error or "")
    # Skill body must NOT have been called.
    assert skill.received is None


async def test_skill_registry_coerces_and_dispatches(tmp_path) -> None:
    reg = SkillRegistry(audit_feed=tmp_path / "skills.jsonl")
    skill = _FakeSkillWithSchema()
    reg.register(skill)

    ctx: Any = object()
    result = await reg.invoke("stub_skill", ctx, limit="7")
    assert result.ok is True
    assert skill.received == {"limit": 7, "tag": "x"}


# ---------- integration: ToolRegistry.call() ----------------------------------


class _FakeToolWithSchema(Tool):
    name = "stub_tool"
    description = "test"
    args_schema: dict[str, dict[str, Any]] = {
        "limit": {"type": "int", "required": True},
    }

    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    async def call(self, **kwargs: Any) -> ToolResult:
        self.received = kwargs
        return ToolResult(ok=True, data={"echo": kwargs})

    async def aclose(self) -> None:
        return None


async def test_tool_registry_rejects_type_mismatch() -> None:
    reg = ToolRegistry()
    tool = _FakeToolWithSchema()
    reg.register(tool)

    result = await reg.call("stub_tool", limit="not_a_number")
    assert result.ok is False
    assert "argspec" in (result.error or "")
    assert tool.received is None


async def test_tool_registry_coerces_and_dispatches() -> None:
    reg = ToolRegistry()
    tool = _FakeToolWithSchema()
    reg.register(tool)

    result = await reg.call("stub_tool", limit="3")
    assert result.ok is True
    assert tool.received == {"limit": 3}
