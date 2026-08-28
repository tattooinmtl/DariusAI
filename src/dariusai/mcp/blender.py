"""The Blender bridge — Darius's connection to Blender over MCP.

The server lives inside Blender (the `darius_blender_mcp` add-on shipped in
`addon/blender/`), so this side is a client that has to cope with the other
end coming and going as the user opens and closes Blender. It therefore
holds no persistent socket: it probes cheaply, and re-runs the handshake
automatically the first time a probe comes back healthy.

That is what makes the status light honest. `status()` is safe to call on a
timer — a red light costs one refused TCP connect, and a green one costs a
single GET.
"""

from __future__ import annotations

import threading
from typing import Any

from .client import GREEN, MCPClient, MCPError, ToolInfo, data_of

DEFAULT_BLENDER_HOST = "127.0.0.1"
DEFAULT_BLENDER_PORT = 8765
DEFAULT_BLENDER_ENDPOINT = f"http://{DEFAULT_BLENDER_HOST}:{DEFAULT_BLENDER_PORT}/mcp"


class BlenderBridge:
    def __init__(self, endpoint: str = DEFAULT_BLENDER_ENDPOINT, timeout: float = 30.0,
                 auth_token: str = "") -> None:
        self._lock = threading.Lock()
        self._endpoint = endpoint
        self._timeout = timeout
        self._auth_token = auth_token
        self._client = self._new_client()
        self.connected = False
        self.last_error = ""

    def _new_client(self) -> MCPClient:
        return MCPClient(self._endpoint, timeout=self._timeout, auth_token=self._auth_token)

    # ---- configuration ---------------------------------------------------
    @property
    def endpoint(self) -> str:
        return self._endpoint

    def configure(self, endpoint: str | None = None, auth_token: str | None = None) -> None:
        """Point at a different server. Drops the handshake — tools and
        server identity belong to the endpoint they came from."""
        with self._lock:
            if endpoint is not None:
                self._endpoint = endpoint
            if auth_token is not None:
                self._auth_token = auth_token
            self._client = self._new_client()
            self.connected = False
            self.last_error = ""

    # ---- state -----------------------------------------------------------
    @property
    def tools(self) -> list[ToolInfo]:
        return list(self._client.tools)

    def tool_names(self) -> list[str]:
        return [t.name for t in self._client.tools]

    def status(self, handshake: bool = True) -> dict[str, Any]:
        """What the light shows. `handshake=False` probes only — used by
        the poll when a caller just wants the colour."""
        state, detail = self._client.health()

        if state != GREEN:
            # Losing the server invalidates the handshake: whatever comes
            # back later may be a different Blender with different tools.
            self.connected = False
            self.last_error = detail
        elif handshake and not self.connected:
            try:
                self.connect()
                state, detail = self._client.health()
            except MCPError as exc:
                # Reachable and healthy-looking, but it will not talk MCP.
                self.connected = False
                self.last_error = str(exc)
                return self._payload("orange", f"handshake failed: {exc}")

        return self._payload(state, detail)

    def _payload(self, state: str, detail: str) -> dict[str, Any]:
        return {
            "state": state,
            "detail": detail,
            "endpoint": self._endpoint,
            "connected": self.connected,
            "server": self._client.server_info,
            "tools": self.tool_names(),
            "tool_count": len(self._client.tools),
            "instructions": self._client.instructions,
        }

    # ---- protocol --------------------------------------------------------
    def connect(self) -> dict[str, Any]:
        with self._lock:
            result = self._client.connect()
            self.connected = True
            self.last_error = ""
            return result

    def ensure_connected(self) -> None:
        if not self.connected:
            self.connect()

    def call(self, name: str, arguments: dict[str, Any] | None = None,
             timeout: float | None = None) -> Any:
        """Call a Blender tool, returning its decoded payload. Connects
        first if needed, so a caller never has to sequence the handshake
        itself."""
        self.ensure_connected()
        try:
            result = self._client.call_tool(name, arguments, timeout=timeout)
        except MCPError:
            # A dropped Blender looks exactly like a failed call; force the
            # next attempt to re-handshake rather than reusing a stale one.
            self.connected = False
            raise
        return data_of(result)


_bridge: BlenderBridge | None = None
_bridge_lock = threading.Lock()


def get_bridge(endpoint: str | None = None) -> BlenderBridge:
    """The process-wide bridge. One connection state shared by the status
    endpoint, the agent tools and the /3dgame command — three lights
    disagreeing about whether Blender is attached would be worse than none."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = BlenderBridge(endpoint or DEFAULT_BLENDER_ENDPOINT)
        elif endpoint and endpoint != _bridge.endpoint:
            _bridge.configure(endpoint=endpoint)
        return _bridge


def reset_bridge() -> None:
    """Tests only — drop the singleton so each one starts clean."""
    global _bridge
    with _bridge_lock:
        _bridge = None
