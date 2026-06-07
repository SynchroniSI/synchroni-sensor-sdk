from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from synchroni_sensor_sdk.async_api.driver.managed_usb.usb_common import (
    MANAGED_USB_ADAPTER_ID_PREFIX,
    first_string,
    list_libusb_managed_usb_adapters,
    looks_like_managed_usb_bluetooth_adapter,
    managed_usb_transport_name,
    normalize_hex_id,
    normalize_identity_token,
    run_command_json,
    usb_display_name,
    walk_dict_nodes,
)
from synchroni_sensor_sdk.core.bluetooth import BluetoothAdapter


@dataclass(frozen=True)
class MacOsSystemUsbCandidate:
    name: str
    vendor_id: str | None
    product_id: str | None
    serial_number: str | None
    location_id: str | None


async def list_macos_managed_usb_adapters(command_timeout_s: float) -> list[BluetoothAdapter]:
    """List macOS dedicated dongles via libusb, with system_profiler as a backup.

    Prefer libusb rows (usable ``usb_transport`` strings) but always merge
    system_profiler candidates so a sticky libusb claim cannot make a still-
    plugged dongle vanish from inventory after a failed/hung transport close.
    """
    now = datetime.now(UTC)
    libusb_adapters = list_macos_libusb_adapters(now)
    system_adapters = await list_macos_system_usb_adapters(now, command_timeout_s)
    return merge_macos_adapters(libusb_adapters, system_adapters)


def merge_macos_adapters(
    libusb_adapters: list[BluetoothAdapter],
    system_adapters: list[BluetoothAdapter],
) -> list[BluetoothAdapter]:
    """Prefer libusb identities; keep system_profiler rows not already covered."""
    if not libusb_adapters:
        return system_adapters
    if not system_adapters:
        return libusb_adapters

    merged: dict[str, BluetoothAdapter] = {}
    for adapter in system_adapters:
        merged[_macos_adapter_merge_key(adapter)] = adapter
    for adapter in libusb_adapters:
        # libusb wins for a given VID/PID(+serial) — it owns a workable transport.
        merged[_macos_adapter_merge_key(adapter)] = adapter
    return list(merged.values())


def _macos_adapter_merge_key(adapter: BluetoothAdapter) -> str:
    vendor = (adapter.vendor_id or "unknown").lower()
    product = (adapter.product_id or "unknown").lower()
    serial = (adapter.serial_number or "").strip().lower()
    if serial:
        return f"{vendor}:{product}:{serial}"
    # Fall back to adapter id so two non-serial units of the same model still both show.
    return f"{vendor}:{product}:{adapter.id.lower()}"


def list_macos_libusb_adapters(now: datetime) -> list[BluetoothAdapter]:
    return list_libusb_managed_usb_adapters(now, platform_name="macos")


async def list_macos_system_usb_adapters(now: datetime, command_timeout_s: float) -> list[BluetoothAdapter]:
    raw = await run_command_json(
        ["system_profiler", "SPUSBDataType", "-json"],
        timeout_s=command_timeout_s,
    )
    if raw is None:
        raw = await run_command_json(
            ["system_profiler", "SPUSBHostDataType", "-json"],
            timeout_s=command_timeout_s,
        )
    if raw is None:
        return []

    candidates: list[MacOsSystemUsbCandidate] = []
    for node in walk_dict_nodes(raw):
        name = first_string(node, ["_name", "name", "device_name", "product_name"])
        if name is None:
            continue

        vendor = normalize_hex_id(first_string(node, ["vendor_id", "vendor_id_hex", "vendor_id_num"]))
        product = normalize_hex_id(first_string(node, ["product_id", "product_id_hex", "product_id_num"]))
        manufacturer = first_string(node, ["manufacturer", "manufacturer_name"])
        searchable = " ".join(value for value in [name, manufacturer, vendor, product] if value is not None)
        if not looks_like_managed_usb_bluetooth_adapter(searchable, vendor, product):
            continue

        candidates.append(
            MacOsSystemUsbCandidate(
                name=usb_display_name(name, manufacturer),
                vendor_id=vendor,
                product_id=product,
                serial_number=first_string(node, ["serial_num", "serial_number"]),
                location_id=first_string(node, ["location_id", "location"]),
            )
        )

    serial_counts = Counter(
        (candidate.vendor_id, candidate.product_id, candidate.serial_number)
        for candidate in candidates
        if candidate.serial_number is not None
    )

    vid_pid_indexes: dict[tuple[str | None, str | None], int] = {}
    adapters: list[BluetoothAdapter] = []
    for candidate in candidates:
        vid_pid_key = (candidate.vendor_id, candidate.product_id)
        device_index = vid_pid_indexes.get(vid_pid_key, 0)
        vid_pid_indexes[vid_pid_key] = device_index + 1
        serial_key = (
            candidate.vendor_id,
            candidate.product_id,
            candidate.serial_number or "",
        )
        serial_is_unique = candidate.serial_number is not None and serial_counts.get(serial_key, 0) == 1

        identity = macos_system_profiler_identity(candidate, device_index, serial_is_unique)
        usb_transport = managed_usb_transport_name(
            candidate.vendor_id,
            candidate.product_id,
            candidate.serial_number if serial_is_unique else None,
            device_index=None if serial_is_unique else device_index,
        )

        adapters.append(
            BluetoothAdapter(
                id=f"{MANAGED_USB_ADAPTER_ID_PREFIX}macos:{identity}",
                name=candidate.name,
                source="managed_usb",
                platform="macos",
                transport="libusb",
                vendor_id=candidate.vendor_id,
                product_id=candidate.product_id,
                serial_number=candidate.serial_number,
                usb_transport=usb_transport,
                is_external=True,
                last_seen_at=now,
            )
        )

    return adapters


def macos_system_profiler_identity(
    candidate: MacOsSystemUsbCandidate,
    device_index: int,
    serial_is_unique: bool,
) -> str:
    vendor_id = candidate.vendor_id or "unknown"
    product_id = candidate.product_id or "unknown"
    if serial_is_unique and candidate.serial_number is not None:
        return f"{vendor_id}:{product_id}:{normalize_identity_token(candidate.serial_number)}"
    if candidate.location_id is not None:
        return f"{vendor_id}:{product_id}:{normalize_identity_token(candidate.location_id)}"
    return f"{vendor_id}:{product_id}:{device_index}"
