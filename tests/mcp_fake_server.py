"""A stand-in for the Blender add-on's MCP server.

Blender cannot run in the test suite, but the wire protocol can: this
replicates the add-on's HTTP surface exactly — same JSON-RPC 2.0 methods,
same `GET /` health payload — so the client, the bridge, the status
endpoint and the traffic light are all tested against the real shape.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "darius-blender-mcp"
SERVER_VERSION = "1.0.0"

DEFAULT_TOOLS = [
    {"name": "get_addon_info", "description": "capabilities and scene state",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "scene_info", "description": "scene statistics",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "game3d_health_check", "description": "3dgame readiness",
     "inputSchema": {"type": "object", "properties": {}}},
]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        pass

    # -- helpers
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def _cfg(self):
        return self.server.config

    # -- verbs
    def do_GET(self):
        if self._cfg.get("health_payload") is not None:
            self._send(200, self._cfg["health_payload"])
            return
        if self._cfg.get("health_status_code", 200) != 200:
            self._send(self._cfg["health_status_code"], {"error": "nope"})
            return
        if self._cfg.get("health_non_json"):
            body = b"<html>not an mcp server</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(200, {
            "status": self._cfg.get("status", "ok"),
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocolVersion": PROTOCOL_VERSION,
            "tools_count": len(self._cfg["tools"]),
        })

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "bad json"})
            return

        method, rid = req.get("method"), req.get("id")
        self.server.calls.append(req)

        if rid is None and str(method).startswith("notifications/"):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Drive Blender over MCP.",
            }
        elif method == "tools/list":
            result = {"tools": self._cfg["tools"]}
        elif method == "ping":
            result = {}
        elif method == "tools/call":
            params = req.get("params", {}) or {}
            name = params.get("name")
            known = {t["name"] for t in self._cfg["tools"]}
            if name not in known:
                self._send(200, {"jsonrpc": "2.0", "id": rid,
                                 "error": {"code": -32602, "message": f"unknown tool: {name}"}})
                return
            if name in self._cfg.get("failing_tools", ()):
                self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"{name} blew up"}], "isError": True}})
                return
            payload = self._cfg.get("results", {}).get(
                name, {"tool": name, "arguments": params.get("arguments", {})})
            result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
        else:
            self._send(200, {"jsonrpc": "2.0", "id": rid,
                             "error": {"code": -32601, "message": f"method not found: {method}"}})
            return

        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result})


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeMCPServer:
    """Context manager yielding a live server on an ephemeral port."""

    def __init__(self, tools=None, status="ok", results=None, failing_tools=(),
                 health_payload=None, health_status_code=200, health_non_json=False):
        self.config = {
            "tools": tools if tools is not None else list(DEFAULT_TOOLS),
            "status": status,
            "results": results or {},
            "failing_tools": set(failing_tools),
            "health_payload": health_payload,
            "health_status_code": health_status_code,
            "health_non_json": health_non_json,
        }
        self._server = None
        self._thread = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def calls(self) -> list:
        return self._server.calls

    def methods(self) -> list[str]:
        return [c.get("method") for c in self.calls]

    def start(self) -> FakeMCPServer:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.config = self.config
        self._server.calls = []
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def free_port() -> int:
    """A port nothing is listening on — for the red-light case."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
