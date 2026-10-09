"""In-process asyncio pub/sub — lets the brain/agent layer announce "a node
was created/edited/used" without knowing who's listening. The viz server's
websocket subscribes to this to drive the live pulse animation. Keeps a
small ring buffer so a browser tab opened mid-session (the normal case) can
replay recent history instead of sitting on a stream that's blind to
everything that already happened.

Publishers are not all on the event loop: a chat turn runs under
`asyncio.to_thread`, so every tool call publishes from a worker thread.
`asyncio.Queue` is not thread-safe and a bare `put_nowait` from another
thread never wakes the loop — events then sat until something else woke it
(uvicorn's 100 ms tick), which is why the brain lagged the agent. Each
subscriber therefore remembers its loop, and cross-thread publishes go
through `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any


class ActivityBus:
    def __init__(self, history_limit: int = 50):
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        stamped = {"time": time.time(), **event}
        with self._lock:
            self._history.append(stamped)
            subscribers = list(self._subscribers.items())
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for q, loop in subscribers:
            if loop is current:
                q.put_nowait(stamped)
                continue
            try:
                loop.call_soon_threadsafe(q.put_nowait, stamped)
            except RuntimeError:
                # The subscriber's loop is closed — its socket is gone.
                self.unsubscribe(q)
        return stamped

    def subscribe(self) -> asyncio.Queue:
        """Must be called from the event loop that will read the queue."""
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(q, None)

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def clear(self) -> None:
        """Wipe replay history — mainly for test isolation, since `bus` is a
        process-wide singleton shared by every BrainStore/app in the process."""
        with self._lock:
            self._history.clear()


# One process-wide bus, same pattern as omni's activity-bus.mjs — everything
# in this process (brain writes, agent tool calls) publishes to the same
# instance; the viz server is just one of potentially several subscribers.
bus = ActivityBus()
