"""Model Context Protocol client — Darius's side of an MCP connection.

Darius is the *client* here: the server lives in whatever tool it is
driving (for Blender, inside the add-on, in Blender's own process). This
package speaks JSON-RPC 2.0 over Streamable HTTP with nothing but the
standard library, so it adds no dependency to the app.
"""

from __future__ import annotations

from .blender import DEFAULT_BLENDER_ENDPOINT, BlenderBridge, get_bridge
from .client import MCPClient, MCPError, ToolInfo

__all__ = (
    "MCPClient",
    "MCPError",
    "ToolInfo",
    "BlenderBridge",
    "get_bridge",
    "DEFAULT_BLENDER_ENDPOINT",
)
