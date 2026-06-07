from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections import Counter
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from synchroni_sensor_sdk.core.bluetooth import MANAGED_USB_ADAPTER_ID_PREFIX, BluetoothAdapter

VID_PID_RE = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.IGNORECASE)
USB_BLUETOOTH_ADAPTER_NAME_RE = re.compile(
    r"\b(bluetooth|bt|ble|csr|cambridge silicon radio|realtek|broadcom|qualcomm|nordic|hci)\b",
    re.IGNORECASE,
)
USB_USERSPACE_DRIVER_RE = re.compile(r"\b(winusb|libusb|libusbk|libusb0)\b", re.IGNORECASE)
KNOWN_USB_BLUETOOTH_VID_PID = frozenset(
    {
        ("0a12", "0001"),  # Cambridge Silicon Radio CSR8510
        ("10d7", "b012"),  # Actions "general adapter" / dedicated EEG dongles
        ("33fa", "0010"),  # UGREEN BT5.4 Adapter
        ("2357", "0604"),  # TP-Link UB500 Adapter
    }
)


@dataclass(frozen=True)
class LibusbUsbDevice:
    name: str | None
    manufacturer: str | None
    vendor_id: str
    product_id: str
    serial_number: str | None
    identity: str
    usb_transport: str


async def run_command(args: list[str], timeout_s: float) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        return await asyncio.to_thread(run_command_blocking, args, timeout_s)
    except (FileNotFoundError, OSError):
        return None

    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return None

    if process.returncode != 0:
        return None
    return stdout.decode(errors="ignore")


def run_command_blocking(args: list[str], timeout_s: float) -> str | None:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        return None
    return completed.stdout.decode(errors="ignore")


async def run_command_json(args: list[str], timeout_s: float) -> object | None:
    output = await run_command(args, timeout_s)
    if output is None:
        return None
    payload = output.strip()
    if payload == "":
        return None
    try:
        return cast(object, json.loads(payload))
    except json.JSONDecodeError:
        return None


def list_libusb_managed_usb_adapters(now: datetime, *, platform_name: str) -> list[BluetoothAdapter]:
    devices = [
        device
        for device in list_libusb_usb_devices()
        if looks_like_managed_usb_bluetooth_adapter(
            " ".join(
                value
                for value in [
                    device.name,
                    device.manufacturer,
                    device.vendor_id,
                    device.product_id,
                ]
                if value is not None
            ),
            device.vendor_id,
            device.product_id,
        )
    ]
    serial_counts = Counter(
        (device.vendor_id, device.product_id, device.serial_number)
        for device in devices
        if device.serial_number is not None
    )

    adapters: list[BluetoothAdapter] = []
    for device in devices:
        serial_is_unique = False
        if device.serial_number is not None:
            serial_key = (device.vendor_id, device.product_id, device.serial_number)
            serial_is_unique = serial_counts.get(serial_key, 0) == 1
        if serial_is_unique:
            identity = f"{device.vendor_id}:{device.product_id}:{normalize_identity_token(device.serial_number or '')}"
            usb_transport = managed_usb_transport_name(
                device.vendor_id,
                device.product_id,
                device.serial_number,
            )
        else:
            identity = device.identity
            usb_transport = device.usb_transport

        if usb_transport is None:
            continue

        adapters.append(
            BluetoothAdapter(
                id=f"{MANAGED_USB_ADAPTER_ID_PREFIX}{platform_name}:{identity}",
                name=usb_display_name(device.name, device.manufacturer),
                source="managed_usb",
                platform=platform_name,
                transport="libusb",
                vendor_id=device.vendor_id,
                product_id=device.product_id,
                serial_number=device.serial_number,
                usb_transport=usb_transport,
                is_external=True,
                last_seen_at=now,
            )
        )

    return adapters


