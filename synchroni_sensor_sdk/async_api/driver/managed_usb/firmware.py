"""Optional host firmware pins for known USB Bluetooth dongle models.

Some HCI dongles require a host-provided firmware file before the controller is
usable. The SDK does **not** download firmware from the network. When a VID/PID
is mapped, the file is resolved under a resource directory and verified by
size + SHA-256 before use.

Unset / unmapped models return ``None`` (no pin).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from synchroni_sensor_sdk.core.bluetooth import BluetoothAdapter
from synchroni_sensor_sdk.core.exceptions import AdapterFirmwareError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FirmwarePin:
    """Immutable pin for a dongle model firmware blob."""

    vendor_id: str
    product_id: str
    filename: str
    size_bytes: int
    sha256: str


# Empty by default: distributors populate via firmware_resource_dir + pins list.
# Known models can be registered here when shipping opaque resources with verified digests.
KNOWN_FIRMWARE_PINS: tuple[FirmwarePin, ...] = ()


def pins_for(vendor_id: str | None, product_id: str | None) -> FirmwarePin | None:
    """Return the pin for a VID/PID pair, or None if unmapped."""
    if vendor_id is None or product_id is None:
        return None
    v = vendor_id.lower()
    p = product_id.lower()
    for pin in KNOWN_FIRMWARE_PINS:
        if pin.vendor_id == v and pin.product_id == p:
            return pin
    return None


def default_firmware_resource_dir() -> Path:
    """Package data directory for optional firmware blobs."""
    return Path(__file__).resolve().parents[3] / "resources" / "firmware"


def clear_firmware_resource_dir(*, resource_dir: Path | str | None = None) -> Path:
    """Remove firmware files under the package resource directory (keep the folder).

    Deletes every file under the pin resource root except dotfiles such as
    ``.gitkeep``. Does not remove the directory itself so git placeholders remain
    usable.

    Returns
    -------
    Path
        Resource directory that was cleaned.
    """
    base = Path(resource_dir) if resource_dir is not None else default_firmware_resource_dir()
    if not base.is_dir():
        return base
    removed = 0
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        path.unlink(missing_ok=True)
        removed += 1
    if removed:
        logger.info("Cleared %s firmware file(s) under %s", removed, base)
    return base


def ensure_firmware(
    adapter: BluetoothAdapter,
    *,
    resource_dir: Path | str | None = None,
) -> Path | None:
    """Verify and return the firmware path for *adapter*, if mapped.

    Returns
    -------
    Path | None
        Absolute path to verified firmware, or None when no pin applies.

    Raises
    ------
    AdapterFirmwareError
        Mapped pin but file missing or digest mismatch.
    """
    pin = pins_for(adapter.vendor_id, adapter.product_id)
    if pin is None:
        return None

    base = Path(resource_dir) if resource_dir is not None else default_firmware_resource_dir()
    path = base / pin.filename
    if not path.is_file():
        raise AdapterFirmwareError(
            f"Mapped firmware for VID:{pin.vendor_id} PID:{pin.product_id} "
            f"not found at {path}. Place the official blob or set firmware_resource_dir."
        )

    data = path.read_bytes()
    if len(data) != pin.size_bytes:
        raise AdapterFirmwareError(f"Firmware size mismatch for {path}: expected {pin.size_bytes}, got {len(data)}")
    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != pin.sha256.lower():
        raise AdapterFirmwareError(f"Firmware SHA-256 mismatch for {path}: expected {pin.sha256}, got {digest}")
    logger.debug("Verified firmware pin %s for adapter %s", path, adapter.id)
    return path.resolve()
