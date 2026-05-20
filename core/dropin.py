"""core.dropin — Drop-in folder loader (hybrid plug-in source).

THE PROMISE
-----------
A third-party developer should be able to extend OpenCrayFish in
EITHER of two ways:

  1. ``pip install opencrayfish-skill-x`` — the canonical entry-points
     path, handled by ``core.skills.discovery`` /
     ``tools.discovery`` / ``connectors.discovery`` /
     ``core.provider_manifest``. Best for shareable / versioned /
     dependency-managed plug-ins.

  2. **Drop a ``.py`` file (or a package folder) into
     ``plugins/<surface>/``** at the workspace root. No
     ``pyproject.toml``, no entry-points, no ``pip install`` — just
     copy the file in, restart, done. Best for local experiments,
     private one-offs, or air-gapped deployments where pip-installing
     packages is inconvenient.

Both paths feed the SAME registries with the SAME manifest
validation and the SAME fail-isolation contract. From the agent's
point of view, a drop-in plug-in is indistinguishable from a
pip-installed one once it's registered.

FOLDER LAYOUT
-------------
::

    <project root>/
        plugins/
            skills/
                weather.py          # flat: one Skill per file
                translate/          # nested: a sub-package
                    __init__.py
                    skill.py
            tools/
                home_assistant.py
            connectors/
                discord.py
            backends/
                vllm_cuda.py

The default root is ``<cwd>/plugins/``. Override the entire root with
the ``OPENCRAYFISH_PLUGINS_DIR`` env var if you want to keep plug-ins
outside the repo (e.g. ``/etc/opencrayfish/plugins``).

MODULE CONTRACT
---------------
Every drop-in module must expose ONE of these two module-level names
(checked in this order):

  * ``PLUGIN`` — a single Skill/Tool/Connector/Backend class,
    factory callable, or already-instantiated object. The discovery
    layer's existing ``_instantiate()`` helper accepts all three
    shapes — same as entry-points.
  * ``PLUGINS`` — an iterable of the same. Use this when one file
    exports multiple plug-ins of the same surface.

A module that exposes neither is logged at INFO and skipped. This is
intentional: drop-in folders can contain shared helper modules that
other drop-ins import, and we don't want to spam WARNINGs for them.

ISOLATION
---------
Every file is loaded inside its own ``try``/``except``. A broken
drop-in only loses itself; the boot continues. The exception is
logged at ERROR with the file path so the operator can fix or
delete it.

SECURITY NOTE
-------------
Drop-in folder loading executes arbitrary Python from any file under
the configured root. This is no more dangerous than the entry-points
path (both run third-party code on import), but operators should
treat the drop-in root with the same trust posture as a venv:
**only put code you wrote or audited in there**. The agent does NOT
sandbox drop-in modules.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)


# Env var lets operators relocate the entire drop-in root without
# touching config.yaml. Useful for system-wide installs
# (e.g. /etc/opencrayfish/plugins) or per-deployment overrides.
PLUGINS_ROOT_ENV: str = "OPENCRAYFISH_PLUGINS_DIR"

# Folder names under the drop-in root — one per plug-in surface.
# Mirrors the ``opencrayfish.*`` entry-point group naming so an
# operator who knows one knows the other.
SURFACE_FOLDERS: dict[str, str] = {
    "skills": "skills",
    "tools": "tools",
    "connectors": "connectors",
    "backends": "backends",
}


def dropin_root() -> Path:
    """Resolve the active drop-in root.

    Returns the env-var override if set, else ``<cwd>/plugins/``.
    The path is returned even if it doesn't exist — callers are
    responsible for the ``is_dir()`` check (and silently skipping is
    the correct behaviour for an unused surface).
    """
    override = os.environ.get(PLUGINS_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "plugins"


def surface_root(surface: str) -> Path:
    """Resolve the drop-in folder for a given surface name.

    ``surface`` must be one of the keys in ``SURFACE_FOLDERS``.
    """
    if surface not in SURFACE_FOLDERS:
        raise ValueError(
            f"unknown drop-in surface {surface!r}; "
            f"expected one of {sorted(SURFACE_FOLDERS)}"
        )
    return dropin_root() / SURFACE_FOLDERS[surface]


def _module_name_for(surface: str, source: Path) -> str:
    """Synthetic ``sys.modules`` key for a drop-in module.

    Prefixed with ``_opencrayfish_dropin.`` so it can never collide
    with a real importable package the operator may have installed.
    """
    return f"_opencrayfish_dropin.{surface}.{source.stem}"


def _load_module(surface: str, source: Path) -> ModuleType | None:
    """Import a single drop-in source (file OR package folder).

    Returns the loaded module on success, ``None`` on failure.
    Failures are logged and isolated — they MUST NOT propagate.
    """
    if source.is_dir():
        init_py = source / "__init__.py"
        if not init_py.is_file():
            log.info(
                "DROPIN %s: skipping folder %s (no __init__.py)",
                surface, source,
            )
            return None
        spec_target = init_py
        # Sub-packages need submodule_search_locations so their own
        # relative imports (``from .api import ...``) resolve against
        # the drop-in folder rather than sys.path.
        submodule_search_locations = [str(source)]
    else:
        spec_target = source
        submodule_search_locations = None

    mod_name = _module_name_for(surface, source)
    try:
        spec = importlib.util.spec_from_file_location(
            mod_name,
            spec_target,
            submodule_search_locations=submodule_search_locations,
        )
        if spec is None or spec.loader is None:
            log.error(
                "DROPIN %s: failed to build spec for %s — skipping",
                surface, source,
            )
            return None
        module = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec so the module can
        # `import` its own siblings via the synthetic package name.
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        log.exception(
            "DROPIN %s: failed to import %s — skipping",
            surface, source,
        )
        # Pop the half-loaded module so a retry isn't poisoned.
        sys.modules.pop(mod_name, None)
        return None


def _extract_plugins(
    surface: str,
    source: Path,
    module: ModuleType,
) -> list[Any]:
    """Pull plug-in objects out of a successfully-imported module.

    Honours the documented module contract:
      * ``PLUGIN`` — single object
      * ``PLUGINS`` — iterable of objects

    Returns an empty list (with INFO log) if the module exposes
    neither — drop-in folders are allowed to contain shared helper
    modules.
    """
    if hasattr(module, "PLUGIN"):
        return [module.PLUGIN]
    if hasattr(module, "PLUGINS"):
        plugins = module.PLUGINS
        try:
            return list(plugins)
        except TypeError:
            log.warning(
                "DROPIN %s: %s exposes PLUGINS but it isn't iterable "
                "— skipping",
                surface, source,
            )
            return []
    log.info(
        "DROPIN %s: %s exposes neither PLUGIN nor PLUGINS — "
        "treating as helper module",
        surface, source,
    )
    return []


def iter_dropin_plugins(
    surface: str,
    *,
    root: Path | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield ``(source_label, raw_plugin)`` pairs from a surface root.

    ``source_label`` is a human-readable identifier (the file or
    folder stem) that the caller logs on failure. ``raw_plugin`` is
    whatever the module's ``PLUGIN`` / ``PLUGINS`` exposed — the
    caller is expected to feed it through the surface-specific
    ``_instantiate()`` helper.

    Folder layout rules:
      * Top-level ``.py`` files (excluding ``__init__.py`` and
        anything starting with ``_``) — each loaded individually.
      * Sub-folders with an ``__init__.py`` — loaded as a package.
        Files inside the sub-package are NOT scanned individually;
        the package's ``__init__.py`` is the only entry point.
      * Sub-folders without ``__init__.py`` — silently skipped (they
        may be data folders, examples, READMEs, etc.).

    If the surface root doesn't exist, this yields nothing — that's
    the normal case for an operator who only uses entry-points.
    """
    target = (root if root is not None else surface_root(surface))
    if not target.is_dir():
        log.debug(
            "DROPIN %s: root %s does not exist — skipping",
            surface, target,
        )
        return

    # Sort for deterministic load order across boots — makes logs
    # reproducible and lets operators predict which of two same-name
    # plug-ins wins (lexicographically smaller path wins, registry
    # rejects the second).
    candidates: list[Path] = []
    for entry in sorted(target.iterdir()):
        name = entry.name
        if name.startswith((".", "_")):
            continue
        if entry.is_file() and entry.suffix == ".py":
            candidates.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            candidates.append(entry)

    for source in candidates:
        module = _load_module(surface, source)
        if module is None:
            continue
        for raw in _extract_plugins(surface, source, module):
            yield (source.stem, raw)
