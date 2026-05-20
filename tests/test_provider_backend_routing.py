"""Tests for SMELL C: third-party Provider backend routing.

When the operator sets ``cfg.hardware.primary_backend`` or
``cfg.hardware.fallback_backend`` to the manifest name of a discovered
backend (``opencrayfish.provider_backends`` entry-point), that backend
takes the matching Provider slot in place of the built-in
``HailoOllamaBackend`` / ``OllamaBackend``. Unknown names fail loud.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.provider import Provider
from core.provider_manifest import BackendManifest


@dataclass(frozen=True)
class _FakeHardwareCfg:
    npu_acceleration: bool = False
    hailo_ollama_url: str = "http://localhost:8000"
    hailo_model: str = "qwen2.5-instruct:1.5b"
    cpu_fallback_url: str = "http://localhost:11434"
    cpu_fallback_model: str = "qwen2:1.5b"
    thermal_limit_celsius: float = 80.0
    ram_limit_pct: float = 85.0
    thermal_release_celsius: float = 0.0
    ram_release_pct: float = 0.0
    vitals_cache_ttl_seconds: float = 5.0
    primary_backend: str | None = None
    fallback_backend: str | None = None


class _FakeBackend:
    """Minimal stub matching the `_Backend` Protocol."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    manifest = None  # type: ignore[assignment]

    async def generate(self, system_prompt: str, messages: list) -> str:
        return f"{self.name}:ok"

    async def aclose(self) -> None:
        self.closed = True


def _mk_manifest(name: str) -> BackendManifest:
    return BackendManifest(
        name=name,
        description=f"fake backend {name}",
        compat_version="backend-protocol/1",
    )


def test_no_overrides_uses_built_in_cpu_only():
    cfg = _FakeHardwareCfg()
    prov = Provider.from_config(cfg)
    assert prov.active_backend == "ollama-cpu"


def test_no_overrides_uses_built_in_npu_when_acceleration_on():
    cfg = _FakeHardwareCfg(npu_acceleration=True)
    prov = Provider.from_config(cfg)
    assert prov.active_backend == "hailo-ollama-npu"


def test_primary_override_routes_to_discovered_backend():
    fake = _FakeBackend("vllm-cuda")
    cfg = _FakeHardwareCfg(primary_backend="vllm-cuda")
    prov = Provider.from_config(
        cfg, discovered_backends=[(_mk_manifest("vllm-cuda"), fake)]
    )
    assert prov.active_backend == "vllm-cuda"


def test_fallback_override_routes_to_discovered_backend():
    fake = _FakeBackend("cpu-llama")
    cfg = _FakeHardwareCfg(
        npu_acceleration=True,
        fallback_backend="cpu-llama",
    )
    prov = Provider.from_config(
        cfg, discovered_backends=[(_mk_manifest("cpu-llama"), fake)]
    )
    # Primary is still the built-in Hailo backend
    assert prov.active_backend == "hailo-ollama-npu"
    # But _fallback was replaced — verified via private attr for the test
    assert prov._fallback is fake  # type: ignore[attr-defined]


def test_both_overrides_route_to_discovered_backends():
    primary = _FakeBackend("vllm-cuda")
    fallback = _FakeBackend("cpu-llama")
    cfg = _FakeHardwareCfg(
        primary_backend="vllm-cuda",
        fallback_backend="cpu-llama",
    )
    prov = Provider.from_config(
        cfg,
        discovered_backends=[
            (_mk_manifest("vllm-cuda"), primary),
            (_mk_manifest("cpu-llama"), fallback),
        ],
    )
    assert prov.active_backend == "vllm-cuda"
    assert prov._fallback is fallback  # type: ignore[attr-defined]


def test_unknown_primary_override_raises_value_error():
    cfg = _FakeHardwareCfg(primary_backend="nonexistent")
    with pytest.raises(ValueError, match="primary_backend=.*nonexistent"):
        Provider.from_config(cfg, discovered_backends=[])


def test_unknown_fallback_override_raises_value_error():
    cfg = _FakeHardwareCfg(fallback_backend="nonexistent")
    with pytest.raises(ValueError, match="fallback_backend=.*nonexistent"):
        Provider.from_config(cfg, discovered_backends=[])


def test_override_unknown_lists_available_names_in_error():
    fake = _FakeBackend("vllm-cuda")
    cfg = _FakeHardwareCfg(primary_backend="typo")
    with pytest.raises(ValueError, match="vllm-cuda"):
        Provider.from_config(
            cfg, discovered_backends=[(_mk_manifest("vllm-cuda"), fake)]
        )


def test_empty_discovery_with_no_overrides_keeps_built_in_wiring():
    cfg = _FakeHardwareCfg()
    prov = Provider.from_config(cfg, discovered_backends=[])
    assert prov.active_backend == "ollama-cpu"
