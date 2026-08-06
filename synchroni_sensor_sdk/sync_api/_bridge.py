"""Helpers for wrapping async API objects with blocking calls."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from synchroni_sensor_sdk.sync_api.runtime import EventLoopRunner

T = TypeVar("T")


class SyncBridge:
    """
    Base class for synchronous wrappers.

    Subclasses hold a reference to the async implementation and delegate
    coroutines through the hub's :class:`EventLoopRunner`.
    """

    def __init__(self, runner: EventLoopRunner) -> None:
        self._logger = logging.getLogger(__name__)
        self._runner = runner

    def _run(self, coro: Coroutine[Any, Any, T], *, timeout: float | None = None) -> T:
        return self._runner.run(coro, timeout=timeout)

    def _sync_method(self, async_fn: Callable[..., Coroutine[Any, Any, T]], *args: object, **kwargs: object) -> T:
        return self._run(async_fn(*args, **kwargs))
