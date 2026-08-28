"""Server package — protocol types, the tool registry, and the HTTP server."""

from __future__ import annotations

from .protocol import (
    PROTOCOL_VERSION,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_VERSION,
    ToolDefinition,
    ToolRegistry,
    dispatch,
    health_payload,
)

# `mcp_server` imports bpy, so importing this package outside Blender must
# not drag it in. The protocol layer above is deliberately bpy-free and is
# what the test suite exercises.
try:  # pragma: no cover - exercised inside Blender
    from .mcp_server import (
        DariusMCPServer,
        MainThreadTimeout,
        get_server,
        registry,
        run_on_main,
    )
except ImportError:  # pragma: no cover - outside Blender
    DariusMCPServer = None  # type: ignore[assignment]
    MainThreadTimeout = RuntimeError  # type: ignore[misc]
    get_server = None  # type: ignore[assignment]
    registry = ToolRegistry()
    run_on_main = None  # type: ignore[assignment]

__all__ = (
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SERVER_INSTRUCTIONS",
    "ToolDefinition",
    "ToolRegistry",
    "dispatch",
    "health_payload",
    "registry",
    "run_on_main",
    "get_server",
    "DariusMCPServer",
    "MainThreadTimeout",
)
