"""connectors.registry \u2014 Lookup + lifecycle for Connector plug-ins.

Mirror of ``tools.registry.ToolRegistry`` one layer further out: where
the ToolRegistry owns mechanisms and SkillRegistry owns capabilities,
the ConnectorRegistry owns the *I/O surfaces* that bring messages into
the agent (chat transports, webhook servers, voice loops, etc.).

WHY THIS EXISTS
---------------
The pre-framework wiring constructed Connectors directly in ``main.py``
and held their references in local variables (``telegram = TelegramConnector(\u2026)``,
``web_chat = WebChatConnector(\u2026)``). That works when there are two
connectors that always exist. For third-party connectors discovered
via entry-points to be first-class citizens, they need:

  * **Uniform lifecycle** \u2014 every connector goes through
    ``start()`` at boot and ``stop()`` at shutdown, no matter who
    shipped it.
  * **Boot-time validation** \u2014 a Connector that declares a
    ``config_key`` MUST find a matching ``cfg.plugins.<key>`` map,
    or boot fails loud BEFORE the socket binds.
  * **Bootstrap collision detection** \u2014 two connectors with the
    same name (e.g. two third-party Telegram packages) fail at
    registration, not after the second one silently shadows the
    first.
  * **Cap-token surface** \u2014 the dashboard shows what each
    connector touches (``network.inbound``, ``network.outbound``,
    \u2026), which is part of the operator's mental model.

The ConnectorRegistry handles all of the above without changing how
each Connector implements its own ``start``/``stop`` \u2014 those methods
remain native to the Connector and are simply called through the
registry's lifecycle helpers.

NB: The registry does NOT enforce a hard Connector Protocol like
``Skill``/``Tool`` do (no abstract class, no Protocol). The Connector
contract today is duck-typed (``start`` + ``stop`` coroutines, name
attribute). The registry is the bookkeeping layer; the contract
itself can grow over time without rewriting every implementation.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .manifest import (
    SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS,
    WELL_KNOWN_CONNECTOR_CAPABILITIES,
    ConnectorManifest,
    resolve_connector_manifest,
)

log = logging.getLogger(__name__)


class ConnectorRegistry:
    """Owns the live set of Connector instances for one agent process.

    Mirrors ``tools.registry.ToolRegistry``. See the module docstring
    for the reasoning behind each method.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, Any] = {}
        # Parallel map: connector name -> resolved ConnectorManifest.
        # Populated at register() so consumers (dashboard panel,
        # bootstrap_validate) read uniform manifest objects rather
        # than poking scattered class attributes.
        self._manifests: dict[str, ConnectorManifest] = {}
        # Names of connectors whose start()/stop() the CALLER manages
        # explicitly (e.g. the in-tree Telegram connector wraps
        # python-telegram-bot's own ``Application`` lifecycle and the
        # in-tree WebChat connector is started/stopped from main.py
        # for shutdown-order reasons). The registry skips these in
        # ``start_all`` / ``aclose_all`` so we never double-start or
        # double-stop a connector. Third-party connectors discovered
        # via entry-points NEVER end up in this set; they get full
        # registry-managed lifecycle for free.
        self._external_lifecycle: set[str] = set()
        # Optional callback invoked AFTER any register/unregister so a
        # dashboard publisher can re-snapshot the connectors panel
        # without polling. Mirrors the other two registries.
        self._change_listener: Callable[[], None] | None = None

    # ---------- registration --------------------------------------------------

    def set_change_listener(self, cb: Callable[[], None] | None) -> None:
        """Install (or clear with None) a fn called after every
        register/unregister."""
        self._change_listener = cb

    def _notify_change(self) -> None:
        cb = self._change_listener
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception("CONNECTOR change_listener raised (continuing)")

    def register(self, connector: Any, *, external_lifecycle: bool = False) -> None:
        """Add a connector. Raises ValueError on duplicate name.

        Resolves the manifest (declared or synthesized) and stores it
        alongside the instance. A Connector whose manifest fails
        validation is rejected here rather than at first event \u2014
        fail loud.

        Args:
            connector: The Connector instance to register.
            external_lifecycle: When True, the registry will NOT call
                ``start()``/``stop()`` on this connector during
                ``start_all`` / ``aclose_all``. Use this for in-tree
                connectors that main.py drives explicitly (e.g.
                Telegram's ``Application.start()`` lifecycle, or
                connectors that need ordered shutdown relative to
                other subsystems). Defaults to False so that
                third-party connectors discovered via entry-points
                get full registry-managed lifecycle by default.
        """
        manifest = resolve_connector_manifest(connector)
        name = manifest.name

        attr_name = getattr(connector, "name", None)
        if isinstance(attr_name, str) and attr_name and attr_name != name:
            raise ValueError(
                f"Connector {connector!r}: manifest.name={name!r} "
                f"disagrees with class attribute name={attr_name!r}. "
                "Pick one source of truth (manifest wins, but the "
                "legacy attribute should either match or be removed)."
            )

        if name in self._connectors:
            raise ValueError(
                f"Connector name {name!r} is already registered. "
                "Pick a unique name or unregister first."
            )
        self._connectors[name] = connector
        self._manifests[name] = manifest
        if external_lifecycle:
            self._external_lifecycle.add(name)
        log.info(
            "CONNECTOR registered name=%s protocol=%s requires_caps=%s "
            "config_key=%s external_lifecycle=%s",
            name,
            manifest.compat_version,
            ",".join(manifest.requires_caps) or "-",
            manifest.config_key or "-",
            external_lifecycle,
        )
        self._notify_change()

    def unregister(self, name: str) -> Any | None:
        """Remove and return the connector with this name, or None if
        absent. Does NOT call ``stop()`` \u2014 caller decides what to do
        with it."""
        removed = self._connectors.pop(name, None)
        self._manifests.pop(name, None)
        self._external_lifecycle.discard(name)
        if removed is not None:
            self._notify_change()
        return removed

    # ---------- lookup --------------------------------------------------------

    def get(self, name: str) -> Any | None:
        return self._connectors.get(name)

    def has(self, name: str) -> bool:
        return name in self._connectors

    def names(self) -> list[str]:
        return sorted(self._connectors.keys())

    def all(self) -> list[Any]:
        """Return the live connector instances in registration order
        \u2014 callers that want to iterate for ``start()``/``stop()``
        rely on this order."""
        return list(self._connectors.values())

    # ---------- manifest accessors -------------------------------------------

    def manifest(self, name: str) -> ConnectorManifest | None:
        return self._manifests.get(name)

    def manifests(self) -> dict[str, ConnectorManifest]:
        """Return a defensive copy of the name -> manifest map."""
        return dict(self._manifests)

    # ---------- bootstrap validation -----------------------------------------

    def bootstrap_validate(
        self,
        *,
        plugins_config: dict[str, Any] | None = None,
        strict: bool = True,
    ) -> list[str]:
        """Fail-loud cross-validation called once after all Connectors
        have been registered in ``main.py`` (and after entry-points
        discovery has run).

        Mirrors ``ToolRegistry.bootstrap_validate``. Checks per
        Connector:
          1. ``compat_version`` in
             ``SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS`` (also enforced
             at ``register()``; re-checked as a belt-and-braces guard).
          2. If ``config_key`` is set, the matching
             ``cfg.plugins.<key>`` namespace exists when
             ``plugins_config`` is supplied. Skipped when
             ``plugins_config`` is None (unit tests).
          3. Capability tokens in ``requires_caps`` are logged when
             unknown so third-party packages can ship their own
             conventions without breaking validation.

        Returns:
            A list of human-readable problem descriptions. Empty list
            means everything checks out.

        Raises:
            RuntimeError: when ``strict=True`` and at least one
                problem was found. The exception message lists every
                problem so the operator gets the full picture in one
                go.
        """
        problems: list[str] = []
        for name, manifest in self._manifests.items():
            # (1) Protocol version
            if (
                manifest.compat_version
                not in SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS
            ):
                problems.append(
                    f"connector {name!r}: compat_version "
                    f"{manifest.compat_version!r} not in "
                    f"{sorted(SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS)}"
                )
            # (2) Config namespace presence
            if manifest.config_key and plugins_config is not None:
                if manifest.config_key not in plugins_config:
                    problems.append(
                        f"connector {name!r}: declares config_key="
                        f"{manifest.config_key!r} but cfg.plugins has "
                        f"no such key (available: "
                        f"{sorted(plugins_config.keys())})"
                    )
            # (3) Capability tokens \u2014 warning only
            for cap in manifest.requires_caps:
                if cap not in WELL_KNOWN_CONNECTOR_CAPABILITIES:
                    log.info(
                        "CONNECTOR bootstrap_validate name=%s unknown "
                        "capability token %r (allowed but undocumented)",
                        name, cap,
                    )

        if problems and strict:
            raise RuntimeError(
                "Connector bootstrap validation failed:\n  - "
                + "\n  - ".join(problems)
            )
        if problems:
            for p in problems:
                log.warning("CONNECTOR bootstrap_validate problem: %s", p)
        return problems

    # ---------- lifecycle -----------------------------------------------------

    async def start_all(self) -> list[str]:
        """Best-effort ``start()`` on every registry-managed connector.

        Skips connectors registered with ``external_lifecycle=True``
        (the caller drives those itself) and connectors without an
        ``async def start()`` coroutine (the contract is duck-typed
        so a polling-only connector is allowed to omit start).

        Returns the list of connector names successfully started.
        Mirrors ``aclose_all``'s isolation: a failure to start one
        third-party connector logs the exception and continues, so
        a broken plug-in can never block the agent boot.
        """
        started: list[str] = []
        for name, connector in list(self._connectors.items()):
            if name in self._external_lifecycle:
                continue
            start = getattr(connector, "start", None)
            if start is None or not callable(start):
                log.info(
                    "CONNECTOR start_all name=%s has no start() \u2014 skipping",
                    name,
                )
                continue
            try:
                result = start()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                log.exception(
                    "CONNECTOR start_all name=%s start() raised "
                    "(continuing with the rest)",
                    name,
                )
                continue
            started.append(name)
        if started:
            log.info(
                "CONNECTOR start_all started %d connector(s): %s",
                len(started), ", ".join(started),
            )
        return started

    async def aclose_all(self) -> None:
        """Best-effort ``stop()`` on every registry-managed connector.

        Skips connectors registered with ``external_lifecycle=True``
        (main.py drives those itself \u2014 e.g. ``tg_app.stop()`` for
        Telegram). Mirrors ``SkillRegistry.aclose_all``. One bad
        shutdown does NOT block the others; main.py calls this once
        at SIGINT / SIGTERM. Connectors without a ``stop`` coroutine
        are silently skipped.
        """
        for name, connector in list(self._connectors.items()):
            if name in self._external_lifecycle:
                continue
            stop = getattr(connector, "stop", None)
            if stop is None or not callable(stop):
                continue
            try:
                result = stop()
                # ``stop()`` may be sync or async \u2014 await if needed.
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                log.exception(
                    "CONNECTOR aclose name=%s stop() raised (continuing)",
                    name,
                )

    # ---------- prompt / dashboard surface ------------------------------------

    def inventory_lines(self) -> list[str]:
        """Build the dashboard's Connectors panel rows.

        One line per registered connector. The dashboard renderer
        composes this; the registry just owns the data shape so the
        panel stays in sync with what is actually live.
        """
        if not self._connectors:
            return ["(no connectors registered)"]
        lines: list[str] = []
        for name in sorted(self._connectors.keys()):
            m = self._manifests.get(name)
            if m is None:  # belt-and-braces; should never happen
                lines.append(f"- {name}")
                continue
            caps = ",".join(m.requires_caps) or "-"
            lines.append(
                f"- {name}: {m.description}  [caps={caps}]"
            )
        return lines
