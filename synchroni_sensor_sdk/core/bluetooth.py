"""Public Bluetooth / multi-adapter types for the v2 SDK.

These types describe **host HCI USB dongles** (transport routing), not Synchroni headsets.
Separate physical adapters isolate Bluetooth controllers; they do **not** provide
hardware sample-time alignment or a shared acquisition clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# OS-default radio (Bleak). Multi-adapter inventory always includes this entry.
SYSTEM_DEFAULT_ADAPTER_ID = "system:default"

# Stable ID namespace for libusb/WinUSB managed HCI adapters.
MANAGED_USB_ADAPTER_ID_PREFIX = "usb:"

# Claim action recognized by :meth:`SensorHub.claim_adapter`.
WINDOWS_CLAIM_ACTION_WINUSB = "windows_winusb_install"

# Known EEG USB dongle VID:PID pairs that the SDK will help claim for WinUSB.
KNOWN_EEG_USB_DONGLES: frozenset[tuple[str, str]] = frozenset(
    {
        ("0a12", "0001"),  # Cambridge Silicon Radio CSR8510 A10
        ("10d7", "b012"),  # Actions "general adapter" / dedicated EEG dongles
    }
)


@dataclass(frozen=True)
class BluetoothAdapter:
    """A host Bluetooth controller the hub can route connections through.

    Parameters
    ----------
    id:
        Stable identifier (``system:default`` or ``usb:{platform}:{identity}``).
    source:
        ``system`` for OS-owned Bluetooth, ``managed_usb`` for userspace (libusb/WinUSB).
    claim_required:
        When True, :meth:`~synchroni_sensor_sdk.async_api.sensor_hub.SensorHub.claim_adapter`
        must succeed before the dongle can be used for managed USB scan/connect.
    usb_transport:
        Bumble ``open_transport`` specifier (e.g. ``usb:10d7:b012``). None for system BLE.
    is_in_use:
        Filled by the hub when a hub-session claim or temporary reserve owns this
        adapter. Session claims outlive individual sensor disconnects.
    firmware_status:
        Optional pin state: ``None``, ``ready``, ``missing``, or ``failed``.
    """

    id: str
    name: str
    source: str
    platform: str
    transport: str | None = None
    vendor_id: str | None = None
    product_id: str | None = None
    serial_number: str | None = None
    device_instance_id: str | None = None
    usb_transport: str | None = None
    driver_name: str | None = None
    claim_required: bool = False
    claim_action: str | None = None
    claim_message: str | None = None
    is_external: bool = False
    is_in_use: bool = False
    firmware_status: str | None = None
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class BluetoothCapability:
    """What multi-adapter features this install/platform can offer."""

    platform: str
    supports_managed_usb: bool
    supports_windows_claim: bool
    supports_firmware_pins: bool
    notes: str
    managed_usb_available: bool = False
    """True when the optional ``managed-usb`` (Bumble) dependency imports successfully."""


@dataclass(frozen=True)
class SensorRoute:
    """One observation of a sensor MAC on a specific adapter (route for connect selection)."""

    adapter_id: str
    mac_address: str
    rssi: int


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of :meth:`~synchroni_sensor_sdk.async_api.sensor_hub.SensorHub.claim_adapter`."""

    adapter_id: str
    success: bool
    message: str
    log_path: str | None = None


@dataclass(frozen=True)
class ManagedUsbBackend:
    """Transport metadata injected into the GForce driver for Bumble/libusb links.

    Attributes
    ----------
    transport_name:
        Bumble HCI transport string (from :attr:`BluetoothAdapter.usb_transport`).
    peer_address:
        Link-layer address reported by that controller's advertisements / connect target.
    adapter_id:
        Hub adapter id used for occupancy tracking.
    """

    transport_name: str
    peer_address: str
    adapter_id: str
