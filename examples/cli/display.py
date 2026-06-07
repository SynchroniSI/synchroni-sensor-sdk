"""Rich terminal tables for adapter / device listing."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from synchroni_sensor_sdk.async_api.sensor_hub import ScanResult
from synchroni_sensor_sdk.core.bluetooth import BluetoothAdapter, BluetoothCapability

console = Console()


def print_capability(capability: BluetoothCapability) -> None:
    table = Table(title="Bluetooth capability", show_header=True, header_style="bold")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("platform", capability.platform)
    table.add_row("managed_usb", str(capability.supports_managed_usb))
    table.add_row("managed_usb_available", str(capability.managed_usb_available))
    table.add_row("windows_claim", str(capability.supports_windows_claim))
    if capability.notes:
        table.add_row("notes", capability.notes)
    console.print(table)


def print_adapters(adapters: list[BluetoothAdapter]) -> None:
    table = Table(title=f"Adapters ({len(adapters)})", show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Source")
    table.add_column("Platform")
    table.add_column("VID:PID", no_wrap=True)
    table.add_column("USB transport", overflow="fold")
    table.add_column("Claim", overflow="fold")
    table.add_column("In use", justify="center")

    for adapter in adapters:
        vid_pid = "-"
        if adapter.vendor_id and adapter.product_id:
            vid_pid = f"{adapter.vendor_id}:{adapter.product_id}"

        if adapter.claim_required:
            claim = adapter.claim_action or "required"
            if adapter.claim_message:
                claim = f"{claim}\n{adapter.claim_message}"
        else:
            claim = "-"

        table.add_row(
            adapter.id,
            adapter.name,
            adapter.source,
            adapter.platform,
            vid_pid,
            adapter.usb_transport or "-",
            claim,
            "yes" if adapter.is_in_use else "-",
        )

    console.print(table)


def print_devices(devices: list[ScanResult]) -> None:
    table = Table(title=f"Devices ({len(devices)})", show_header=True, header_style="bold")
    table.add_column("MAC", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("RSSI", justify="right")
    table.add_column("Adapter", overflow="fold")
    table.add_column("Routes", overflow="fold")

    for device in sorted(devices, key=lambda d: d.rssi, reverse=True):
        routes = "-"
        if device.routes:
            routes = ", ".join(f"{r.adapter_id} ({r.rssi})" for r in device.routes)
        table.add_row(
            device.mac_address,
            device.name or "-",
            str(device.rssi),
            device.adapter_id or "-",
            routes,
        )

    console.print(table)
