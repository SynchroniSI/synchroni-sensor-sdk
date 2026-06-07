"""On-demand USB HCI adapter inventory for multi-adapter mode.

Lazy-loads platform modules; default hubs never import this unless
``enable_multi_adapter=True``.
"""

from __future__ import annotations

import platform
from datetime import UTC, datetime

from synchroni_sensor_sdk.core.bluetooth import (
    SYSTEM_DEFAULT_ADAPTER_ID,
    BluetoothAdapter,
    BluetoothCapability,
)


def system_default_adapter() -> BluetoothAdapter:
    """Return the OS-default Bluetooth controller entry."""
    system = platform.system().lower()
    return BluetoothAdapter(
        id=SYSTEM_DEFAULT_ADAPTER_ID,
        name="System Bluetooth",
        source="system",
        platform=system if system else "unknown",
        transport="os",
        is_external=False,
        last_seen_at=datetime.now(UTC),
    )


def get_bluetooth_capability() -> BluetoothCapability:
    """Describe multi-adapter support for this host and optional install."""
    system = platform.system()
    system_l = system.lower()
    supports_managed = system_l in {"darwin", "windows"}
    supports_claim = system_l == "windows"
    managed_available = False
    if supports_managed:
        try:
            import bumble  # noqa: F401

            managed_available = True
        except ImportError:
            managed_available = False

    if not supports_managed:
        notes = "Managed USB dongles are only supported on macOS and Windows."
    elif not managed_available:
        notes = (
            "Platform can use dedicated USB HCI dongles, but Bumble is not installed. "
            "Install synchroni-sensor-sdk[managed-usb]."
        )
    elif system_l == "windows":
        notes = (
            "Windows supports dedicated EEG dongles bound to WinUSB/libusb. "
            "Use claim_adapter() for OS-bound known dongles (admin elevation)."
        )
    else:
        notes = "Use dedicated EEG dongles exposed for libusb access by the managed USB backend."

    return BluetoothCapability(
        platform=system_l or system,
        supports_managed_usb=supports_managed,
        supports_windows_claim=supports_claim,
        supports_firmware_pins=True,
        notes=notes,
        managed_usb_available=managed_available,
    )


async def list_managed_usb_adapters(*, command_timeout_s: float = 6.0) -> list[BluetoothAdapter]:
    """Discover external USB Bluetooth adapters suitable for multi-adapter mode."""
    system = platform.system().lower()
    if system == "darwin":
        from synchroni_sensor_sdk.async_api.driver.managed_usb.macos import (
            list_macos_managed_usb_adapters,
        )

        adapters = await list_macos_managed_usb_adapters(command_timeout_s)
    elif system == "windows":
        from synchroni_sensor_sdk.async_api.driver.managed_usb.windows import (
            list_windows_managed_usb_adapters,
        )

        adapters = await list_windows_managed_usb_adapters(command_timeout_s)
    else:
        adapters = []

    return [a for a in adapters if a.is_external]


async def list_all_adapters(*, command_timeout_s: float = 6.0) -> list[BluetoothAdapter]:
    """System default plus external managed/claim candidates."""
    managed = await list_managed_usb_adapters(command_timeout_s=command_timeout_s)
    return [system_default_adapter(), *managed]
