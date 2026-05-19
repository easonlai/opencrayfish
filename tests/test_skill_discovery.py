"""Tests for the entry-points-based Skill discovery layer.

Covers:
  * Successful discovery + registration of a class-style entry-point.
  * Successful discovery + registration of a factory-callable entry-point.
  * Isolation: a broken entry-point (import error / instantiation
    error) is logged and skipped, NOT bubbled.
  * Duplicate-name rejection: a discovered Skill whose name collides
    with a first-party one is dropped with a warning, NOT bubbled
    (so the agent boots with the first-party version intact).
  * Empty group: a clean codebase with zero third-party Skills
    returns ``[]`` and logs at INFO (not WARNING).

We don't install real packages — instead we monkeypatch
``importlib.metadata.entry_points`` to return a fabricated list, so
the test is fast and hermetic.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.skills import SkillManifest, SkillRegistry
from core.skills.discovery import (
    SKILL_ENTRY_POINT_GROUP,
    discover_external_skills,
)


# ---------------------------------------------------------------------------
# Fixtures: fake Skill classes and a fake EntryPoint shim
# ---------------------------------------------------------------------------


class _FakeWeatherSkill:
    """Class-style entry-point: discovery should call __init__()."""
    manifest = SkillManifest(
        name="fake_weather",
        description="Fake weather lookup for tests.",
        plan_verb="WEATHER",
        cost_tier="cheap",
    )
    name = "fake_weather"
    description = "Fake weather lookup for tests."

    async def execute(self, ctx: Any, **kw: Any) -> Any:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def _fake_translate_factory() -> Any:
    """Factory-callable entry-point: discovery should call it with no args."""

    class _Translate:
        manifest = SkillManifest(
            name="fake_translate",
            description="Fake translate skill for tests.",
            plan_verb="TRANSLATE",
            cost_tier="cheap",
        )
        name = "fake_translate"
        description = "Fake translate skill for tests."

        async def execute(self, ctx: Any, **kw: Any) -> Any:
            raise NotImplementedError

        async def aclose(self) -> None:
            return None

    return _Translate()


class _BrokenSkill:
    """A class whose __init__ raises — discovery must isolate this."""

    def __init__(self) -> None:
        raise RuntimeError("simulated package init failure")


class _ShadowingSkill:
    """A discovered Skill whose name collides with a first-party one.
    The registry must reject this on duplicate-name; discovery must
    log + skip (NOT bubble) so the boot continues."""

    manifest = SkillManifest(
        name="fake_weather",  # collides with _FakeWeatherSkill
        description="Shadowing skill.",
        plan_verb="WEATHER2",
        cost_tier="cheap",
    )
    name = "fake_weather"
    description = "Shadowing skill."

    async def execute(self, ctx: Any, **kw: Any) -> Any:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class _FakeEntryPoint:
    """Mimics ``importlib.metadata.EntryPoint`` enough for discovery.

    Only the ``name`` / ``value`` / ``load`` / ``group`` surface is
    exercised — the real EntryPoint has more shape we don't need.
    """

    def __init__(self, name: str, value: str, target: Any) -> None:
        self.name = name
        self.value = value
        self.group = SKILL_ENTRY_POINT_GROUP
        self._target = target

    def load(self) -> Any:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, eps: list[Any]) -> None:
    """Patch ``importlib.metadata.entry_points`` AT THE DISCOVERY SITE.

    We patch the symbol the discovery module imported into its own
    namespace (``core.skills.discovery.entry_points``) rather than
    the source module — the former is what discovery actually calls.
    """
    def fake_entry_points(group: str = "") -> list[Any]:
        assert group == SKILL_ENTRY_POINT_GROUP
        return list(eps)

    monkeypatch.setattr(
        "core.skills.discovery.entry_points",
        fake_entry_points,
    )


def test_discovers_class_style_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "fake_weather",
            "tests.fake:_FakeWeatherSkill",
            _FakeWeatherSkill,
        ),
    ])
    reg = SkillRegistry()
    names = discover_external_skills(reg)
    assert names == ["fake_weather"]
    assert reg.has("fake_weather")
    m = reg.manifest("fake_weather")
    assert m is not None and m.plan_verb == "WEATHER"


def test_discovers_factory_callable_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "fake_translate",
            "tests.fake:_fake_translate_factory",
            _fake_translate_factory,
        ),
    ])
    reg = SkillRegistry()
    names = discover_external_skills(reg)
    assert names == ["fake_translate"]


def test_isolates_broken_entry_point(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    # Two entry-points: one good, one whose class raises in __init__.
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("fake_weather", "x:Y", _FakeWeatherSkill),
        _FakeEntryPoint("broken", "x:Broken", _BrokenSkill),
    ])
    reg = SkillRegistry()
    with caplog.at_level("WARNING"):
        names = discover_external_skills(reg)
    # Only the good one made it; broken is dropped not raised.
    assert names == ["fake_weather"]
    # The broken entry-point should have been logged.
    assert any("broken" in rec.getMessage() for rec in caplog.records)


def test_isolates_load_failure(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    # An entry-point whose ``load()`` raises (simulating ImportError).
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "import_fails",
            "x:DoesNotExist",
            ImportError("no module x"),
        ),
        _FakeEntryPoint("fake_weather", "x:Y", _FakeWeatherSkill),
    ])
    reg = SkillRegistry()
    with caplog.at_level("WARNING"):
        names = discover_external_skills(reg)
    assert names == ["fake_weather"]


def test_rejects_duplicate_name_without_bubbling(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    reg = SkillRegistry()
    # First-party registration: fake_weather is already there.
    reg.register(_FakeWeatherSkill())
    # Discovery then encounters a Skill with the same name — must NOT bubble.
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("shadow", "x:Y", _ShadowingSkill),
    ])
    with caplog.at_level("WARNING"):
        names = discover_external_skills(reg)
    assert names == []  # discovery skipped the duplicate
    assert reg.has("fake_weather")  # original survives
    # The shadower should have been logged as rejected.
    assert any(
        "rejected by registry" in rec.getMessage() or "shadow" in rec.getMessage()
        for rec in caplog.records
    )


def test_empty_group_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    reg = SkillRegistry()
    assert discover_external_skills(reg) == []
    assert reg.names() == []
