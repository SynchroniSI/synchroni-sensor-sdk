from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator
from typing import Generic, TypeVar

T = TypeVar("T")

DEFAULT_DATA_BUFFER_MAXSIZE = 64


class DropOldestBuffer(Generic[T]):
    """
    Bounded single-loop buffer that drops the oldest item when full.

    Not thread-safe. All :meth:`publish` / :meth:`consume` calls must run on
    the same asyncio event loop. When a producer runs on another thread (e.g.
    a BLE callback), the driver must marshal onto the loop with
    :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.schedule_publish_data`,
    which uses ``call_soon_threadsafe`` to invoke :meth:`publish_on_loop` on
    the loop thread.
    """

    def __init__(self, maxsize: int = DEFAULT_DATA_BUFFER_MAXSIZE) -> None:
        self._logger = logging.getLogger(__name__)
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._buffer: deque[T] = deque(maxlen=maxsize)
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()
        self._closed = False
        self.dropped = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def qsize(self) -> int:
        return len(self._buffer)

    @property
    def closed(self) -> bool:
        return self._closed

    async def publish(self, item: T) -> None:
        """Enqueue from a coroutine on the buffer's event loop."""
        async with self._lock:
            self._publish_unlocked(item)
        self._event.set()

    def publish_on_loop(self, item: T) -> None:
        """
        Enqueue synchronously on the event loop thread.

        Intended as the target of ``loop.call_soon_threadsafe`` from
        :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.schedule_publish_data`.
        Do not call from other threads directly.
        """
        if self._closed:
            return
        if len(self._buffer) == self._maxsize:
            self.dropped += 1
        self._buffer.append(item)
        self._event.set()

    def _publish_unlocked(self, item: T) -> None:
        if self._closed:
            return
        if len(self._buffer) == self._maxsize:
            self.dropped += 1
        self._buffer.append(item)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
        self._event.set()

    async def consume(self) -> AsyncGenerator[T, None]:
        while True:
            item: T | None = None
            async with self._lock:
                if self._buffer:
                    item = self._buffer.popleft()
                    if not self._buffer:
                        self._event.clear()
                elif self._closed:
                    return
            if item is not None:
                yield item
                continue
            await self._event.wait()
