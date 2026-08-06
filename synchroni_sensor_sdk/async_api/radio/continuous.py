"""Shared continuous-scan state for RadioAdapter implementations."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable

from synchroni_sensor_sdk.async_api.radio.base import RadioScanHit


class ContinuousScanMixin:
    """Threading-guarded continuous scan loop shared by system + managed radios."""

    def __init__(self) -> None:
        self._scan_lock = threading.Lock()
        self._is_scanning = False
        self._scan_task: asyncio.Task[None] | None = None
        self._scan_results_callback: Callable[[list[RadioScanHit]], None] | None = None

    def _set_scan_results_callback(
        self,
        callback: Callable[[list[RadioScanHit]], None] | None,
    ) -> None:
        """Wire continuous-scan result delivery (hub internal; not app-facing)."""
        self._scan_results_callback = callback

    def stop_scan(self) -> None:
        with self._scan_lock:
            self._is_scanning = False
            if self._scan_task is not None:
                self._scan_task.cancel()
                self._scan_task = None

    def is_scanning(self) -> bool:
        with self._scan_lock:
            return self._is_scanning

    async def _start_continuous(
        self,
        period_ms: int,
        scan_once: Callable[[int], Awaitable[list[RadioScanHit]]],
    ) -> None:
        with self._scan_lock:
            if self._is_scanning:
                return
            self._is_scanning = True
            self._scan_task = asyncio.create_task(self._continuous_impl(period_ms, scan_once))

    async def _continuous_impl(
        self,
        period_ms: int,
        scan_once: Callable[[int], Awaitable[list[RadioScanHit]]],
    ) -> None:
        try:
            while True:
                with self._scan_lock:
                    if not self._is_scanning:
                        break
                hits = await scan_once(period_ms)
                cb = self._scan_results_callback
                if cb is not None and hits:
                    cb(hits)
        except asyncio.CancelledError:
            pass
        finally:
            with self._scan_lock:
                self._is_scanning = False
                self._scan_task = None
