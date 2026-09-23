from __future__ import annotations

import asyncio


class KitchenEventBroker:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def publish(self, event: dict) -> None:
        for queue in tuple(self._subscribers):
            queue.put_nowait(event)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