def list_libusb_usb_devices() -> list[LibusbUsbDevice]:
    try:
        import usb1
        from bumble.transport.usb import load_libusb

        load_libusb()
        context = usb1.USBContext()
        context.open()
    except Exception:
        return []

    devices: list[LibusbUsbDevice] = []
    vid_pid_indexes: dict[tuple[str, str], int] = {}
    try:
        for device in context.getDeviceIterator(skip_on_error=True):
            try:
                vendor_id = f"{device.getVendorID():04x}"
                product_id = f"{device.getProductID():04x}"
                vid_pid_key = (vendor_id, product_id)
                device_index = vid_pid_indexes.get(vid_pid_key, 0)
                vid_pid_indexes[vid_pid_key] = device_index + 1

                device_path = libusb_device_path(device)
                usb_transport = managed_usb_transport_name(
                    vendor_id,
                    product_id,
                    None,
                    device_index=device_index if device_path is None else None,
                    device_path=device_path,
                )
                if usb_transport is None:
                    continue

                identity = device_path if device_path is not None else f"{vendor_id}:{product_id}:{device_index}"
                devices.append(
                    LibusbUsbDevice(
                        name=read_libusb_string(device, "getProduct"),
                        manufacturer=read_libusb_string(device, "getManufacturer"),
                        vendor_id=vendor_id,
                        product_id=product_id,
                        serial_number=read_libusb_string(device, "getSerialNumber"),
                        identity=identity,
                        usb_transport=usb_transport,
                    )
                )
            except Exception:
                continue
            finally:
                with suppress(Exception):
                    device.close()
    finally:
        with suppress(Exception):
            context.close()

    return devices


def read_libusb_string(device: Any, method_name: str) -> str | None:
    try:
        value = getattr(device, method_name)()
    except Exception:
        return None
    if isinstance(value, str) and value.strip() != "":
        return value.strip()
    return None


def libusb_device_path(device: Any) -> str | None:
    try:
        bus_number = device.getBusNumber()
        port_numbers = list(device.getPortNumberList())
    except Exception:
        return None

    if not port_numbers:
        return None
    port_path = ".".join(str(port_number) for port_number in port_numbers)
    return f"{bus_number}-{port_path}"


def usb_display_name(name: str | None, manufacturer: str | None) -> str:
    clean_name = name.strip() if name is not None and name.strip() != "" else None
    clean_manufacturer = manufacturer.strip() if manufacturer is not None and manufacturer.strip() != "" else None
    if (
        clean_name is not None
        and clean_manufacturer is not None
        and clean_manufacturer.lower() not in clean_name.lower()
    ):
        return f"{clean_manufacturer} {clean_name}"
    if clean_name is not None:
        return clean_name
    if clean_manufacturer is not None:
        return f"{clean_manufacturer} USB Bluetooth Adapter"
    return "USB Bluetooth Adapter"


def looks_like_managed_usb_bluetooth_adapter(
    value: str,
    vendor_id: str | None,
    product_id: str | None,
) -> bool:
    return is_known_usb_bluetooth_adapter(vendor_id, product_id) or looks_like_usb_bluetooth_adapter(value)


def is_known_usb_bluetooth_adapter(vendor_id: str | None, product_id: str | None) -> bool:
    return vendor_id is not None and product_id is not None and (vendor_id, product_id) in KNOWN_USB_BLUETOOTH_VID_PID


def looks_like_usb_bluetooth_adapter(value: str) -> bool:
    return USB_BLUETOOTH_ADAPTER_NAME_RE.search(value) is not None


def normalize_identity_token(value: str) -> str:
    cleaned = re.sub(r"\s+", "-", value.strip().lower())
    cleaned = re.sub(r"[^0-9a-z_.:-]", "-", cleaned)
    return cleaned or "unknown"


def managed_usb_transport_name(
    vendor_id: str | None,
    product_id: str | None,
    serial_number: str | None,
    *,
    device_index: int | None = None,
    device_path: str | None = None,
) -> str | None:
    if device_path is not None and device_path.strip() != "":
        return f"usb:{device_path.strip()}"
    if vendor_id is None or product_id is None:
        return None
    spec = f"usb:{vendor_id}:{product_id}"
    if device_index is not None:
        spec = f"{spec}#{device_index}"
    elif serial_number is not None and serial_number.strip() != "":
        spec = f"{spec}/{serial_number.strip()}"
    return spec


def walk_dict_nodes(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dict_nodes(child)
    elif isinstance(value, list):
        for item in value:
            yield from walk_dict_nodes(item)


def first_string(node: dict[str, object], keys: list[str]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    return None


def normalize_hex_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower().replace("0x", "")
    cleaned = cleaned.replace("vid_", "").replace("pid_", "")
    cleaned = re.sub(r"[^0-9a-f]", "", cleaned)
    if len(cleaned) < 4:
        return None
    return cleaned[-4:]
