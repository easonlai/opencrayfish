"""tools.discovery — Entry-points-based third-party Tool discovery.

Mirror of ``core.skills.discovery`` one layer down. See that module's
docstring for the full design rationale; this file is intentionally
narrow to keep the two layers symmetric.

THE PROMISE
-----------
A third-party developer can publish an OpenCrayFish Tool as a
pip-installable package, and that package is auto-registered the
next time the agent boots. No code changes to ``main.py``.

Third-party packages opt in by adding to their ``pyproject.toml``::

    [project.entry-points."opencrayfish.tools"]
    home_assistant = "opencrayfish_tool_home_assistant:HomeAssistantTool"

The value is a ``module:attr`` import path that resolves to either:

  * A Tool class \u2014 instantiated with zero args.
  * A factory callable \u2014 called with zero args, expected to return
    a Tool instance. Factories are one way a Tool gets its
    configuration: the factory closes over the operator config read
    from outside. See ``examples/`` in the README for a worked
    pattern.
  * An already-instantiated Tool instance.

CONFIG INJECTION (preferred)
----------------------------
For most plug-ins, factory closures are not necessary: a Tool can
declare a ``manifest.config_key`` and implement an optional
``bind_context(ctx: ToolContext)`` method. The
``ToolRegistry`` will call ``bind_context`` exactly once after
``main.py`` finishes building the shared context, handing the Tool
its operator-supplied ``cfg.plugins.<key>`` slice plus stable
handles to ``soul`` / ``stm`` / ``monitor`` / ``provider``. See
``tools/base.py::ToolContext`` for the full surface and the
recommended pattern. This is symmetric with how Skills receive
``SkillContext.plugins_config`` and removes the need for the
factory-closure dance in the common case.

ISOLATION
---------
A misbehaving third-party Tool MUST NEVER prevent the agent from
starting. ``discover_external_tools`` wraps each entry-point in a
try/except so a single broken package only loses that one Tool,
not the whole boot.

SECURITY NOTE
-------------
Entry-points discovery executes arbitrary code from any installed
package that registers itself in the ``opencrayfish.tools`` group
\u2014 the standard Python plug-in security model. If you don't trust a
package, don't ``pip install`` it. The agent does NOT sandbox
third-party Tools today; the ``requires_caps`` manifest field is
informational only.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

from .base import Tool
from .registry import ToolRegistry

log = logging.getLogger(__name__)

# The entry-point group third-party packages register Tools under.
# Renaming this is a Tool Protocol break \u2014 bump
# SUPPORTED_TOOL_PROTOCOL_VERSIONS in tools/manifest.py.
TOOL_ENTRY_POINT_GROUP: str = "opencrayfish.tools"


def _instantiate(loaded: Any, ep_name: str) -> Tool | None:
    """Turn whatever ``ep.load()`` returned into a Tool instance.

    Accepts three shapes (mirror of skills.discovery._instantiate):
      * Tool instance already \u2014 returned as-is.
      * Class \u2014 instantiated with zero args.
      * Callable (factory) \u2014 called with zero args; result must
        be a Tool instance.

    Returns ``None`` (with a logged warning) if the loaded object is
    none of the above. We don't raise; isolation lives at the caller.
    """
    if isinstance(loaded, type):
        try:
            return loaded()  # type: ignore[return-value]
        except Exception:
            log.exception(
                "TOOL discovery ep=%s class instantiation failed",
                ep_name,
            )
            return None
    # Duck-typed: already a Tool instance.
    if hasattr(loaded, "name") and hasattr(loaded, "call"):
        return loaded
    if callable(loaded):
        try:
            result = loaded()
        except Exception:
            log.exception(
                "TOOL discovery ep=%s factory call failed",
                ep_name,
            )
            return None
        if hasattr(result, "name") and hasattr(result, "call"):
            return result
        log.warning(
            "TOOL discovery ep=%s factory returned %s, not a Tool",
            ep_name, type(result).__name__,
        )
        return None
    log.warning(
        "TOOL discovery ep=%s loaded object %s is not a Tool / class / "
        "callable \u2014 skipping",
        ep_name, type(loaded).__name__,
    )
    return None


def discover_external_tools(
    registry: ToolRegistry,
    *,
    group: str = TOOL_ENTRY_POINT_GROUP,
    filter_fn: Callable[[EntryPoint], bool] | None = None,
) -> list[str]:
    """Walk installed packages for Tools and register them.

    Should be called from ``main.py`` AFTER the first-party Tools
    are registered, so a third-party package can't accidentally
    shadow a built-in by registering the same name first (the
    registry's duplicate-name check then fails loud at the third-
    party registration, which is the right blast radius).

    Args:
        registry: The live ToolRegistry instance to register into.
        group: Entry-point group name. Override only in tests.
        filter_fn: Optional predicate ``(entry_point) -> bool`` for
            operator-side filtering (e.g. an allowlist of trusted
            package names). When ``None`` every entry-point in the
            group is loaded.

    Returns:
        List of registered Tool names. Tools that failed to load
        are NOT in this list (the failure is logged at WARNING).
    """
    registered: list[str] = []

    try:
        eps = entry_points(group=group)
    except Exception:
        log.exception(
            "TOOL discovery: entry_points(group=%r) lookup failed",
            group,
        )
        return registered

    for ep in eps:
        if filter_fn is not None and not filter_fn(ep):
            log.info("TOOL discovery ep=%s skipped by filter", ep.name)
            continue
        try:
            loaded = ep.load()
        except Exception:
            log.exception(
                "TOOL discovery ep=%s (%s) load failed \u2014 skipping",
                ep.name, ep.value,
            )
            continue
        tool = _instantiate(loaded, ep.name)
        if tool is None:
            continue
        try:
            registry.register(tool)
        except Exception:
            log.exception(
                "TOOL discovery ep=%s register failed — skipping",
                ep.name,
            )
            continue
        registered.append(getattr(tool, "name", ep.name))

    return registered


def discover_dropin_tools(
    registry: ToolRegistry,
    *,
    root: Path | None = None,
) -> list[str]:
    """Walk the drop-in folder for Tools and register them.

    No-pip-install path: each ``.py`` file or sub-package under
    ``plugins/tools/`` is loaded; its module-level ``PLUGIN`` /
    ``PLUGINS`` attribute is fed through the same ``_instantiate``
    + ``registry.register`` pipeline as the entry-points path.
    See ``core.dropin`` for the folder layout + module contract.

    Should run AFTER both first-party Tools and
    ``discover_external_tools`` — a drop-in attempting to shadow an
    existing name fails loud at the registry's duplicate-name check.

    Args:
        registry: The live ToolRegistry instance to register into.
        root: Override the drop-in folder root. ``None`` resolves to
            ``OPENCRAYFISH_PLUGINS_DIR`` env-var or
            ``<cwd>/plugins/tools/``. Override only in tests.

    Returns:
        List of registered Tool names. Tools that failed to load are
        NOT in this list.
    """
    from core.dropin import iter_dropin_plugins, surface_root

    registered: list[str] = []
    target = root if root is not None else surface_root("tools")

    for source_label, raw in iter_dropin_plugins("tools", root=target):
        tool = _instantiate(raw, source_label)
        if tool is None:
            continue
        try:
            registry.register(tool)
        except Exception:
            log.exception(
                "TOOL dropin %s register failed — skipping", source_label,
            )
            continue
        registered.append(getattr(tool, "name", source_label))

    if registered:
        log.info(
            "TOOL dropin root=%s registered %d tool(s): %s",
            target, len(registered), ", ".join(registered),
        )
    return registered

