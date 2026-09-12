"""Bounded handoff from notification threads to one parser event loop."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from threading import Lock
from typing import Generic, TypeVar

T = TypeVar("T")
RAW_NOTIFICATION_QUEUE_CAP = 4_096
RAW_NOTIFICATION_DRAIN_BATCH = 64
MAX_NOTIFICATION_BYTES = 4_096


class BoundedThreadIngress(Generic[T]):
    """One scheduled drain, finite staging, no blocking queue put in a callback.

    Overflow invalidates scientific delivery through ``on_fault`` exactly once
    until an explicit reset. Existing accepted items retain their order. Keep
    draining afterward so a universal notification stream can still carry stop
    command responses; faulted data must not be treated as a healthy capture.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        target: asyncio.Queue[T],
        on_fault: Callable[[str], None],
        *,
        capacity: int = RAW_NOTIFICATION_QUEUE_CAP,
    ) -> None:
        if capacity < 1:
            raise ValueError("Ingress capacity must be positive")
        self._loop = loop
        self._target = target
        self._on_fault = on_fault
        self._capacity = capacity
        self._pending: deque[T] = deque()
        self._lock = Lock()
        self._scheduled = False
        self._fault: str | None = None
        self._fault_reported = False
        self.rejected = 0

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def reset(self) -> None:
        """Called on the loop at an explicit stream start, before enabling data."""
        with self._lock:
            self._pending.clear()
            self._fault = None
            self._fault_reported = False
            self.rejected = 0

    def publish(self, item: T) -> None:
        with self._lock:
            if len(self._pending) >= self._capacity:
                self.rejected += 1
                if self._fault is None:
                    self._fault = f"stage=raw_callback_staging|capacity={self._capacity}"
            else:
                self._pending.append(item)
            if self._scheduled:
                return
            self._scheduled = True
        self._loop.call_soon_threadsafe(self._drain)

    def reject(self, reason: str) -> None:
        """Surface invalid-size input through the same single scheduled fault lane."""
        with self._lock:
            self.rejected += 1
            if self._fault is None:
                self._fault = reason
            if self._scheduled:
                return
            self._scheduled = True
        self._loop.call_soon_threadsafe(self._drain)

    def _drain(self) -> None:
        for _ in range(RAW_NOTIFICATION_DRAIN_BATCH):
            with self._lock:
                if not self._pending:
                    break
                item = self._pending.popleft()
            try:
                self._target.put_nowait(item)
            except asyncio.QueueFull:
                with self._lock:
                    self.rejected += 1
                    if self._fault is None:
                        self._fault = f"stage=raw_parser_queue|capacity={self._target.maxsize}"
        with self._lock:
            report = self._fault if not self._fault_reported else None
            if report is not None:
                self._fault_reported = True
                report += f"|rejected_notifications={self.rejected}"
            again = bool(self._pending)
            if not again:
                self._scheduled = False
        if report is not None:
            self._on_fault(report)
        if again:
            self._loop.call_soon(self._drain)
