"""JSON-RPC 2.0 + MCP wire layer.

Deliberately free of `bpy`: the registry and the dispatcher are pure
functions over dicts, so the whole protocol can be exercised in Darius's
test suite without Blender. Everything that actually touches Blender lives
behind the tool handlers.
"""

from __future__ import annotations

from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "darius-blender-mcp"
SERVER_VERSION = "1.0.0"
SERVER_INSTRUCTIONS = (
    "Drive Blender from DariusAI. Call `get_addon_info` first to learn what this "
    "build supports. For game art, `game3d_health_check` then `game3d_scene_setup`, "
    "`game3d_build_structure` / `game3d_build_unit`, `game3d_render_asset`. "
    "Every tool runs on Blender's main thread."
)

# JSON-RPC error codes (spec-defined; -32000..-32099 is the server range).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolDefinition:
    def __init__(self, name: str, description: str, input_schema: dict[str, Any],
                 handler: Callable[..., Any]) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.handler = handler

    def to_mcp(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    """Tools register themselves at import time via the decorator, so the
    set of tools is whatever `tools/__init__.py` imports — one list, no
    second place to forget to update."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def tool(self, name: str, description: str = "",
             input_schema: dict[str, Any] | None = None) -> Callable:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = ToolDefinition(name, description or (func.__doc__ or "").strip(),
                                               input_schema or {}, func)
            return func
        return decorator

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def list_mcp(self) -> list[dict[str, Any]]:
        return [self._tools[n].to_mcp() for n in sorted(self._tools)]

    def __len__(self) -> int:
        return len(self._tools)


def ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def health_payload(registry: ToolRegistry, status: str = "ok") -> dict[str, Any]:
    """What `GET /` returns. Darius's status light reads exactly this: the
    keys are a contract, not debug output."""
    return {
        "status": status,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "tools_count": len(registry),
    }


def dispatch(request: dict[str, Any], registry: ToolRegistry,
             invoke: Callable[[ToolDefinition, dict[str, Any]], Any] | None = None
             ) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for notifications, which
    by spec get no response body.

    `invoke` is how a tool actually runs — the HTTP layer passes one that
    marshals onto Blender's main thread. Defaulting to a direct call keeps
    this callable from tests.
    """
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 request")

    method = request.get("method")
    request_id = request.get("id")

    if request_id is None:
        # Notifications: acknowledge nothing, but never treat an unknown
        # one as an error the client has to care about.
        return None

    if method == "initialize":
        return ok(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })

    if method == "ping":
        return ok(request_id, {})

    if method == "tools/list":
        return ok(request_id, {"tools": registry.list_mcp()})

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return error(request_id, INVALID_PARAMS, "arguments must be an object")
        tool = registry.get(name)
        if tool is None:
            return error(request_id, INVALID_PARAMS, f"unknown tool: {name}")
        runner = invoke or (lambda t, a: t.handler(**a))
        try:
            payload = runner(tool, arguments)
        except TypeError as exc:
            # Wrong/missing arguments: a protocol-level complaint, so the
            # caller can fix the call rather than reading a stack trace.
            return error(request_id, INVALID_PARAMS, f"{name}: {exc}")
        except Exception as exc:  # a failing tool must not kill the server
            return ok(request_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return ok(request_id, {"content": [{"type": "text", "text": to_text(payload)}],
                               "isError": False})

    return error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def to_text(payload: Any) -> str:
    """Tool results travel as text content blocks; structured payloads go
    as JSON so the client can decode them."""
    import json

    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return str(payload)
