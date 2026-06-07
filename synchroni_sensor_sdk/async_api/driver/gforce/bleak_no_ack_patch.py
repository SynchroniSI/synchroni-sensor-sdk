"""Force write-without-response on Bleak command characteristics.

On some Windows hosts, write-with-response stalls on system ACK. Forcing no-ack
on known command UUIDs improves command-channel reliability. Enabled when
``SENSOR_SDK_FORCE_NO_ACK=1``.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CMD_CHAR_UUIDS = {
    "f000ffe1-0451-4000-b000-000000000000",  # OYM CMD
    "00000002-0000-1000-8000-00805f9b34fb",  # RFSTAR CMD
}


def _char_uuid(char_specifier: Any) -> str:
    if isinstance(char_specifier, str):
        return char_specifier.lower()
    try:
        return str(char_specifier.uuid).lower()
    except Exception:
        return ""


def apply(cmd_char_uuids: Collection[str] | None = DEFAULT_CMD_CHAR_UUIDS) -> None:
    """Patch ``BleakClient.write_gatt_char`` to force ``response=False`` for targets."""
    try:
        from bleak import BleakClient
    except Exception as e:
        logger.debug("bleak not available, skip no-ack patch: %s", e)
        return

    if getattr(BleakClient, "_no_ack_patched", False):
        return

    orig_write = BleakClient.write_gatt_char
    targets = None if cmd_char_uuids is None else {u.lower() for u in cmd_char_uuids}

    @wraps(orig_write)
    async def patched_write(self: Any, char_specifier: Any, data: Any, response: bool | None = None) -> Any:
        if targets is None or _char_uuid(char_specifier) in targets:
            response = False
        return await orig_write(self, char_specifier, data, response=response)

    BleakClient.write_gatt_char = patched_write  # type: ignore[method-assign]
    BleakClient._orig_write_gatt_char = orig_write  # type: ignore[attr-defined]
    BleakClient._no_ack_patched = True  # type: ignore[attr-defined]
    logger.debug(
        "Applied bleak write-without-response patch (targets: %s)",
        "all chars" if targets is None else targets,
    )


def reset() -> None:
    """Remove the write patch (tests)."""
    try:
        from bleak import BleakClient
    except Exception:
        return

    if not getattr(BleakClient, "_no_ack_patched", False):
        return

    orig_write = getattr(BleakClient, "_orig_write_gatt_char", None)
    if orig_write is not None:
        BleakClient.write_gatt_char = orig_write  # type: ignore[method-assign]
        del BleakClient._orig_write_gatt_char  # type: ignore[attr-defined]
    BleakClient._no_ack_patched = False  # type: ignore[attr-defined]
