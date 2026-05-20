"""connectors.manifest \u2014 Declarative capability descriptor for Connectors.

This module is the third layer of the plug-in manifest stack, mirroring
``core.skills.manifest`` (capabilities) and ``tools.manifest`` (mechanisms).
A Connector is an *I/O surface* \u2014 it owns a chat transport, a webhook
endpoint, a voice loop, or any other way the agent exchanges messages
with the outside world.

WHY THIS EXISTS
---------------
Before this module a Connector advertised itself purely through whatever
constructor ``main.py`` happened to wire up. That worked when there were
two of them (Telegram + WebChat) and both shipped in-tree. For
OpenCrayFish to become a *framework* \u2014 third parties shipping new
connectors (Discord, Matrix, SIP voice, MQTT, Slack, \u2026) as their own
pip packages \u2014 the same metadata third-party Skills and Tools already
declare must exist here too:

  * **Unified** \u2014 one canonical place for the connector registry to
    read identity + config + capability metadata.
  * **Versioned** \u2014 a Connector built against the v1 protocol must
    say so; the core refuses ones built against revisions it doesn't
    know how to host.
  * **Dependency-aware** \u2014 a Connector that needs network ingress,
    network egress, or a specific ``cfg.plugins.<name>`` namespace
    declares so at boot. Misconfigured Connectors die loud BEFORE
    they open their socket and start dropping events.

``ConnectorManifest`` is the dataclass. ``resolve_connector_manifest``
is the back-compat synthesizer that reads the scattered class attributes
the in-tree Telegram + WebChat connectors already expose and builds an
equivalent manifest, so existing connectors don't need any code change
to keep working \u2014 only ones that want the new fields must add an
explicit ``manifest = ConnectorManifest(...)`` class attribute.

DESIGN PRINCIPLES (mirror SkillManifest / ToolManifest)
-------------------------------------------------------
* **Frozen + slotted.** Manifests are immutable contracts.
* **No runtime behaviour here.** Pure data; all policy lives in
  ``connectors.registry``.
* **Optional fields default to backwards-compatible values.**
* **Validation is split.** Static checks in ``__post_init__``;
  cross-connector checks in ``ConnectorRegistry.bootstrap_validate``.

VERSIONING POLICY
-----------------
``compat_version`` declares the Connector Protocol revision. Set of
supported revisions lives in ``SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS``;
a Connector outside that set is refused at registration. Same
two-majors support promise as the other layers.

CAPABILITY TOKENS
-----------------
``requires_caps`` lets a Connector advertise what kind of network /
filesystem / process surface it needs. The operator may eventually
gate this against an allowlist; today the tokens are informational +
surfaced on the dashboard. Well-known tokens:

  * ``network.outbound`` \u2014 makes outbound HTTP / TCP calls (Telegram
    polling client, Discord gateway, SIP REGISTER, \u2026).
  * ``network.inbound``  \u2014 listens on a TCP port for inbound
    connections (WebChat HTTP server, MQTT subscriber, \u2026).
  * ``ipc.local``        \u2014 listens on a unix domain socket / named
    pipe for local-only IPC (a voice front-end, a Pi GPIO bridge).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Connector Protocol revisions the running core knows how to host.
# Bump this set AND add a migration note in CONTRIBUTING.md whenever
# a breaking change ships in the Connector surface. Convention mirrors
# SkillManifest / ToolManifest: ``"connector-protocol/N"``.
SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS: frozenset[str] = frozenset({
    "connector-protocol/1",
})

# Default protocol the back-compat synthesizer stamps onto a Connector
# that doesn't expose an explicit ``manifest`` \u2014 i.e. every
# first-party Connector written before manifests existed.
DEFAULT_CONNECTOR_PROTOCOL_VERSION: str = "connector-protocol/1"


# Reserved capability tokens the registry knows how to interpret when
# validating a Connector's ``requires_caps``. Unknown tokens are
# allowed at registration but logged so third-party authors can ship
# their own conventions. Document new ones here when they graduate.
WELL_KNOWN_CONNECTOR_CAPABILITIES: frozenset[str] = frozenset({
    "network.outbound",  # outbound HTTP / TCP (Telegram poll, Discord gateway).
    "network.inbound",   # binds a TCP port for inbound connections (WebChat).
    "ipc.local",         # binds a unix socket / named pipe (voice, GPIO bridge).
    "filesystem.read",   # reads files outside its own state dir.
    "filesystem.write",  # writes files outside its own state dir.
    "subprocess",        # spawns subprocesses (e.g. voice stack).
})


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    """The declarative contract between a Connector and the core.

    Every field is part of the *plug-in surface*: adding, removing,
    or changing the semantics of any field is a Connector Protocol
    break and MUST bump ``SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS``.
    Don't add fields for convenience \u2014 extend the optional ``extras``
    map instead.
    """

    # -- Identity -------------------------------------------------------------

    name: str
    """Registry key. Lowercase snake_case. Must be unique per process.
    Convention: noun naming the transport (``telegram``, ``web_chat``,
    ``discord``, ``matrix``, ``mqtt``, \u2026). The registry refuses
    duplicates at boot."""

    description: str
    """One-line human-readable purpose. Surfaced on the dashboard's
    Connectors panel. Keep \u2264 80 chars."""

    # -- Protocol versioning --------------------------------------------------

    compat_version: str = DEFAULT_CONNECTOR_PROTOCOL_VERSION
    """Connector Protocol revision this Connector was written against.
    Must be one of ``SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS`` at
    registration time."""

    # -- Dependencies + capability surface ------------------------------------

    requires_caps: tuple[str, ...] = ()
    """Capability tokens this Connector exercises (see
    ``WELL_KNOWN_CONNECTOR_CAPABILITIES``). Informational today; the
    registry logs unknown tokens at boot. A future sandbox layer may
    gate Connectors against an operator-configured allowlist."""

    config_key: str | None = None
    """Optional ``cfg.plugins.<key>`` namespace this Connector reads at
    construct time. ``bootstrap_validate`` warns if a Connector
    declares a config_key that the live ``cfg.plugins`` map doesn't
    contain \u2014 third-party Connectors shipping required config surface
    that gap at boot, not at first event."""

    # -- Extension slot -------------------------------------------------------

    extras: dict[str, Any] = field(default_factory=dict)
    """Free-form key/value pairs for fields not yet stable enough to
    warrant their own typed slot. When a key proves useful across
    multiple Connectors it should graduate to a real field + protocol
    bump."""

    # -- Static validation ---------------------------------------------------

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(
                f"ConnectorManifest.name must be a non-empty string, "
                f"got {self.name!r}"
            )
        if any(ch.isspace() for ch in self.name):
            raise ValueError(
                f"ConnectorManifest.name must not contain whitespace: "
                f"{self.name!r}"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(
                f"ConnectorManifest.description must be a non-empty "
                f"string for connector {self.name!r}"
            )
        if self.compat_version not in SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS:
            raise ValueError(
                f"ConnectorManifest({self.name!r}): compat_version "
                f"{self.compat_version!r} is not supported by this "
                f"core. Supported: "
                f"{sorted(SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS)}."
            )
        if self.config_key is not None and (
            not isinstance(self.config_key, str)
            or not self.config_key.strip()
        ):
            raise ValueError(
                f"ConnectorManifest({self.name!r}): config_key must be "
                f"a non-empty string or None, got {self.config_key!r}"
            )


# ---------------------------------------------------------------------------
# Back-compat synthesizer
# ---------------------------------------------------------------------------


def _to_str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def resolve_connector_manifest(connector: Any) -> ConnectorManifest:
    """Return the Connector's declared manifest, or synthesize one.

    Resolution order mirrors ``resolve_tool_manifest``:
      1. If the Connector exposes a class-level ``manifest`` attribute
         that is already a ``ConnectorManifest`` instance, return it.
      2. If the Connector exposes a ``manifest`` attribute that is a
         dict, build a ``ConnectorManifest(**that_dict)``.
      3. Otherwise synthesize from the legacy scattered attributes
         (``name``, ``description``, \u2026). The ``compat_version`` is
         stamped as ``DEFAULT_CONNECTOR_PROTOCOL_VERSION``.

    Raises:
        ValueError: if the Connector is missing usable ``name`` or
            ``description`` (re-raised from ``__post_init__``).
    """
    declared = getattr(connector, "manifest", None)
    if isinstance(declared, ConnectorManifest):
        return declared
    if isinstance(declared, dict):
        return ConnectorManifest(**declared)

    # Legacy synthesis: a connector without explicit metadata gets a
    # name derived from its class, a description from its docstring
    # first line, and no caps / config_key.
    name = getattr(connector, "name", None)
    if not isinstance(name, str) or not name:
        # Class name camelCase -> snake_case fallback (TelegramConnector
        # -> telegram). Strip trailing "Connector" if present.
        cls_name = type(connector).__name__
        if cls_name.endswith("Connector"):
            cls_name = cls_name[: -len("Connector")]
        name = ""
        for i, ch in enumerate(cls_name):
            if ch.isupper() and i > 0:
                name += "_"
            name += ch.lower()
        if not name:
            raise ValueError(
                f"Cannot resolve manifest for {connector!r}: missing "
                "`name` attribute and class name yields empty fallback."
            )

    description = getattr(connector, "description", None)
    if not isinstance(description, str) or not description.strip():
        doc = (type(connector).__doc__ or "").strip()
        # First non-empty line of the docstring.
        first_line = next(
            (line.strip() for line in doc.splitlines() if line.strip()),
            f"{name} connector",
        )
        description = first_line

    return ConnectorManifest(
        name=name,
        description=description,
        compat_version=DEFAULT_CONNECTOR_PROTOCOL_VERSION,
        requires_caps=_to_str_tuple(getattr(connector, "requires_caps", ())),
        config_key=getattr(connector, "config_key", None),
        extras=dict(getattr(connector, "extras", {}) or {}),
    )
