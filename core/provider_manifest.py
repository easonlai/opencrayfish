"""core.provider_manifest \u2014 Plug-in surface for inference backends.

This module is the *lightest* of the four manifest stacks
(``core.skills.manifest``, ``tools.manifest``, ``connectors.manifest``,
and now this one for Provider backends). It is light by design:

  * The Provider singleton (``core/provider.py::Provider``) has a
    deliberately hard-coded primary/fallback structure (NPU first,
    CPU second, circuit-breaker between). That structure is right
    for the Pi 5 + AI HAT+ deployment target and we don't want to
    refactor it speculatively.
  * What third parties NEED today is the ability to *ship a new
    backend* (vLLM, llama.cpp, OpenAI, Bedrock, \u2026) as a
    pip-installable package and have it auto-discovered. The
    composition of which backend is primary vs. fallback can stay
    in operator config or wait for a future refactor.

So this module gives us:

  * ``BackendManifest`` \u2014 the declarative descriptor every backend
    plug-in advertises (name, description, compat_version,
    requires_caps, config_key, extras).
  * ``BACKEND_ENTRY_POINT_GROUP`` \u2014 the well-known entry-point group
    third-party packages register under.
  * ``discover_external_backends()`` \u2014 walks installed packages and
    returns a list of ``(manifest, instance)`` pairs the operator
    can inspect. Today main.py only logs the result; future work
    will let cfg.hardware route a named external backend into the
    primary/fallback slots.
  * ``resolve_backend_manifest()`` \u2014 back-compat synthesizer so the
    two existing in-tree backends (``HailoOllamaBackend``,
    ``OllamaBackend``) get a manifest synthesized from their
    ``name`` attribute without needing a manual annotation.

Compared to Tool/Skill/Connector this stack has:
  * No central ``BackendRegistry`` \u2014 Provider already IS the live
    backend owner. Re-introducing a parallel registry would force a
    refactor we don't need yet.
  * No bootstrap_validate (no aggregate to validate \u2014 the manifest's
    own ``__post_init__`` is enough today).

WHEN A FUTURE REFACTOR LANDS, this module's surface remains stable:
adding a registry just means writing a new module that consumes
these manifests + the entry-points group.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Backend Protocol revisions the running core knows how to talk to.
SUPPORTED_BACKEND_PROTOCOL_VERSIONS: frozenset[str] = frozenset({
    "backend-protocol/1",
})

DEFAULT_BACKEND_PROTOCOL_VERSION: str = "backend-protocol/1"

# Entry-point group third-party packages register Provider backends
# under. Renaming this is a Backend Protocol break.
BACKEND_ENTRY_POINT_GROUP: str = "opencrayfish.provider_backends"


# Reserved capability tokens for backends. Most backends are network
# clients, but a local llama.cpp wrapper might use subprocess +
# filesystem.read instead.
WELL_KNOWN_BACKEND_CAPABILITIES: frozenset[str] = frozenset({
    "network.outbound",  # backend makes outbound HTTP / TCP calls.
    "subprocess",        # backend spawns a subprocess (e.g. llama.cpp).
    "filesystem.read",   # backend reads model files from disk.
    "gpu",               # backend uses a GPU.
    "npu",               # backend uses a Pi AI HAT+ / Hailo / similar NPU.
})


@dataclass(frozen=True, slots=True)
class BackendManifest:
    """Declarative metadata for a Provider backend plug-in."""

    name: str
    """Short identifier (``hailo-ollama-npu``, ``ollama-cpu``,
    ``vllm-cuda``, \u2026). Must match the backend instance's ``name``
    attribute."""

    description: str
    """One-line human-readable purpose. Surfaced in startup logs
    when third-party backends are discovered."""

    compat_version: str = DEFAULT_BACKEND_PROTOCOL_VERSION
    """Backend Protocol revision this backend was written against.
    Must be in ``SUPPORTED_BACKEND_PROTOCOL_VERSIONS``."""

    requires_caps: tuple[str, ...] = ()
    """Capability tokens (``WELL_KNOWN_BACKEND_CAPABILITIES``).
    Informational today."""

    config_key: str | None = None
    """Optional ``cfg.plugins.<key>`` namespace the backend reads
    its model / endpoint / credentials from. Operator-facing."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Free-form extension slot. Same convention as the other
    manifest types."""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(
                f"BackendManifest.name must be a non-empty string, "
                f"got {self.name!r}"
            )
        if any(ch.isspace() for ch in self.name):
            raise ValueError(
                f"BackendManifest.name must not contain whitespace: "
                f"{self.name!r}"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(
                f"BackendManifest.description must be a non-empty "
                f"string for backend {self.name!r}"
            )
        if self.compat_version not in SUPPORTED_BACKEND_PROTOCOL_VERSIONS:
            raise ValueError(
                f"BackendManifest({self.name!r}): compat_version "
                f"{self.compat_version!r} is not supported. "
                f"Supported: "
                f"{sorted(SUPPORTED_BACKEND_PROTOCOL_VERSIONS)}."
            )
        if self.config_key is not None and (
            not isinstance(self.config_key, str)
            or not self.config_key.strip()
        ):
            raise ValueError(
                f"BackendManifest({self.name!r}): config_key must be "
                f"a non-empty string or None, got {self.config_key!r}"
            )


# ---------------------------------------------------------------------------
# Back-compat synthesizer (mirror of resolve_tool_manifest)
# ---------------------------------------------------------------------------


def _to_str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def resolve_backend_manifest(backend: Any) -> BackendManifest:
    """Return the backend's declared manifest, or synthesize one
    from its ``name`` + class docstring."""
    declared = getattr(backend, "manifest", None)
    if isinstance(declared, BackendManifest):
        return declared
    if isinstance(declared, dict):
        return BackendManifest(**declared)

    name = getattr(backend, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"Cannot resolve manifest for {backend!r}: missing `name`."
        )
    description = getattr(backend, "description", None)
    if not isinstance(description, str) or not description.strip():
        doc = (type(backend).__doc__ or "").strip()
        description = next(
            (line.strip() for line in doc.splitlines() if line.strip()),
            f"{name} backend",
        )
    return BackendManifest(
        name=name,
        description=description,
        compat_version=DEFAULT_BACKEND_PROTOCOL_VERSION,
        requires_caps=_to_str_tuple(getattr(backend, "requires_caps", ())),
        config_key=getattr(backend, "config_key", None),
        extras=dict(getattr(backend, "extras", {}) or {}),
    )


# ---------------------------------------------------------------------------
# Entry-points discovery
# ---------------------------------------------------------------------------


def _instantiate(loaded: Any, ep_name: str) -> Any | None:
    """Turn whatever ``ep.load()`` returned into a backend instance.

    Same three-shape contract as the other discovery modules:
      * Backend instance \u2014 returned as-is.
      * Class \u2014 instantiated with zero args.
      * Callable \u2014 called with zero args; result must have
        ``name`` + ``generate``.
    """
    if isinstance(loaded, type):
        try:
            return loaded()
        except Exception:
            log.exception(
                "BACKEND discovery ep=%s class instantiation failed",
                ep_name,
            )
            return None
    if hasattr(loaded, "name") and hasattr(loaded, "generate"):
        return loaded
    if callable(loaded):
        try:
            result = loaded()
        except Exception:
            log.exception(
                "BACKEND discovery ep=%s factory call failed", ep_name,
            )
            return None
        if hasattr(result, "name") and hasattr(result, "generate"):
            return result
        log.warning(
            "BACKEND discovery ep=%s factory returned %s, not a "
            "backend (must have name + generate)",
            ep_name, type(result).__name__,
        )
        return None
    log.warning(
        "BACKEND discovery ep=%s loaded object %s is not a backend "
        "/ class / callable \u2014 skipping",
        ep_name, type(loaded).__name__,
    )
    return None


def discover_external_backends(
    *,
    group: str = BACKEND_ENTRY_POINT_GROUP,
    filter_fn: Callable[[EntryPoint], bool] | None = None,
) -> list[tuple[BackendManifest, Any]]:
    """Walk installed packages for backend plug-ins.

    Returns a list of ``(manifest, instance)`` pairs. Failed
    entry-points are logged and skipped \u2014 isolation contract same
    as the Tool/Skill/Connector discovery loops.

    Today the Provider singleton ignores this list; main.py only
    logs what was found. A future refactor will let
    cfg.hardware.primary = "vllm-cuda" route a discovered backend
    into the primary slot.
    """
    discovered: list[tuple[BackendManifest, Any]] = []

    try:
        eps = entry_points(group=group)
    except Exception:
        log.exception(
            "BACKEND discovery: entry_points(group=%r) lookup failed",
            group,
        )
        return discovered

    for ep in eps:
        if filter_fn is not None and not filter_fn(ep):
            log.info(
                "BACKEND discovery ep=%s skipped by filter", ep.name,
            )
            continue
        try:
            loaded = ep.load()
        except Exception:
            log.exception(
                "BACKEND discovery ep=%s (%s) load failed \u2014 skipping",
                ep.name, ep.value,
            )
            continue
        backend = _instantiate(loaded, ep.name)
        if backend is None:
            continue
        try:
            manifest = resolve_backend_manifest(backend)
        except Exception:
            log.exception(
                "BACKEND discovery ep=%s manifest resolution failed "
                "— skipping",
                ep.name,
            )
            continue
        discovered.append((manifest, backend))

    return discovered


def discover_dropin_backends(
    *,
    root: Path | None = None,
) -> list[tuple[BackendManifest, Any]]:
    """Walk the drop-in folder for backends and return them.

    No-pip-install path mirroring ``discover_external_backends``:
    each ``.py`` file or sub-package under ``plugins/backends/`` is
    loaded; its module-level ``PLUGIN`` / ``PLUGINS`` attribute is
    fed through the same ``_instantiate`` + ``resolve_backend_manifest``
    pipeline as the entry-points path. See ``core.dropin`` for the
    folder layout + module contract.

    Returns the same ``(manifest, instance)`` list shape as
    ``discover_external_backends`` so ``main.py`` can concatenate
    the two lists and pass the union to ``Provider.from_config``.

    Args:
        root: Override the drop-in folder root. ``None`` resolves to
            ``OPENCRAYFISH_PLUGINS_DIR`` env-var or
            ``<cwd>/plugins/backends/``. Override only in tests.
    """
    from core.dropin import iter_dropin_plugins, surface_root

    discovered: list[tuple[BackendManifest, Any]] = []
    target = root if root is not None else surface_root("backends")

    for source_label, raw in iter_dropin_plugins("backends", root=target):
        backend = _instantiate(raw, source_label)
        if backend is None:
            continue
        try:
            manifest = resolve_backend_manifest(backend)
        except Exception:
            log.exception(
                "BACKEND dropin %s manifest resolution failed — "
                "skipping",
                source_label,
            )
            continue
        discovered.append((manifest, backend))

    if discovered:
        log.info(
            "BACKEND dropin root=%s discovered %d backend(s): %s",
            target, len(discovered),
            ", ".join(m.name for m, _ in discovered),
        )
    return discovered
