"""OpenCrayFish — Tools (SearXNG web search, archive read, etc.).

Public re-exports so first-party + third-party callers don't need to
know the internal layout. Mirror of ``core/skills/__init__.py``.
"""
from __future__ import annotations

from .base import Tool, ToolContext, ToolResult
from .discovery import (
    TOOL_ENTRY_POINT_GROUP,
    discover_dropin_tools,
    discover_external_tools,
)
from .manifest import (
    DEFAULT_TOOL_PROTOCOL_VERSION,
    SUPPORTED_TOOL_PROTOCOL_VERSIONS,
    WELL_KNOWN_TOOL_CAPABILITIES,
    ToolManifest,
    resolve_tool_manifest,
)
from .mcp_bridge import (
    McpBridge,
    McpRemoteTool,
    McpServerSpec,
    make_mcp_bridge,
    parse_server_specs,
)
from .registry import ToolRegistry

__all__ = [
    "DEFAULT_TOOL_PROTOCOL_VERSION",
    "SUPPORTED_TOOL_PROTOCOL_VERSIONS",
    "TOOL_ENTRY_POINT_GROUP",
    "McpBridge",
    "McpRemoteTool",
    "McpServerSpec",
    "Tool",
    "ToolContext",
    "ToolManifest",
    "ToolRegistry",
    "ToolResult",
    "WELL_KNOWN_TOOL_CAPABILITIES",
    "discover_dropin_tools",
    "discover_external_tools",
    "make_mcp_bridge",
    "parse_server_specs",
    "resolve_tool_manifest",
]
