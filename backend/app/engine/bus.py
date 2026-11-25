from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

# Sent to a subscriber that fell behind so its socket closes instead of going quiet.
CLOSE: Any = object()


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[Any]] = set()

    def subscribe(self) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        self._subs.discard(q)

    async def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        message = json.dumps({"event": event, "payload": payload or {}}, default=str)
        for q in list(self._subs):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                self._subs.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(CLOSE)

    async def stream(self) -> AsyncIterator[Any]:
        q = self.subscribe()
        try:
            while True:
                item = await q.get()
                yield item
                if item is CLOSE:
                    return
        finally:
            self.unsubscribe(q)


bus = EventBus()
