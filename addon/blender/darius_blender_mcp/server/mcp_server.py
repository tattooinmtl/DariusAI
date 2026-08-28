"""HTTP transport + Blender main-thread bridge.

The MCP server runs on a daemon thread inside Blender so the UI keeps
responding, but `bpy` is not thread-safe: touching it from the HTTP thread
corrupts state or crashes Blender outright. Every tool call is therefore
marshalled onto the main thread through `bpy.app.timers` and waited on.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Callable

import bpy

from .protocol import (
    INTERNAL_ERROR,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    ToolDefinition,
    ToolRegistry,
    dispatch,
    error,
    health_payload,
)

registry = ToolRegistry()

MAIN_THREAD_TIMEOUT = 120.0  # renders and heavy builds are legitimately slow

# Background Blender (`blender --background`) runs no event loop, so
# `bpy.app.timers` never fire and a timer-based hop would hang forever.
# In that mode work goes on this queue instead and `pump()` — called from
# the main thread by `serve_background()` — drains it.
_background_queue: "queue.Queue[tuple]" = queue.Queue()


class MainThreadTimeout(RuntimeError):
    pass


def _settle(box: dict[str, Any], done: threading.Event,
            func: Callable[..., Any], args: tuple, kwargs: dict) -> None:
    try:
        box["value"] = func(*args, **kwargs)
    except BaseException as exc:  # carried back to the caller thread
        box["error"] = exc
    finally:
        done.set()


def run_on_main(func: Callable[..., Any], *args: Any,
                timeout: float = MAIN_THREAD_TIMEOUT, **kwargs: Any) -> Any:
    """Run `func` on Blender's main thread and return its result here.

    `bpy` is not thread-safe, and the HTTP server runs on a daemon thread,
    so nothing may touch Blender directly from a request. With a UI the
    hop goes through `bpy.app.timers`, which fires inside Blender's own
    event loop; headless it goes through the pump.
    """
    # Already on the main thread — run it here. Scheduling would block the
    # very thread that has to service the schedule: in background mode the
    # pump is what called us, and with a UI we would be inside a timer
    # callback waiting for the next timer to fire. Either way, deadlock.
    if threading.current_thread() is threading.main_thread():
        return func(*args, **kwargs)

    done = threading.Event()
    box: dict[str, Any] = {"value": None, "error": None}

    if bpy.app.background:
        _background_queue.put((func, args, kwargs, box, done))
    else:
        def _invoke():
            _settle(box, done, func, args, kwargs)
            return None  # returning None unregisters the timer

        try:
            bpy.app.timers.register(_invoke)
        except Exception as exc:
            raise RuntimeError(f"could not schedule work on Blender's main thread: {exc}") from exc

    if not done.wait(timeout=timeout):
        raise MainThreadTimeout(
            f"{getattr(func, '__name__', 'task')} did not finish within {timeout:.0f}s — "
            "Blender may be busy in a modal operator."
        )
    if box["error"] is not None:
        raise box["error"]
    return box["value"]


def pump(block_for: float = 0.1) -> int:
    """Run queued work. Must be called from Blender's main thread."""
    handled = 0
    deadline = time.monotonic() + max(0.0, block_for)
    while True:
        remaining = deadline - time.monotonic()
        try:
            func, args, kwargs, box, done = _background_queue.get(
                timeout=remaining if remaining > 0 else 0)
        except queue.Empty:
            return handled
        _settle(box, done, func, args, kwargs)
        handled += 1


def serve_background(host: str = "127.0.0.1", port: int = 8765, seconds: float = 0.0,
                     auth_token: str = "") -> None:
    """Serve from a headless Blender until `seconds` elapse (0 = forever).

    This is what makes batch asset generation possible — and it is how the
    bridge is tested end-to-end without a GUI.
    """
    server = get_server()
    server.start(host=host, port=port, auth_token=auth_token)
    print(f"[{SERVER_NAME}] headless on {server.endpoint}", flush=True)
    deadline = (time.monotonic() + seconds) if seconds else None
    try:
        while deadline is None or time.monotonic() < deadline:
            pump(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


def _invoke_tool(tool: ToolDefinition, arguments: dict[str, Any]) -> Any:
    """Call the handler on the request thread and let *it* decide whether
    it needs Blender's main thread. Every handler that touches bpy already
    wraps its work in run_on_main; marshalling here as well would hop
    twice and hang, and would also drag pure tools like
    `game3d_list_archetypes` through Blender for no reason."""
    return tool.handler(**arguments)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    auth_token = ""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"

    def log_message(self, *args):
        """Blender's console is the user's console — stay out of it."""

    # -- helpers ----------------------------------------------------------
    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        token = getattr(self.server, "auth_token", "")
        if not token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {token}"

    # -- verbs ------------------------------------------------------------
    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        """Health probe. Darius polls this for the status light, so it must
        stay cheap and must never touch bpy — it is called from a thread
        that has no business in Blender's data."""
        self._send(200, health_payload(registry))

    def do_POST(self):
        if not self._authorised():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            request = json.loads(raw or b"{}")
        except (ValueError, OSError):
            self._send(400, error(None, PARSE_ERROR, "invalid JSON"))
            return

        try:
            if isinstance(request, list):  # JSON-RPC batch
                responses = [r for r in
                             (dispatch(item, registry, _invoke_tool) for item in request)
                             if r is not None]
                self._send(200, responses if responses else {"ok": True})
                return
            response = dispatch(request, registry, _invoke_tool)
        except Exception as exc:
            traceback.print_exc()
            self._send(200, error(request.get("id") if isinstance(request, dict) else None,
                                  INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
            return

        if response is None:
            # A notification. 202 with no body is what the spec expects.
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, response)


class DariusMCPServer:
    """Lifecycle for the HTTP server. One per Blender session."""

    def __init__(self) -> None:
        self._httpd: _ThreadedHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.host = "127.0.0.1"
        self.port = 8765

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    def start(self, host: str = "127.0.0.1", port: int = 8765, auth_token: str = "") -> None:
        if self._httpd is not None:
            raise RuntimeError(f"already listening on {self.endpoint}")
        httpd = _ThreadedHTTPServer((host, port), _Handler)
        httpd.auth_token = auth_token
        self._httpd = httpd
        self.host, self.port = host, port
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="darius-mcp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
            self._thread = None


_server = DariusMCPServer()


def get_server() -> DariusMCPServer:
    return _server


__all__ = (
    "registry",
    "run_on_main",
    "pump",
    "serve_background",
    "get_server",
    "DariusMCPServer",
    "MainThreadTimeout",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
)
