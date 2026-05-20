"""connectors.discovery \u2014 Entry-points-based third-party Connector discovery.

Mirror of ``core.skills.discovery`` and ``tools.discovery`` one layer
out. See those modules' docstrings for the full design rationale;
this file is intentionally narrow to keep the three layers symmetric.

THE PROMISE
-----------
A third-party developer can publish an OpenCrayFish Connector
(Discord, Matrix, Slack, MQTT, SIP voice, \u2026) as a pip-installable
package, and that package is auto-registered next time the agent
boots. No code changes to ``main.py``.

Third-party packages opt in by adding to their ``pyproject.toml``::

    [project.entry-points."opencrayfish.connectors"]
    discord = "opencrayfish_connector_discord:DiscordConnector"

The value is a ``module:attr`` import path that resolves to either:

  * A Connector class \u2014 instantiated with zero args.
  * A factory callable \u2014 called with zero args, expected to return
    a Connector instance. Factories are how a Connector gets its
    configuration: the factory closes over ``cfg.plugins.<key>`` read
    from the caller's config.
  * An already-instantiated Connector instance.

ISOLATION
---------
A misbehaving third-party Connector MUST NEVER prevent the agent
from starting. ``discover_external_connectors`` wraps each
entry-point in a try/except so a single broken package only loses
that one Connector, not the whole boot.

SECURITY NOTE
-------------
Entry-points discovery executes arbitrary code from any installed
package that registers itself in the ``opencrayfish.connectors``
group \u2014 the standard Python plug-in security model. If you don't
trust a package, don't ``pip install`` it. The agent does NOT
sandbox third-party Connectors; the ``requires_caps`` manifest
field is informational only.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

from .registry import ConnectorRegistry

log = logging.getLogger(__name__)

# The entry-point group third-party packages register Connectors
# under. Renaming this is a Connector Protocol break \u2014 bump
# SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS in connectors/manifest.py.
CONNECTOR_ENTRY_POINT_GROUP: str = "opencrayfish.connectors"


def _is_connector_shaped(obj: Any) -> bool:
    """Duck-typed Connector check: must have a ``name`` and at least
    one of the lifecycle coroutines (``start`` or ``stop``)."""
    if not hasattr(obj, "name"):
        return False
    return hasattr(obj, "start") or hasattr(obj, "stop")


def _instantiate(loaded: Any, ep_name: str) -> Any | None:
    """Turn whatever ``ep.load()`` returned into a Connector instance.

    Accepts three shapes (mirror of ``tools.discovery._instantiate``):
      * Connector instance already \u2014 returned as-is.
      * Class \u2014 instantiated with zero args.
      * Callable (factory) \u2014 called with zero args; result must be
        Connector-shaped.

    Returns ``None`` (with a logged warning) if the loaded object
    is none of the above. We don't raise; isolation lives at the
    caller.
    """
    if isinstance(loaded, type):
        try:
            return loaded()
        except Exception:
            log.exception(
                "CONNECTOR discovery ep=%s class instantiation failed",
                ep_name,
            )
            return None
    if _is_connector_shaped(loaded):
        return loaded
    if callable(loaded):
        try:
            result = loaded()
        except Exception:
            log.exception(
                "CONNECTOR discovery ep=%s factory call failed",
                ep_name,
            )
            return None
        if _is_connector_shaped(result):
            return result
        log.warning(
            "CONNECTOR discovery ep=%s factory returned %s, not a "
            "Connector",
            ep_name, type(result).__name__,
        )
        return None
    log.warning(
        "CONNECTOR discovery ep=%s loaded object %s is not a "
        "Connector / class / callable \u2014 skipping",
        ep_name, type(loaded).__name__,
    )
    return None


def discover_external_connectors(
    registry: ConnectorRegistry,
    *,
    group: str = CONNECTOR_ENTRY_POINT_GROUP,
    filter_fn: Callable[[EntryPoint], bool] | None = None,
) -> list[str]:
    """Walk installed packages for Connectors and register them.

    Should be called from ``main.py`` AFTER the first-party
    Connectors are registered, so a third-party package can't
    accidentally shadow a built-in by registering the same name
    first (the registry's duplicate-name check then fails loud at
    the third-party registration, which is the right blast radius).

    Args:
        registry: The live ConnectorRegistry instance to register
            into.
        group: Entry-point group name. Override only in tests.
        filter_fn: Optional predicate ``(entry_point) -> bool`` for
            operator-side filtering (e.g. an allowlist of trusted
            package names). When ``None`` every entry-point in the
            group is loaded.

    Returns:
        List of registered Connector names. Connectors that failed
        to load are NOT in this list (the failure is logged at
        WARNING).
    """
    registered: list[str] = []

    try:
        eps = entry_points(group=group)
    except Exception:
        log.exception(
            "CONNECTOR discovery: entry_points(group=%r) lookup failed",
            group,
        )
        return registered

    for ep in eps:
        if filter_fn is not None and not filter_fn(ep):
            log.info(
                "CONNECTOR discovery ep=%s skipped by filter", ep.name,
            )
            continue
        try:
            loaded = ep.load()
        except Exception:
            log.exception(
                "CONNECTOR discovery ep=%s (%s) load failed \u2014 skipping",
                ep.name, ep.value,
            )
            continue
        connector = _instantiate(loaded, ep.name)
        if connector is None:
            continue
        try:
            registry.register(connector)
        except Exception:
            log.exception(
                "CONNECTOR discovery ep=%s register failed \u2014 skipping",
                ep.name,
            )
            continue
        registered.append(getattr(connector, "name", ep.name))

    return registered


def discover_dropin_connectors(
    registry: ConnectorRegistry,
    *,
    root: Path | None = None,
) -> list[str]:
    """Walk the drop-in folder for Connectors and register them.

    No-pip-install path: each ``.py`` file or sub-package under
    ``plugins/connectors/`` is loaded; its module-level ``PLUGIN`` /
    ``PLUGINS`` attribute is fed through the same ``_instantiate``
    + ``registry.register`` pipeline as the entry-points path.
    See ``core.dropin`` for the folder layout + module contract.

    Should run AFTER both first-party Connectors and
    ``discover_external_connectors`` — a drop-in attempting to
    shadow an existing name fails loud at the registry's
    duplicate-name check.

    Args:
        registry: The live ConnectorRegistry instance to register
            into.
        root: Override the drop-in folder root. ``None`` resolves to
            ``OPENCRAYFISH_PLUGINS_DIR`` env-var or
            ``<cwd>/plugins/connectors/``. Override only in tests.

    Returns:
        List of registered Connector names. Connectors that failed
        to load are NOT in this list.
    """
    from core.dropin import iter_dropin_plugins, surface_root

    registered: list[str] = []
    target = root if root is not None else surface_root("connectors")

    for source_label, raw in iter_dropin_plugins("connectors", root=target):
        connector = _instantiate(raw, source_label)
        if connector is None:
            continue
        try:
            registry.register(connector)
        except Exception:
            log.exception(
                "CONNECTOR dropin %s register failed — skipping",
                source_label,
            )
            continue
        registered.append(getattr(connector, "name", source_label))

    if registered:
        log.info(
            "CONNECTOR dropin root=%s registered %d connector(s): %s",
            target, len(registered), ", ".join(registered),
        )
    return registered

