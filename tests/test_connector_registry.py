"""tests/test_connector_registry.py \u2014 Coverage for the ConnectorRegistry +
manifest + discovery stack.

Mirrors ``tests/test_skill_discovery.py`` shape so the two layers
stay symmetric. We don't install real packages \u2014 instead we
monkeypatch ``importlib.metadata.entry_points`` to return a
fabricated list, so the test is fast and hermetic.

What's covered:
  * ConnectorManifest static validation (name, description, version,
    config_key).
  * resolve_connector_manifest back-compat synthesis from class
    docstrings + class-name fallback.
  * ConnectorRegistry duplicate-name rejection, manifest accessors,
    inventory rendering, aclose_all best-effort.
  * Entry-points discovery: class style, factory style, isolation of
    broken entry-points, isolation of load failures, duplicate-name
    drop-without-bubble, empty-group no-op.
  * bootstrap_validate detects missing config_key namespace.
"""
from __future__ import annotations

from typing import Any

import pytest

from connectors import (
    CONNECTOR_ENTRY_POINT_GROUP,
    ConnectorManifest,
    ConnectorRegistry,
    discover_external_connectors,
    resolve_connector_manifest,
)

# ---------------------------------------------------------------------------
# Fake connector classes used as test doubles
# ---------------------------------------------------------------------------


class _FakeDiscordConnector:
    """Class-style entry-point: discovery should call __init__()."""
    manifest = ConnectorManifest(
        name="fake_discord",
        description="Fake Discord gateway for tests.",
        requires_caps=("network.outbound",),
    )
    name = "fake_discord"

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _fake_mqtt_factory() -> Any:
    """Factory-callable entry-point: discovery should call it with no args."""

    class _Mqtt:
        manifest = ConnectorManifest(
            name="fake_mqtt",
            description="Fake MQTT subscriber for tests.",
            requires_caps=("network.inbound",),
        )
        name = "fake_mqtt"

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    return _Mqtt()


class _BrokenConnector:
    """A class whose __init__ raises \u2014 discovery must isolate this."""

    def __init__(self) -> None:
        raise RuntimeError("simulated package init failure")


class _ShadowingConnector:
    """A discovered Connector whose name collides with a first-party
    one. The registry must reject this on duplicate-name; discovery
    must log + skip (NOT bubble) so the boot continues."""

    manifest = ConnectorManifest(
        name="fake_discord",  # collides with _FakeDiscordConnector
        description="Shadowing connector.",
    )
    name = "fake_discord"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _FakeEntryPoint:
    """Mimics importlib.metadata.EntryPoint enough for discovery."""

    def __init__(self, name: str, value: str, target: Any) -> None:
        self.name = name
        self.value = value
        self.group = CONNECTOR_ENTRY_POINT_GROUP
        self._target = target

    def load(self) -> Any:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def _patch_entry_points(
    monkeypatch: pytest.MonkeyPatch, eps: list[Any],
) -> None:
    def fake_entry_points(group: str = "") -> list[Any]:
        assert group == CONNECTOR_ENTRY_POINT_GROUP
        return list(eps)

    monkeypatch.setattr(
        "connectors.discovery.entry_points", fake_entry_points,
    )


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


def test_manifest_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        ConnectorManifest(name="", description="x")


def test_manifest_rejects_whitespace_name() -> None:
    with pytest.raises(ValueError, match="whitespace"):
        ConnectorManifest(name="bad name", description="x")


def test_manifest_rejects_empty_description() -> None:
    with pytest.raises(ValueError, match="description"):
        ConnectorManifest(name="x", description="")


def test_manifest_rejects_unsupported_protocol() -> None:
    with pytest.raises(ValueError, match="compat_version"):
        ConnectorManifest(
            name="x",
            description="x",
            compat_version="connector-protocol/99",
        )


def test_manifest_rejects_empty_config_key() -> None:
    with pytest.raises(ValueError, match="config_key"):
        ConnectorManifest(name="x", description="x", config_key="")


# ---------------------------------------------------------------------------
# Back-compat synthesis
# ---------------------------------------------------------------------------


