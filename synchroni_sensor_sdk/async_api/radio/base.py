"""RadioAdapter: per-host-HCI scan cache and connection-handle preparation."""

from __future__ import annotations

import abc
from dataclasses import dataclass

from bleak import AdvertisementData, BLEDevice

from synchroni_sensor_sdk.core.bluetooth import ManagedUsbBackend


@dataclass
class RadioScanHit:
    """One sensor observation retained for connect preparation on a radio."""

    mac_address: str
    name: str
    rssi: int
    device: BLEDevice
    advertisement_data: AdvertisementData
    peer_address: str | None = None
    transport_name: str | None = None


@dataclass
class RadioConnection:
    """Inputs for :func:`~synchroni_sensor_sdk.core.driver.driver_factory` (no GATT open)."""

    mac_address: str
    device: BLEDevice
    advertisement_data: AdvertisementData
    managed_usb: ManagedUsbBackend | None = None


class RadioAdapter(abc.ABC):
    """Host Bluetooth radio: scan, continuous scan, and connect-handle preparation.

    Implementations own a per-radio scan cache (keyed by MAC) and continuous-scan
    state. They do **not** own hub-session claims or the public :class:`Sensor`
    lifecycle.
    """

    @property
    @abc.abstractmethod
    def adapter_id(self) -> str:
        """Stable hub adapter id (``system:default`` or managed ``usb:…``)."""

    @abc.abstractmethod
    async def scan(self, timeout_ms: int) -> list[RadioScanHit]:
        """One-shot scan; updates this radio's cache and returns matching hits."""

    @abc.abstractmethod
    async def start_scan(self, period_ms: int = 3000) -> None:
        """Start continuous scan loops for this radio until :meth:`stop_scan`."""

    @abc.abstractmethod
    def stop_scan(self) -> None:
        """Stop continuous scanning on this radio."""

    @abc.abstractmethod
    def is_scanning(self) -> bool:
        """Whether continuous scan is active on this radio."""

    @abc.abstractmethod
    def get_scanned(self, mac: str) -> RadioScanHit | None:
        """Return a cached hit for **mac**, or ``None``."""

    @abc.abstractmethod
    def list_scanned(self) -> list[RadioScanHit]:
        """Return all cached hits on this radio."""

    @abc.abstractmethod
    async def prepare_connection(self, mac: str) -> RadioConnection:
        """Resolve BLE/Bumble handles for **mac** without opening GATT."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Stop continuous scan and clear this radio's scan cache.

        Does not tear down shared managed USB HCI radio sessions (hub/controller
        owns that choke point).
        """
