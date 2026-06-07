"""Background event loop used exclusively by the synchronous API."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class EventLoopRunner:
    """
    Owns a dedicated asyncio event loop on a background daemon thread.

    The async API has no knowledge of this type — sync wrappers schedule
    coroutines onto this loop via :meth:`run`.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("Event loop runner has not been started")
        return self._loop

    @property
    def is_running(self) -> bool:
        return self._loop is not None and self._loop.is_running()

    def start(self) -> None:
        if self.is_running:
            return

        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, name="synchroni-sensor-sdk", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Timed out waiting for event loop to start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, T], *, timeout: float | None = None) -> T:
        """
        Schedule *coro* on the managed loop and block until it completes.

        Uses ``asyncio.run_coroutine_threadsafe`` (not ``call_soon_threadsafe``).
        Driver BLE ingress uses ``call_soon_threadsafe`` separately to publish
        parsed packets onto the same loop from platform threads.
        """
        loop = self.loop
        try:
            if asyncio.get_running_loop() is loop:
                raise RuntimeError("run() cannot be called from the managed event loop thread; use await instead")
        except RuntimeError as exc:
            if "no running event loop" not in str(exc).lower():
                raise

        task: Coroutine[Any, Any, T] = asyncio.wait_for(coro, timeout) if timeout is not None else coro
        future = asyncio.run_coroutine_threadsafe(task, loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        """Stop the background loop from the caller's thread."""
        if not self.is_running or self._loop is None:
            return

        loop = self._loop
        loop.call_soon_threadsafe(loop.stop)  # loop.stop must run on the loop thread
        if self._thread is not None:
            self._thread.join(timeout=5)
        loop.close()
        self._loop = None
        self._thread = None

    def __enter__(self) -> EventLoopRunner:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