def test_resolve_uses_declared_manifest() -> None:
    m = resolve_connector_manifest(_FakeDiscordConnector())
    assert m.name == "fake_discord"
    assert m.requires_caps == ("network.outbound",)


def test_resolve_synthesizes_from_class_name() -> None:
    class TelegramConnector:
        """Telegram Bot API connector."""
    m = resolve_connector_manifest(TelegramConnector())
    assert m.name == "telegram"
    assert "Telegram" in m.description


def test_resolve_handles_dict_manifest() -> None:
    class Dictish:
        manifest = {"name": "dictish", "description": "via dict"}
    m = resolve_connector_manifest(Dictish())
    assert m.name == "dictish"


# ---------------------------------------------------------------------------
# Registry behaviour
# ---------------------------------------------------------------------------


def test_registry_rejects_duplicate_name() -> None:
    reg = ConnectorRegistry()
    reg.register(_FakeDiscordConnector())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_FakeDiscordConnector())


def test_registry_change_listener_fires() -> None:
    reg = ConnectorRegistry()
    calls: list[None] = []
    reg.set_change_listener(lambda: calls.append(None))
    reg.register(_FakeDiscordConnector())
    reg.unregister("fake_discord")
    assert len(calls) == 2


def test_registry_inventory_lines() -> None:
    reg = ConnectorRegistry()
    reg.register(_FakeDiscordConnector())
    lines = reg.inventory_lines()
    assert any("fake_discord" in line for line in lines)
    assert any("network.outbound" in line for line in lines)


async def test_aclose_all_calls_stop() -> None:
    reg = ConnectorRegistry()
    conn = _FakeDiscordConnector()
    reg.register(conn)
    await reg.aclose_all()
    assert conn.stopped is True


async def test_aclose_all_isolates_failures(caplog: pytest.LogCaptureFixture) -> None:
    class _BadStop:
        manifest = ConnectorManifest(name="bad", description="bad")
        name = "bad"

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            raise RuntimeError("boom")

    reg = ConnectorRegistry()
    reg.register(_BadStop())
    reg.register(_FakeDiscordConnector())
    with caplog.at_level("ERROR"):
        await reg.aclose_all()  # must NOT raise
    # The good connector still got stopped.
    assert reg.get("fake_discord").stopped is True
    assert any("bad" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# bootstrap_validate
# ---------------------------------------------------------------------------


def test_bootstrap_validate_flags_missing_config_key() -> None:
    reg = ConnectorRegistry()

    class _Needs:
        manifest = ConnectorManifest(
            name="needs_cfg", description="x", config_key="needs_cfg",
        )
        name = "needs_cfg"

    reg.register(_Needs())
    problems = reg.bootstrap_validate(
        plugins_config={"other": {}}, strict=False,
    )
    assert problems
    assert "needs_cfg" in problems[0]


def test_bootstrap_validate_passes_when_config_present() -> None:
    reg = ConnectorRegistry()

    class _Needs:
        manifest = ConnectorManifest(
            name="needs_cfg", description="x", config_key="needs_cfg",
        )
        name = "needs_cfg"

    reg.register(_Needs())
    problems = reg.bootstrap_validate(
        plugins_config={"needs_cfg": {"foo": 1}}, strict=False,
    )
    assert problems == []


def test_bootstrap_validate_strict_raises() -> None:
    reg = ConnectorRegistry()

    class _Needs:
        manifest = ConnectorManifest(
            name="needs_cfg", description="x", config_key="needs_cfg",
        )
        name = "needs_cfg"

    reg.register(_Needs())
    with pytest.raises(RuntimeError, match="bootstrap validation"):
        reg.bootstrap_validate(plugins_config={}, strict=True)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovers_class_style_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "fake_discord", "x:Y", _FakeDiscordConnector,
        ),
    ])
    reg = ConnectorRegistry()
    names = discover_external_connectors(reg)
    assert names == ["fake_discord"]
    assert reg.has("fake_discord")


def test_discovers_factory_callable_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "fake_mqtt", "x:factory", _fake_mqtt_factory,
        ),
    ])
    reg = ConnectorRegistry()
    names = discover_external_connectors(reg)
    assert names == ["fake_mqtt"]


