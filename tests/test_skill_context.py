"""tests.test_skill_context — unit tests for ``SkillContext.extras``.

P2.2 added an optional ``extras: Mapping[str, Any]`` slot to the frozen
``SkillContext`` dataclass so cross-cutting subsystems (emotions,
empathy directive, future RAG retriever, …) can flow into a Skill
WITHOUT a coordinated refactor of every existing Skill — only the
Skill that needs the new key reads it.

These tests pin down the four contract guarantees:

  1. Default value is an EMPTY, IMMUTABLE mapping (no `None` to type-check).
  2. The field is keyword-only on construction (frozen dataclass).
  3. The container is frozen — attempting to mutate ``ctx.extras`` raises
     ``TypeError`` (the ``MappingProxyType`` blocks ``__setitem__``).
  4. The dataclass itself remains frozen — ``ctx.extras = {}`` raises
     ``dataclasses.FrozenInstanceError``.

We don't construct full ``ToolRegistry`` / ``SoulHandler`` / etc.
collaborators — we pass ``object()`` sentinels so the test stays a
PURE check of the dataclass shape. The runtime types of those fields
are only enforced by the type checker, not at runtime, so this is safe.
"""
from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from core.skills.base import SkillContext


def _make_ctx(**overrides) -> SkillContext:
    """Construct a SkillContext with sentinel placeholders for all
    typed fields. Tests override only ``extras`` (or other fields they
    care about).

    Using ``object()`` for the typed collaborators is intentional: the
    SkillContext dataclass doesn't validate field types at runtime, so
    we can keep these tests entirely independent of the heavy real
    subsystems (which require config + filesystem + provider + HTTP).
    """
    base = dict(
        tools=object(),
        soul=object(),
        stm=object(),
        monitor=object(),
        provider=object(),
        archive_path="/tmp/archive.md",
        designation="Test-Agent",
        architect_name="Test Architect",
        architect_honorific="Boss",
    )
    base.update(overrides)
    return SkillContext(**base)


def test_skill_context_extras_defaults_to_empty_mapping():
    """No-extras case must still expose a non-None mapping so Skills
    can do ``ctx.extras.get("emotions")`` without a None-check.
    """
    ctx = _make_ctx()
    assert ctx.extras is not None
    assert len(ctx.extras) == 0
    # And mapping-shaped (not list / tuple / None).
    assert list(ctx.extras.keys()) == []


def test_skill_context_extras_default_is_immutable():
    """The default empty extras must reject mutation — otherwise a
    Skill could accidentally pollute the boot-time singleton.
    """
    ctx = _make_ctx()
    with pytest.raises(TypeError):
        # MappingProxyType raises TypeError on __setitem__.
        ctx.extras["k"] = "v"  # type: ignore[index]


def test_skill_context_extras_accepts_mapping_proxy():
    """Caller-supplied MappingProxyType wraps a private dict so the
    underlying dict is not exposed to Skills.
    """
    backing = {"emotions": "sentinel-emotions", "empathy": "sentinel-empathy"}
    ctx = _make_ctx(extras=MappingProxyType(backing))

    assert ctx.extras["emotions"] == "sentinel-emotions"
    assert ctx.extras["empathy"] == "sentinel-empathy"
    # ``in`` operator works on Mapping.
    assert "emotions" in ctx.extras
    # And iteration works.
    assert sorted(ctx.extras.keys()) == ["emotions", "empathy"]


def test_skill_context_extras_via_proxy_rejects_mutation():
    """A MappingProxyType-wrapped dict must reject mutation through
    the proxy, even though the underlying dict is mutable.
    """
    backing: dict[str, object] = {"k": "v"}
    ctx = _make_ctx(extras=MappingProxyType(backing))

    with pytest.raises(TypeError):
        ctx.extras["k"] = "new"  # type: ignore[index]
    with pytest.raises(TypeError):
        del ctx.extras["k"]  # type: ignore[arg-type]


def test_skill_context_extras_field_assignment_is_frozen():
    """The dataclass is frozen — ``ctx.extras = ...`` MUST raise.

    Frozen-by-construction guarantees the boot-time SkillContext can be
    safely shared across concurrent ``execute()`` calls.
    """
    ctx = _make_ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.extras = {"new": "value"}  # type: ignore[misc]


def test_skill_context_other_fields_still_present():
    """Regression guard: adding ``extras`` must not have shifted or
    removed any pre-existing field. List them explicitly so a future
    accidental rename is caught immediately.
    """
    ctx = _make_ctx()
    field_names = {f.name for f in dataclasses.fields(ctx)}
    expected = {
        "tools", "soul", "stm", "monitor", "provider",
        "archive_path", "designation", "architect_name",
        "architect_honorific", "extras",
    }
    assert field_names == expected
