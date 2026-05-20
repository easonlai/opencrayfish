"""tests/test_provider_manifest.py — Coverage for the lightest of the
four plug-in surfaces: Provider backend manifest + discovery.

Mirrors ``tests/test_connector_registry.py`` shape (no real packages;
monkeypatch ``entry_points`` for hermeticity). The Provider backend
stack has no central registry by design (see
``core/provider_manifest.py`` module docstring) so this test focuses on:

  * BackendManifest static validation.
  * resolve_backend_manifest back-compat synthesis from class
    attributes + docstrings.
  * discover_external_backends entry-points walk, including
    isolation of broken / invalid backends.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.provider_manifest import (
    BACKEND_ENTRY_POINT_GROUP,
    BackendManifest,
    discover_external_backends,
    resolve_backend_manifest,
)

# ---------------------------------------------------------------------------
# Fake backend classes used as test doubles
# ---------------------------------------------------------------------------


class _FakeVllmBackend:
    """Class-style entry-point: discovery instantiates with zero args."""

    manifest = BackendManifest(
        name="fake-vllm",
        description="Fake vLLM backend for tests.",
        requires_caps=("network.outbound", "gpu"),
        config_key="fake_vllm",
    )
    name = "fake-vllm"

    async def generate(self, *_: Any, **__: Any) -> str:
        return "ok"


def _fake_llamacpp_factory() -> Any:
    """Factory-callable entry-point: discovery calls with zero args."""

    class _Llama:
        manifest = BackendManifest(
            name="fake-llamacpp",
            description="Fake llama.cpp wrapper.",
            requires_caps=("subprocess", "filesystem.read"),
        )
        name = "fake-llamacpp"

        async def generate(self, *_: Any, **__: Any) -> str:
            return "ok"

    return _Llama()


class _BrokenBackend:
    """A class whose __init__ raises — discovery must isolate."""

    def __init__(self) -> None:
        raise RuntimeError("simulated package init failure")


class _NotABackend:
    """Loaded object that is neither a backend nor a class nor a
    factory returning one — discovery must skip with a warning."""


class _FakeEntryPoint:
    """Mimics importlib.metadata.EntryPoint enough for discovery."""

    def __init__(self, name: str, value: str, target: Any) -> None:
        self.name = name
        self.value = value
        self.group = BACKEND_ENTRY_POINT_GROUP
        self._target = target

    def load(self) -> Any:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def _patch_entry_points(
    monkeypatch: pytest.MonkeyPatch, eps: list[Any],
) -> None:
    def fake_entry_points(group: str = "") -> list[Any]:
        assert group == BACKEND_ENTRY_POINT_GROUP
        return list(eps)

    monkeypatch.setattr(
        "core.provider_manifest.entry_points", fake_entry_points,
    )


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


def test_manifest_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        BackendManifest(name="", description="x")


def test_manifest_rejects_whitespace_name() -> None:
    with pytest.raises(ValueError, match="whitespace"):
        BackendManifest(name="bad name", description="x")


def test_manifest_rejects_empty_description() -> None:
    with pytest.raises(ValueError, match="description"):
        BackendManifest(name="x", description="")


def test_manifest_rejects_unsupported_protocol() -> None:
    with pytest.raises(ValueError, match="compat_version"):
        BackendManifest(
            name="x",
            description="x",
            compat_version="backend-protocol/99",
        )


def test_manifest_rejects_empty_config_key() -> None:
    with pytest.raises(ValueError, match="config_key"):
        BackendManifest(name="x", description="x", config_key="")


def test_manifest_accepts_minimum_required() -> None:
    m = BackendManifest(name="ok", description="ok")
    assert m.compat_version == "backend-protocol/1"
    assert m.requires_caps == ()
    assert m.config_key is None


# ---------------------------------------------------------------------------
# Back-compat synthesis
# ---------------------------------------------------------------------------


def test_resolve_uses_declared_manifest() -> None:
    m = resolve_backend_manifest(_FakeVllmBackend())
    assert m.name == "fake-vllm"
    assert "gpu" in m.requires_caps
    assert m.config_key == "fake_vllm"


def test_resolve_synthesizes_from_name_attr() -> None:
    class _Bare:
        """A bare backend with only name."""
        name = "bare-backend"

    m = resolve_backend_manifest(_Bare())
    assert m.name == "bare-backend"
    assert m.description  # synthesized from docstring or fallback


def test_resolve_handles_dict_manifest() -> None:
    class _Dictish:
        manifest = {"name": "dictish", "description": "via dict"}
        name = "dictish"

    m = resolve_backend_manifest(_Dictish())
    assert m.name == "dictish"


def test_resolve_raises_without_name() -> None:
    class _NoName:
        pass

    with pytest.raises(ValueError, match="name"):
        resolve_backend_manifest(_NoName())


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovers_class_style_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("fake-vllm", "x:Y", _FakeVllmBackend),
    ])
    pairs = discover_external_backends()
    assert len(pairs) == 1
    manifest, instance = pairs[0]
    assert manifest.name == "fake-vllm"
    assert instance.name == "fake-vllm"


def test_discovers_factory_callable_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "fake-llamacpp", "x:factory", _fake_llamacpp_factory,
        ),
    ])
    pairs = discover_external_backends()
    assert len(pairs) == 1
    assert pairs[0][0].name == "fake-llamacpp"


def test_isolates_broken_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A backend whose __init__ raises must not block the rest."""
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("fake-vllm", "x:Y", _FakeVllmBackend),
        _FakeEntryPoint("broken", "x:Broken", _BrokenBackend),
    ])
    with caplog.at_level("WARNING"):
        pairs = discover_external_backends()
    names = [m.name for m, _ in pairs]
    assert names == ["fake-vllm"]
    assert any("broken" in rec.getMessage().lower() for rec in caplog.records)


def test_isolates_load_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An ImportError from ep.load() must be logged and skipped."""
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint(
            "import_fails", "x:DoesNotExist",
            ImportError("no module x"),
        ),
        _FakeEntryPoint("fake-vllm", "x:Y", _FakeVllmBackend),
    ])
    with caplog.at_level("WARNING"):
        pairs = discover_external_backends()
    assert [m.name for m, _ in pairs] == ["fake-vllm"]


def test_skips_invalid_loaded_object(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An entry-point pointing at a non-backend, non-class,
    non-factory object must be skipped with a warning."""
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("bogus", "x:Y", _NotABackend()),
    ])
    with caplog.at_level("WARNING"):
        pairs = discover_external_backends()
    assert pairs == []


def test_empty_group_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    assert discover_external_backends() == []


def test_filter_fn_can_exclude_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """filter_fn lets the caller restrict which entry-points get
    loaded (e.g. allow-list from cfg)."""
    _patch_entry_points(monkeypatch, [
        _FakeEntryPoint("fake-vllm", "x:Y", _FakeVllmBackend),
        _FakeEntryPoint(
            "fake-llamacpp", "x:factory", _fake_llamacpp_factory,
        ),
    ])
    pairs = discover_external_backends(
        filter_fn=lambda ep: ep.name == "fake-vllm",
    )
    assert [m.name for m, _ in pairs] == ["fake-vllm"]
