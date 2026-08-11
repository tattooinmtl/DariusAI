"""In-process asyncio pub/sub — lets the brain/agent layer announce "a node
was created/edited/used" without knowing who's listening. The viz server's
websocket subscribes to this to drive the live pulse animation. Keeps a
small ring buffer so a browser tab opened mid-session (the normal case) can
replay recent history instead of sitting on a stream that's blind to
everything that already happened.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any


class ActivityBus:
    def __init__(self, history_limit: int = 50):
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        stamped = {"time": time.time(), **event}
        self._history.append(stamped)
        for q in list(self._subscribers):
            q.put_nowait(stamped)
        return stamped

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def recent(self) -> list[dict[str, Any]]:
        return list(self._history)

    def clear(self) -> None:
        """Wipe replay history — mainly for test isolation, since `bus` is a
        process-wide singleton shared by every BrainStore/app in the process."""
        self._history.clear()


# One process-wide bus, same pattern as omni's activity-bus.mjs — everything
# in this process (brain writes, agent tool calls) publishes to the same
# instance; the viz server is just one of potentially several subscribers.
bus = ActivityBus()