def test_isolates_broken_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("fake_discord", "x:Y", _FakeDiscordConnector),
        _FakeEntryPoint("broken", "x:Broken", _BrokenConnector),
    ])
    reg = ConnectorRegistry()
    with caplog.at_level("WARNING"):
        names = discover_external_connectors(reg)
    assert names == ["fake_discord"]
    assert any("broken" in rec.getMessage() for rec in caplog.records)


def test_isolates_load_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "import_fails", "x:DoesNotExist",
            ImportError("no module x"),
        ),
        _FakeEntryPoint("fake_discord", "x:Y", _FakeDiscordConnector),
    ])
    reg = ConnectorRegistry()
    with caplog.at_level("WARNING"):
        names = discover_external_connectors(reg)
    assert names == ["fake_discord"]


def test_rejects_duplicate_name_without_bubbling(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reg = ConnectorRegistry()
    reg.register(_FakeDiscordConnector())
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("shadow", "x:Y", _ShadowingConnector),
    ])
    with caplog.at_level("WARNING"):
        names = discover_external_connectors(reg)
    assert names == []
    assert reg.has("fake_discord")


def test_empty_group_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    reg = ConnectorRegistry()
    assert discover_external_connectors(reg) == []
    assert reg.names() == []


# ---------------------------------------------------------------------------
# First-party connectors expose explicit manifests
# ---------------------------------------------------------------------------


def test_in_tree_telegram_has_manifest() -> None:
    from connectors.telegram import TelegramConnector

    m = TelegramConnector.manifest  # class attribute
    assert isinstance(m, ConnectorManifest)
    assert m.name == "telegram"
    assert "network.outbound" in m.requires_caps


def test_in_tree_web_chat_has_manifest() -> None:
    from connectors.web_chat import WebChatConnector

    m = WebChatConnector.manifest
    assert isinstance(m, ConnectorManifest)
    assert m.name == "web_chat"
    assert "network.inbound" in m.requires_caps


# ---------------------------------------------------------------------------
# Lifecycle: start_all + external_lifecycle skip
# ---------------------------------------------------------------------------


async def test_start_all_calls_start() -> None:
    reg = ConnectorRegistry()
    conn = _FakeDiscordConnector()
    reg.register(conn)
    started = await reg.start_all()
    assert started == ["fake_discord"]
    assert conn.started is True


async def test_start_all_skips_external_lifecycle() -> None:
    """Connectors registered with ``external_lifecycle=True`` are
    skipped by both start_all and aclose_all so main.py can drive
    them explicitly without double-start/double-stop."""
    reg = ConnectorRegistry()
    external = _FakeDiscordConnector()
    reg.register(external, external_lifecycle=True)

    class _ManagedConn:
        manifest = ConnectorManifest(name="managed", description="x")
        name = "managed"

        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    managed = _ManagedConn()
    reg.register(managed)

    started = await reg.start_all()
    assert started == ["managed"]
    assert external.started is False
    assert managed.started is True

    await reg.aclose_all()
    assert external.stopped is False
    assert managed.stopped is True


async def test_start_all_skips_connectors_without_start() -> None:
    """A connector that defines no start() (e.g. a passive polling
    one) is silently skipped, not crashed against."""

    class _NoStart:
        manifest = ConnectorManifest(name="passive", description="x")
        name = "passive"

    reg = ConnectorRegistry()
    reg.register(_NoStart())
    started = await reg.start_all()
    assert started == []  # no crash, no entry


async def test_start_all_isolates_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing start() on one connector must not block the rest."""

    class _BadStart:
        manifest = ConnectorManifest(name="bad", description="x")
        name = "bad"

        async def start(self) -> None:
            raise RuntimeError("boom")

        async def stop(self) -> None:
            return None

    reg = ConnectorRegistry()
    reg.register(_BadStart())
    good = _FakeDiscordConnector()
    reg.register(good)
    with caplog.at_level("ERROR"):
        started = await reg.start_all()
    assert started == ["fake_discord"]
    assert good.started is True
    assert any("bad" in rec.message for rec in caplog.records)
