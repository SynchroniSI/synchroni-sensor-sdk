from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from synchroni_sensor_sdk.async_api.driver.managed_usb.usb_common import (
    MANAGED_USB_ADAPTER_ID_PREFIX,
    USB_USERSPACE_DRIVER_RE,
    VID_PID_RE,
    first_string,
    is_known_usb_bluetooth_adapter,
    list_libusb_managed_usb_adapters,
    looks_like_managed_usb_bluetooth_adapter,
    managed_usb_transport_name,
    run_command,
    run_command_json,
    usb_display_name,
)
from synchroni_sensor_sdk.core.bluetooth import WINDOWS_CLAIM_ACTION_WINUSB, BluetoothAdapter

WINDOWS_SYSTEM_ADAPTER_ID_PREFIX = "system:windows:"
WINDOWS_PNPUTIL_CLASSES = ("Bluetooth", "USBDevice", "libusbK")
WINDOWS_MANAGED_USB_CLASSES = {"libusbk", "usbdevice"}
WINDOWS_OS_BLUETOOTH_SERVICES = {"bthenum", "bthmini", "bthport", "bthusb"}
SYNCHRONI_WINUSB_DRIVER_NAME = "synchroni eeg bluetooth dongle"


@dataclass(frozen=True)
class WindowsPnpCandidate:
    instance_id: str
    name: str
    status: str
    device_class: str
    service: str
    manufacturer: str | None
    driver_name: str | None
    vendor_id: str | None
    product_id: str | None


async def list_windows_managed_usb_adapters(command_timeout_s: float) -> list[BluetoothAdapter]:
    now = datetime.now(UTC)
    libusb_adapters_task = asyncio.create_task(list_windows_libusb_adapters(now))
    pnp_candidates = await list_windows_pnp_candidates(command_timeout_s)
    pnp_adapters = windows_adapters_from_pnp_candidates(pnp_candidates, now)
    libusb_adapters = await libusb_adapters_task

    return reconcile_windows_adapters(libusb_adapters, pnp_adapters)


async def list_windows_libusb_adapters(now: datetime) -> list[BluetoothAdapter]:
    return await asyncio.to_thread(
        list_libusb_managed_usb_adapters,
        now,
        platform_name="windows",
    )


async def list_windows_pnp_candidates(command_timeout_s: float) -> list[WindowsPnpCandidate]:
    rows_by_instance_id: dict[str, dict[str, object]] = {}

    for device_class in WINDOWS_PNPUTIL_CLASSES:
        output = await run_command(
            ["pnputil", "/enum-devices", "/connected", "/class", device_class],
            timeout_s=command_timeout_s,
        )
        if output is None:
            continue
        for row in parse_pnputil_device_rows(output):
            merge_windows_pnp_row(rows_by_instance_id, row)

    ps_script = (
        "$devices = Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.InstanceId -like 'USB\\VID_*' } | "
        "Select-Object InstanceId,FriendlyName,Status,Class,Service,Manufacturer; "
        "$devices | ConvertTo-Json -Compress"
    )
    raw = await run_command_json(
        ["powershell", "-NoProfile", "-Command", ps_script],
        timeout_s=command_timeout_s,
    )
    if isinstance(raw, dict):
        rows = [raw]
    elif isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict)]
    else:
        rows = []

    for row in rows:
        merge_windows_pnp_row(rows_by_instance_id, row)

    return [
        candidate
        for row in rows_by_instance_id.values()
        if (candidate := windows_pnp_candidate_from_row(row)) is not None
    ]


def parse_pnputil_device_rows(output: str) -> list[dict[str, object]]:
    field_names = {
        "instance id": "InstanceId",
        "device description": "FriendlyName",
        "friendly name": "FriendlyName",
        "class name": "Class",
        "service": "Service",
        "manufacturer name": "Manufacturer",
        "status": "Status",
        "driver name": "DriverName",
    }
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for line in output.splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        raw_key, raw_value = stripped.split(":", 1)
        key = field_names.get(raw_key.strip().lower())
        if key is None:
            continue

        value = raw_value.strip()
        if key == "InstanceId":
            if current is not None and current.get("InstanceId") is not None:
                rows.append(current)
            current = {"InstanceId": value}
            continue

        if current is not None and value != "":
            current[key] = value

    if current is not None and current.get("InstanceId") is not None:
        rows.append(current)
    return rows


