"""Tests for the drop-in folder discovery layer (core.dropin + the
four ``discover_dropin_*`` surfaces).

Covers:
  * Successful registration via a flat ``PLUGIN = ClassName`` module.
  * Successful registration via a sub-package with ``__init__.py``.
  * ``PLUGINS = [A, B]`` multi-export contract.
  * Factory-callable + already-instantiated shapes accepted.
  * Helper modules (no ``PLUGIN`` / ``PLUGINS``) are silently skipped.
  * Broken modules (import error / class instantiation error) are
    isolated and logged, NOT bubbled.
  * Duplicate-name shadowing is rejected without bubbling.
  * Missing folder root is a no-op (returns ``[]``).
  * Deterministic load order across boots (sorted filesystem walk).
  * All four surfaces (skills/tools/connectors/backends) honour the
    same contract via the shared ``iter_dropin_plugins`` helper.

No real packages are installed and no real ``pip`` calls happen —
every test writes its own ``plugins/<surface>/`` tree under
``tmp_path`` and points the discovery function at it via the
``root=`` override.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from connectors import ConnectorRegistry
from connectors.discovery import discover_dropin_connectors
from core.dropin import (
    PLUGINS_ROOT_ENV,
    dropin_root,
    iter_dropin_plugins,
    surface_root,
)
from core.provider_manifest import discover_dropin_backends
from core.skills import SkillRegistry
from core.skills.discovery import discover_dropin_skills
from tools import ToolRegistry
from tools.discovery import discover_dropin_tools

# ---------------------------------------------------------------------------
# Minimal valid plug-in source strings (written to tmp_path)
# ---------------------------------------------------------------------------


_SKILL_SRC = '''
from core.skills.manifest import SkillManifest


class {cls}:
    manifest = SkillManifest(
        name="{name}",
        description="Drop-in test skill.",
        plan_verb="{verb}",
        cost_tier="cheap",
    )
    name = "{name}"
    description = "Drop-in test skill."

    async def execute(self, ctx, **kw):
        return None

    async def aclose(self):
        return None


PLUGIN = {cls}
'''


_TOOL_SRC = '''
from tools.manifest import ToolManifest


class {cls}:
    manifest = ToolManifest(
        name="{name}",
        description="Drop-in test tool.",
    )
    name = "{name}"
    description = "Drop-in test tool."

    async def call(self, **kw):
        return None


PLUGIN = {cls}
'''


_CONNECTOR_SRC = '''
from connectors.manifest import ConnectorManifest


class {cls}:
    manifest = ConnectorManifest(
        name="{name}",
        description="Drop-in test connector.",
    )
    name = "{name}"
    description = "Drop-in test connector."

    async def start(self):
        return None

    async def stop(self):
        return None


PLUGIN = {cls}
'''


_BACKEND_SRC = '''
class {cls}:
    name = "{name}"
    description = "Drop-in test backend."

    async def generate(self, prompt, **kw):
        return ""


PLUGIN = {cls}
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _skills_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins" / "skills"


def _tools_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins" / "tools"


def _connectors_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins" / "connectors"


def _backends_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins" / "backends"


# ---------------------------------------------------------------------------
# Root-resolution: env var + defaults
# ---------------------------------------------------------------------------


def test_dropin_root_defaults_to_cwd_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PLUGINS_ROOT_ENV, raising=False)
    assert dropin_root() == Path.cwd() / "plugins"


def test_dropin_root_honours_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(PLUGINS_ROOT_ENV, str(tmp_path / "custom"))
    assert dropin_root() == tmp_path / "custom"


def test_surface_root_composes_under_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(PLUGINS_ROOT_ENV, str(tmp_path))
    assert surface_root("skills") == tmp_path / "skills"
    assert surface_root("tools") == tmp_path / "tools"
    assert surface_root("connectors") == tmp_path / "connectors"
    assert surface_root("backends") == tmp_path / "backends"


def test_surface_root_rejects_unknown_surface() -> None:
    with pytest.raises(ValueError, match="unknown drop-in surface"):
        surface_root("ladders")


# ---------------------------------------------------------------------------
# iter_dropin_plugins: contract for the shared loader
# ---------------------------------------------------------------------------


def test_iter_returns_nothing_when_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    assert list(iter_dropin_plugins("skills", root=missing)) == []


def test_iter_skips_dotfiles_and_underscore_prefix(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    root.mkdir(parents=True)
    (root / "_private.py").write_text("PLUGIN = object()", encoding="utf-8")
    (root / ".hidden.py").write_text("PLUGIN = object()", encoding="utf-8")
    assert list(iter_dropin_plugins("skills", root=root)) == []


def test_iter_skips_folder_without_init(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    folder = root / "incomplete_pkg"
    folder.mkdir(parents=True)
    (folder / "skill.py").write_text("PLUGIN = object()", encoding="utf-8")
    assert list(iter_dropin_plugins("skills", root=root)) == []


def test_iter_sort_order_is_deterministic(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    root.mkdir(parents=True)
    for name in ("c.py", "a.py", "b.py"):
        (root / name).write_text(f"PLUGIN = '{name}'", encoding="utf-8")
    labels = [label for label, _ in iter_dropin_plugins("skills", root=root)]
    assert labels == ["a", "b", "c"]


def test_iter_handles_plugins_iterable(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    root.mkdir(parents=True)
    (root / "multi.py").write_text(
        "PLUGINS = ['a', 'b', 'c']", encoding="utf-8",
    )
    pairs = list(iter_dropin_plugins("skills", root=root))
    assert [raw for _, raw in pairs] == ["a", "b", "c"]


def test_iter_skips_helper_module_without_plugin_attr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    root = _skills_root(tmp_path)
    root.mkdir(parents=True)
    (root / "shared.py").write_text("HELPER = 42", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="core.dropin"):
        assert list(iter_dropin_plugins("skills", root=root)) == []
    assert any("helper module" in r.message for r in caplog.records)


def test_iter_isolates_broken_module(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    root = _skills_root(tmp_path)
    root.mkdir(parents=True)
    (root / "broken.py").write_text(
        "raise RuntimeError('boom at import')", encoding="utf-8",
    )
    (root / "good.py").write_text("PLUGIN = 'ok'", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="core.dropin"):
        pairs = list(iter_dropin_plugins("skills", root=root))
    # The broken module must NOT abort the walk — the good one
    # still surfaces.
    assert pairs == [("good", "ok")]
    assert any("broken" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Skills surface
# ---------------------------------------------------------------------------


def test_dropin_skills_registers_flat_file(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write(
        root / "weather.py",
        _SKILL_SRC.format(cls="WeatherSkill", name="weather", verb="WEATHER"),
    )
    reg = SkillRegistry()
    names = discover_dropin_skills(reg, root=root)
    assert names == ["weather"]
    assert reg.has("weather")


def test_dropin_skills_registers_sub_package(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    pkg = root / "translate"
    _write(
        pkg / "__init__.py",
        _SKILL_SRC.format(
            cls="TranslateSkill", name="translate", verb="TRANSLATE",
        ),
    )
    reg = SkillRegistry()
    names = discover_dropin_skills(reg, root=root)
    assert names == ["translate"]
    assert reg.has("translate")


def test_dropin_skills_rejects_duplicate_without_bubbling(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    root = _skills_root(tmp_path)
    _write(
        root / "a.py",
        _SKILL_SRC.format(cls="A", name="dupname", verb="DUP1"),
    )
    _write(
        root / "b.py",
        _SKILL_SRC.format(cls="B", name="dupname", verb="DUP2"),
    )
    reg = SkillRegistry()
    with caplog.at_level(logging.WARNING, logger="core.skills.discovery"):
        names = discover_dropin_skills(reg, root=root)
    # Sort order makes ``a.py`` win; ``b.py`` is rejected by registry.
    assert names == ["dupname"]
    assert any("rejected by registry" in r.message for r in caplog.records)


def test_dropin_skills_missing_root_is_noop(tmp_path: Path) -> None:
    assert discover_dropin_skills(
        SkillRegistry(), root=tmp_path / "no-such-dir",
    ) == []


# ---------------------------------------------------------------------------
# Tools surface
# ---------------------------------------------------------------------------


def test_dropin_tools_registers_flat_file(tmp_path: Path) -> None:
    root = _tools_root(tmp_path)
    _write(
        root / "home_assistant.py",
        _TOOL_SRC.format(cls="HATool", name="home_assistant"),
    )
    reg = ToolRegistry()
    names = discover_dropin_tools(reg, root=root)
    assert names == ["home_assistant"]
    assert reg.has("home_assistant")


def test_dropin_tools_accepts_factory_callable(tmp_path: Path) -> None:
    root = _tools_root(tmp_path)
    _write(
        root / "weather.py",
        _TOOL_SRC.format(cls="WTool", name="weather")
        + "\n\ndef factory():\n    return WTool()\n\nPLUGIN = factory\n",
    )
    reg = ToolRegistry()
    # We re-write PLUGIN to point at the factory rather than the class.
    (root / "weather.py").write_text(
        '''
from tools.manifest import ToolManifest

class WTool:
    manifest = ToolManifest(name="weather", description="x")
    name = "weather"
    description = "x"
    async def call(self, **kw):
        return None

def factory():
    return WTool()

PLUGIN = factory
''',
        encoding="utf-8",
    )
    names = discover_dropin_tools(reg, root=root)
    assert names == ["weather"]


# ---------------------------------------------------------------------------
# Connectors surface
# ---------------------------------------------------------------------------


def test_dropin_connectors_registers_flat_file(tmp_path: Path) -> None:
    root = _connectors_root(tmp_path)
    _write(
        root / "discord.py",
        _CONNECTOR_SRC.format(cls="DiscordConnector", name="discord"),
    )
    reg = ConnectorRegistry()
    names = discover_dropin_connectors(reg, root=root)
    assert names == ["discord"]
    assert "discord" in reg.names()


def test_dropin_connectors_already_instantiated(tmp_path: Path) -> None:
    root = _connectors_root(tmp_path)
    src = _CONNECTOR_SRC.format(cls="SlackConnector", name="slack")
    # Override PLUGIN to be an instance instead of the class.
    src += "\nPLUGIN = SlackConnector()\n"
    _write(root / "slack.py", src)
    reg = ConnectorRegistry()
    names = discover_dropin_connectors(reg, root=root)
    assert names == ["slack"]


# ---------------------------------------------------------------------------
# Backends surface
# ---------------------------------------------------------------------------


def test_dropin_backends_returns_manifest_instance_pairs(
    tmp_path: Path,
) -> None:
    root = _backends_root(tmp_path)
    _write(
        root / "vllm_cuda.py",
        _BACKEND_SRC.format(cls="VLLMBackend", name="vllm-cuda"),
    )
    pairs = discover_dropin_backends(root=root)
    assert len(pairs) == 1
    manifest, instance = pairs[0]
    assert manifest.name == "vllm-cuda"
    assert instance.name == "vllm-cuda"


def test_dropin_backends_missing_root_is_noop(tmp_path: Path) -> None:
    assert discover_dropin_backends(
        root=tmp_path / "no-such-dir",
    ) == []
