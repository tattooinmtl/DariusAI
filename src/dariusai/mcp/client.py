"""A minimal MCP client: JSON-RPC 2.0 over Streamable HTTP.

Stdlib only. The servers this talks to run inside host applications
(Blender's bundled Python, which has no third-party packages), so both
ends of the wire are deliberately dependency-free.

The handshake is the one in the MCP spec:

    initialize  ->  notifications/initialized  ->  tools/list

`health()` exists separately from the handshake because a status light has
to answer "is it there?" many times a minute without paying for a full
JSON-RPC round trip, and because it must distinguish *nothing is
listening* from *something is listening but it isn't a healthy MCP
server* — those are different colours to the user and different problems
to fix.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .. import __version__

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "dariusai"

# Traffic-light states, in the order the user reads them.
GREEN = "green"    # handshake-capable server answering healthily
ORANGE = "orange"  # something is on the port, but it is wrong or unwell
RED = "red"        # nothing is listening


class MCPError(RuntimeError):
    """The server answered, and the answer was an error."""


@dataclass
class ToolInfo:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mcp(cls, raw: dict[str, Any]) -> ToolInfo:
        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", "") or "",
            input_schema=raw.get("inputSchema") or {"type": "object", "properties": {}},
        )


def _split_endpoint(endpoint: str) -> tuple[str, int, str]:
    """(host, port, base-url) for an MCP endpoint URL."""
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port, f"{parsed.scheme}://{parsed.netloc}"


class MCPClient:
    def __init__(
        self,
        endpoint: str,
        timeout: float = 10.0,
        auth_token: str = "",
        client_name: str = CLIENT_NAME,
        client_version: str = __version__,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.auth_token = auth_token
        self.client_name = client_name
        self.client_version = client_version
        self.server_info: dict[str, Any] = {}
        self.instructions: str = ""
        self.tools: list[ToolInfo] = []
        self._id = 0

    # ---- wire ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Streamable HTTP servers may answer either way; say we take both.
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None,
             notify: bool = False, timeout: float | None = None) -> Any:
        self._id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._id
        if params is not None:
            payload["params"] = params

        request = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise MCPError(f"HTTP {exc.code} from {self.endpoint}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MCPError(f"cannot reach {self.endpoint}: {exc}") from exc

        if notify or not raw:
            return None
        try:
            message = json.loads(raw)
        except ValueError as exc:
            raise MCPError(f"{self.endpoint} did not answer with JSON") from exc
        if isinstance(message, dict) and "error" in message:
            error = message["error"] or {}
            raise MCPError(str(error.get("message") or error))
        return (message or {}).get("result")

    # ---- health ----------------------------------------------------------
    def health(self, connect_timeout: float = 1.0, read_timeout: float = 2.5) -> tuple[str, str]:
        """(state, detail) for the status light.

        A TCP connect is attempted first and on its own. urllib collapses
        "connection refused" and "connected but silent" into the same
        URLError, and those are exactly the two cases that have to be told
        apart: the first means Blender isn't running, the second means
        something is on the port but isn't a working MCP server.
        """
        host, port, base = _split_endpoint(self.endpoint)
        try:
            with socket.create_connection((host, port), timeout=connect_timeout):
                pass
        except OSError as exc:
            return RED, f"nothing listening on {host}:{port} ({getattr(exc, 'strerror', None) or exc})"

        try:
            request = urllib.request.Request(base + "/", headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=read_timeout) as response:
                info = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return ORANGE, f"{host}:{port} answered HTTP {exc.code}"
        except (TimeoutError, socket.timeout):
            return ORANGE, f"{host}:{port} accepted the connection but never answered"
        except ValueError:
            return ORANGE, f"{host}:{port} is not an MCP server (non-JSON response)"
        except (urllib.error.URLError, OSError) as exc:
            return ORANGE, f"{host}:{port} failed mid-request: {exc}"

        if not isinstance(info, dict) or "protocolVersion" not in info:
            return ORANGE, f"{host}:{port} is not an MCP server (unexpected payload)"
        if info.get("status") != "ok":
            return ORANGE, f"server reports status {info.get('status')!r}"
        return GREEN, (
            f"{info.get('server', 'mcp')} {info.get('version', '?')} · "
            f"{info.get('tools_count', 0)} tools"
        )

    # ---- protocol --------------------------------------------------------
    def initialize(self) -> dict[str, Any]:
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": self.client_name, "version": self.client_version},
        }) or {}
        self.server_info = result.get("serverInfo", {}) or {}
        self.instructions = result.get("instructions", "") or ""
        # Required by the spec, and servers may withhold tools until it
        # arrives. It is a notification: no id, no reply.
        self._rpc("notifications/initialized", {}, notify=True)
        return result

    def list_tools(self) -> list[ToolInfo]:
        result = self._rpc("tools/list") or {}
        self.tools = [ToolInfo.from_mcp(t) for t in result.get("tools", [])]
        return self.tools

    def connect(self) -> dict[str, Any]:
        """Full handshake. Returns the server's initialize result."""
        result = self.initialize()
        self.list_tools()
        return result

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  timeout: float | None = None) -> dict[str, Any]:
        """Call a tool. Raises MCPError when the server flags isError —
        an error the model can read is worth more than a silent {} that
        looks like success."""
        result = self._rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout,
        ) or {}
        if result.get("isError"):
            raise MCPError(text_of(result) or f"{name} failed")
        return result

    def ping(self) -> bool:
        try:
            self._rpc("ping", {}, timeout=3.0)
            return True
        except MCPError:
            return False


def text_of(result: dict[str, Any]) -> str:
    """Flatten an MCP tool result's content blocks into plain text."""
    parts = []
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def data_of(result: dict[str, Any]) -> Any:
    """Tool results carry JSON as text. Decode it when it is JSON, and
    fall back to the raw text when it isn't."""
    text = text_of(result)
    if not text:
        return result.get("structuredContent")
    try:
        return json.loads(text)
    except ValueError:
        return text
