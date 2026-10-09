"""Open permission requests, answerable from anywhere.

A tool that needs the user's OK (request_external_read) blocks its thread on
a request. The chat window shows a modal for it, and since 0.98 the tray
lists it too, with a notification — so a request made while the window is
minimised is no longer a five-minute silent wait. Whichever answers first
wins; the others are told it is settled.

One center per app (app.state.permissions). Thread-safe: requests are added
from tool threads, answered from the HTTP thread pool or the chat socket.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class PermissionCenter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}

    def add(self, req_id: str, path: str, reason: str,
            resolver: Callable[[bool], None], on_settled: Callable[[str], None] | None = None) -> None:
        """`resolver(allow)` unblocks the waiting tool; `on_settled(req_id)`
        tells the requesting window to close its modal."""
        with self._lock:
            self._items[req_id] = {
                "id": req_id, "path": path, "reason": reason, "created_at": time.time(),
                "_resolve": resolver, "_settled": on_settled,
            }

    def resolve(self, req_id: str, allow: bool) -> bool:
        """Answer a request. False if it was already answered or expired."""
        with self._lock:
            item = self._items.pop(req_id, None)
        if item is None:
            return False
        try:
            item["_resolve"](bool(allow))
        finally:
            if item.get("_settled"):
                try:
                    item["_settled"](req_id)
                except Exception:
                    pass
        return True

    def discard(self, req_id: str) -> None:
        """Drop a request that timed out or whose window went away."""
        with self._lock:
            self._items.pop(req_id, None)

    def active(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items.values())
        return [{k: v for k, v in it.items() if not k.startswith("_")}
                for it in sorted(items, key=lambda i: i["created_at"])]