def merge_windows_pnp_row(
    rows_by_instance_id: dict[str, dict[str, object]],
    row: dict[str, object],
) -> None:
    instance_id = first_string(row, ["InstanceId", "InstanceID", "DeviceID", "PNPDeviceID"])
    if instance_id is None:
        return

    key = instance_id.lower()
    existing = rows_by_instance_id.setdefault(key, {"InstanceId": instance_id})
    for field in ["FriendlyName", "Status", "Class", "Service", "Manufacturer", "DriverName"]:
        if existing.get(field) is None:
            value = first_string(row, [field])
            if value is not None:
                existing[field] = value


def windows_pnp_candidate_from_row(row: dict[str, object]) -> WindowsPnpCandidate | None:
    instance_id = first_string(row, ["InstanceId"])
    if instance_id is None:
        return None

    match = VID_PID_RE.search(instance_id)
    vendor_id = match.group(1).lower() if match is not None else None
    product_id = match.group(2).lower() if match is not None else None
    name = first_string(row, ["FriendlyName"]) or "USB Bluetooth Adapter"
    manufacturer = first_string(row, ["Manufacturer"])

    return WindowsPnpCandidate(
        instance_id=instance_id,
        name=usb_display_name(name, manufacturer),
        status=first_string(row, ["Status"]) or "",
        device_class=first_string(row, ["Class"]) or "",
        service=first_string(row, ["Service"]) or "",
        manufacturer=manufacturer,
        driver_name=first_string(row, ["DriverName"]),
        vendor_id=vendor_id,
        product_id=product_id,
    )


def windows_adapters_from_pnp_candidates(
    candidates: list[WindowsPnpCandidate],
    now: datetime,
) -> list[BluetoothAdapter]:
    managed_candidates = [candidate for candidate in candidates if windows_row_looks_like_managed_usb(candidate)]
    counts_by_vid_pid = Counter((candidate.vendor_id, candidate.product_id) for candidate in managed_candidates)
    indexes_by_vid_pid: dict[tuple[str | None, str | None], int] = {}
    adapters: list[BluetoothAdapter] = []

    for candidate in candidates:
        if windows_row_looks_like_managed_usb(candidate):
            vid_pid_key = (candidate.vendor_id, candidate.product_id)
            device_index = indexes_by_vid_pid.get(vid_pid_key, 0)
            indexes_by_vid_pid[vid_pid_key] = device_index + 1
            usb_transport = managed_usb_transport_name(
                candidate.vendor_id,
                candidate.product_id,
                None,
                device_index=device_index if counts_by_vid_pid[vid_pid_key] > 1 else None,
            )
            adapters.append(
                BluetoothAdapter(
                    id=f"{MANAGED_USB_ADAPTER_ID_PREFIX}windows:{candidate.instance_id.lower()}",
                    name=candidate.name,
                    source="managed_usb",
                    platform="windows",
                    transport=windows_usb_transport(
                        candidate.service,
                        candidate.device_class,
                        candidate.name,
                        candidate.driver_name,
                    ),
                    vendor_id=candidate.vendor_id,
                    product_id=candidate.product_id,
                    device_instance_id=candidate.instance_id,
                    usb_transport=usb_transport,
                    driver_name=candidate.driver_name or candidate.service or None,
                    is_external=True,
                    last_seen_at=now,
                )
            )
        elif is_known_usb_bluetooth_adapter(candidate.vendor_id, candidate.product_id):
            claim_required = windows_row_requires_claim(candidate)
            adapters.append(
                BluetoothAdapter(
                    id=f"{WINDOWS_SYSTEM_ADAPTER_ID_PREFIX}{candidate.instance_id.lower()}",
                    name=candidate.name,
                    source="system",
                    platform="windows",
                    transport="os",
                    vendor_id=candidate.vendor_id,
                    product_id=candidate.product_id,
                    device_instance_id=candidate.instance_id,
                    usb_transport=None,
                    driver_name=candidate.driver_name or candidate.service or None,
                    is_external=True,
                    claim_required=claim_required,
                    claim_action=WINDOWS_CLAIM_ACTION_WINUSB if claim_required else None,
                    claim_message=windows_claim_message(candidate) if claim_required else None,
                    last_seen_at=now,
                )
            )

    return adapters


