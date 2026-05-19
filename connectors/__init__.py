"""OpenCrayFish — External Interfaces (chat transports, webhook bridges, voice loops).

This package now exposes a framework-level plug-in surface so
third-party Connectors (Discord, Matrix, MQTT, SIP voice, …) can
ship as their own pip packages and be auto-registered at boot via
the ``opencrayfish.connectors`` entry-point group.

See ``connectors.manifest`` for the contract, ``connectors.registry``
for the lifecycle owner, and ``connectors.discovery`` for the
entry-points loader.
"""

from .discovery import (
    CONNECTOR_ENTRY_POINT_GROUP,
    discover_external_connectors,
)
from .manifest import (
    DEFAULT_CONNECTOR_PROTOCOL_VERSION,
    SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS,
    WELL_KNOWN_CONNECTOR_CAPABILITIES,
    ConnectorManifest,
    resolve_connector_manifest,
)
from .registry import ConnectorRegistry

__all__ = [
    "CONNECTOR_ENTRY_POINT_GROUP",
    "ConnectorManifest",
    "ConnectorRegistry",
    "DEFAULT_CONNECTOR_PROTOCOL_VERSION",
    "SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS",
    "WELL_KNOWN_CONNECTOR_CAPABILITIES",
    "discover_external_connectors",
    "resolve_connector_manifest",
]
