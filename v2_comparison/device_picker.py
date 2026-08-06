"""Scan result filtering and interactive device selection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from v2_comparison.config import DEVICE_NAME_PREFIXES, MIN_RSSI

T = TypeVar("T")


@dataclass(frozen=True)
class PickableDevice:
    name: str
    address: str
    rssi: int
    payload: object


def matches_device_filter(name: str, rssi: int) -> bool:
    if rssi <= MIN_RSSI:
        return False
    return any(name.startswith(prefix) for prefix in DEVICE_NAME_PREFIXES)


def filter_devices(
    devices: list[T],
    *,
    name_getter: Callable[[T], str | None],
    address_getter: Callable[[T], str],
    rssi_getter: Callable[[T], int],
) -> list[PickableDevice]:
    picked: list[PickableDevice] = []
    for device in devices:
        name = name_getter(device) or ""
        rssi = int(rssi_getter(device))
        if not matches_device_filter(name, rssi):
            continue
        picked.append(
            PickableDevice(
                name=name,
                address=address_getter(device),
                rssi=rssi,
                payload=device,
            )
        )
    return picked


def prompt_device_selection(devices: list[PickableDevice]) -> PickableDevice:
    if not devices:
        raise ValueError("No devices matched the filter (RSSI > -80, name OB/Sync/Orion).")

    if len(devices) == 1:
        device = devices[0]
        print(f"\nAuto-selected device: {device.name}  {device.address}  RSSI={device.rssi}")
        return device

    print("\nDiscovered devices:")
    for index, device in enumerate(devices, start=1):
        print(f"  [{index}] {device.name}  {device.address}  RSSI={device.rssi}")

    while True:
        choice = input("\nSelect device number (or q to quit): ").strip()
        if choice.lower() in {"q", "quit", "exit"}:
            raise KeyboardInterrupt("User cancelled device selection")
        if not choice.isdigit():
            print("Enter a number from the list.")
            continue
        idx = int(choice)
        if 1 <= idx <= len(devices):
            return devices[idx - 1]
        print(f"Enter a value between 1 and {len(devices)}.")
