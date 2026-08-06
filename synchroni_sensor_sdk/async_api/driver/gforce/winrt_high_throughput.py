"""Windows WinRT high-throughput connection parameters for Bleak."""

from __future__ import annotations

import logging
import platform
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

_REQUEST_ATTR_CANDIDATES = (
    "request_preferred_connection_parameters",
    "RequestPreferredConnectionParameters",
)


def _set_high_throughput(backend: Any) -> bool:
    if platform.system() != "Windows":
        return False

    requester = getattr(backend, "_requester", None)
    if requester is None:
        return False

    try:
        from winrt.windows.devices.bluetooth import (  # type: ignore[import-not-found]
            BluetoothLEPreferredConnectionParameters,
        )
    except Exception as e:
        logger.debug("winrt BluetoothLEPreferredConnectionParameters not available: %s", e)
        return False

    params = None
    for attr in ("throughput_optimized", "ThroughputOptimized"):
        try:
            params = getattr(BluetoothLEPreferredConnectionParameters, attr)()
            break
        except Exception:
            continue

    if params is None:
        logger.debug("BluetoothLEPreferredConnectionParameters.throughput_optimized not found")
        return False

    request_fn = None
    for attr in _REQUEST_ATTR_CANDIDATES:
        request_fn = getattr(requester, attr, None)
        if request_fn is not None:
            break

    if request_fn is None:
        logger.debug("BluetoothLEDevice.request_preferred_connection_parameters not found")
        return False

    try:
        backend._high_throughput_request = request_fn(params)
        logger.debug("Requested WinRT high-throughput connection parameters")
        return True
    except Exception as e:
        logger.debug("Failed to request high-throughput connection parameters: %s", e)
        return False


def _try_patch_connect() -> None:
    if platform.system() != "Windows":
        return

    try:
        from bleak.backends.winrt.client import BleakClientWinRT
    except Exception as e:
        logger.debug("bleak WinRT backend not available: %s", e)
        return

    if getattr(BleakClientWinRT, "_high_throughput_patched", False):
        return

    orig_connect = BleakClientWinRT.connect

    @wraps(orig_connect)
    async def patched_connect(self: Any, *args: Any, **kwargs: Any) -> None:
        await orig_connect(self, *args, **kwargs)
        _set_high_throughput(self)

    BleakClientWinRT.connect = patched_connect  # type: ignore[method-assign]
    BleakClientWinRT._high_throughput_patched = True  # type: ignore[attr-defined]
    logger.debug("Applied bleak WinRT high-throughput patch")


def apply() -> None:
    """Apply WinRT high-throughput patch on Windows; no-op elsewhere."""
    try:
        _try_patch_connect()
    except Exception as e:
        logger.debug("Failed to apply WinRT high-throughput patch: %s", e)