def reconcile_windows_adapters(
    libusb_adapters: list[BluetoothAdapter],
    pnp_adapters: list[BluetoothAdapter],
) -> list[BluetoothAdapter]:
    by_vid_pid: dict[tuple[str, ...], dict[str, list[BluetoothAdapter]]] = {}
    for source_name, adapters in [("managed", libusb_adapters), ("pnp", pnp_adapters)]:
        for adapter in adapters:
            key = adapter_vid_pid_key(adapter)
            by_vid_pid.setdefault(key, {"managed": [], "pnp": []})[source_name].append(adapter)

    reconciled: list[BluetoothAdapter] = []
    for groups in by_vid_pid.values():
        managed = groups["managed"]
        pnp = groups["pnp"]
        if not pnp:
            reconciled.extend(dedupe_adapters_by_id(managed))
            continue
        if not managed:
            reconciled.extend(dedupe_adapters_by_id([*managed, *pnp]))
            continue

        pnp_managed = [adapter for adapter in pnp if adapter.source == "managed_usb"]
        pnp_system = [adapter for adapter in pnp if adapter.source == "system"]
        if pnp_managed:
            # Once a dongle is bound to a userspace driver, trust libusb for the identity and
            # transport name. That mirrors macOS and avoids PnP-order based usb:vid:pid#index drift.
            managed_candidates = dedupe_adapters_by_id(managed)
            if len(managed_candidates) < len(pnp_managed):
                managed_candidates = dedupe_adapters_by_id([*managed_candidates, *pnp_managed])
            reconciled.extend(managed_candidates[: len(pnp_managed)])
            reconciled.extend(dedupe_adapters_by_id(pnp_system))
            continue

        if pnp_system:
            # A Windows Bluetooth-driver row without a matching userspace-driver row means this
            # VID/PID is still OS-bound. Bumble/libusb may still glimpse the USB topology, but it
            # is not a usable managed adapter until WinUSB is installed.
            reconciled.extend(dedupe_adapters_by_id(pnp_system))
            continue

        reconciled.extend(dedupe_adapters_by_id(managed))

    return dedupe_adapters_by_id(reconciled)


def adapter_vid_pid_key(adapter: BluetoothAdapter) -> tuple[str, ...]:
    if adapter.vendor_id is None or adapter.product_id is None:
        return ("adapter", adapter.id)
    return ("vidpid", adapter.vendor_id.lower(), adapter.product_id.lower())


def dedupe_adapters_by_id(adapters: list[BluetoothAdapter]) -> list[BluetoothAdapter]:
    by_id: dict[str, BluetoothAdapter] = {}
    for adapter in adapters:
        by_id.setdefault(adapter.id, adapter)
    return list(by_id.values())


def windows_row_searchable_text(candidate: WindowsPnpCandidate) -> str:
    return " ".join(
        value
        for value in [
            candidate.name,
            candidate.service,
            candidate.device_class,
            candidate.manufacturer,
            candidate.driver_name,
            candidate.vendor_id,
            candidate.product_id,
        ]
        if value is not None and value != ""
    )


def windows_row_looks_like_managed_usb(candidate: WindowsPnpCandidate) -> bool:
    searchable = windows_row_searchable_text(candidate)
    if USB_USERSPACE_DRIVER_RE.search(searchable) is not None:
        return looks_like_managed_usb_bluetooth_adapter(searchable, candidate.vendor_id, candidate.product_id)
    return windows_row_looks_like_synchroni_winusb(candidate, searchable)


def windows_row_looks_like_synchroni_winusb(candidate: WindowsPnpCandidate, searchable: str) -> bool:
    if not is_known_usb_bluetooth_adapter(candidate.vendor_id, candidate.product_id):
        return False
    if SYNCHRONI_WINUSB_DRIVER_NAME in searchable.lower():
        return True
    device_class = candidate.device_class.strip().lower()
    service = candidate.service.strip().lower()
    manufacturer = (candidate.manufacturer or "").strip().lower()
    return (
        manufacturer == "synchroni"
        and device_class in WINDOWS_MANAGED_USB_CLASSES
        and service not in WINDOWS_OS_BLUETOOTH_SERVICES
    )


def windows_row_requires_claim(candidate: WindowsPnpCandidate) -> bool:
    if not is_known_usb_bluetooth_adapter(candidate.vendor_id, candidate.product_id):
        return False
    return not windows_row_looks_like_managed_usb(candidate)


def windows_claim_message(candidate: WindowsPnpCandidate) -> str:
    vendor_id = (candidate.vendor_id or "unknown").upper()
    product_id = (candidate.product_id or "unknown").upper()
    return (
        "Install the Synchroni WinUSB driver for this dedicated dongle model with a Windows admin prompt. "
        "Matching VID/PID dongles will stop using Windows Bluetooth and appear as managed USB. "
        f"VID:{vendor_id} PID:{product_id}"
    )


def windows_usb_transport(
    service: str,
    device_class: str,
    name: str,
    driver_name: str | None = None,
) -> str:
    searchable = " ".join([service, device_class, name, driver_name or ""]).lower()
    if "libusbk" in searchable:
        return "libusbk"
    if "libusb0" in searchable:
        return "libusb0"
    if "libusb" in searchable:
        return "libusb"
    return "winusb"
