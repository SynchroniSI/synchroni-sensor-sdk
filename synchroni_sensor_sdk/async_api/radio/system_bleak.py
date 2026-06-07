"""System Bluetooth radio via Bleak (OS-owned HCI)."""

from __future__ import annotations

import logging

from bleak import BleakScanner

from synchroni_sensor_sdk.async_api.radio.base import RadioAdapter, RadioConnection, RadioScanHit
from synchroni_sensor_sdk.async_api.radio.continuous import ContinuousScanMixin
from synchroni_sensor_sdk.async_api.radio.filter import (
    is_synchroni_advertisement,
    product_mac,
    rssi_from_adv,
)
from synchroni_sensor_sdk.core.bluetooth import SYSTEM_DEFAULT_ADAPTER_ID

logger = logging.getLogger(__name__)


class SystemBleakRadioAdapter(ContinuousScanMixin, RadioAdapter):
    """Bleak ``system:default`` radio with local scan cache."""

    def __init__(self) -> None:
        ContinuousScanMixin.__init__(self)
        self._scanner: BleakScanner | None = None
        self._scanned: dict[str, RadioScanHit] = {}

    def _get_scanner(self) -> BleakScanner:
        if self._scanner is None:
            self._scanner = BleakScanner()
        return self._scanner

    @property
    def adapter_id(self) -> str:
        return SYSTEM_DEFAULT_ADAPTER_ID

    async def scan(self, timeout_ms: int) -> list[RadioScanHit]:
        devices = await self._get_scanner().discover(
            timeout=max(timeout_ms, 1) / 1000.0,
            return_adv=True,
        )
        hits: list[RadioScanHit] = []
        for _bleak_address, (device, advertisement_data) in devices.items():
            if device.name is None:
                continue
            if not is_synchroni_advertisement(advertisement_data):
                continue
            mac = product_mac(device, advertisement_data)
            hit = RadioScanHit(
                mac_address=mac,
                name=device.name,
                rssi=rssi_from_adv(advertisement_data),
                device=device,
                advertisement_data=advertisement_data,
            )
            self._scanned[mac] = hit
            hits.append(hit)
        return hits

    async def start_scan(self, period_ms: int = 3000) -> None:
        await self._start_continuous(period_ms, self.scan)

    def get_scanned(self, mac: str) -> RadioScanHit | None:
        return self._scanned.get(mac)

    def list_scanned(self) -> list[RadioScanHit]:
        return list(self._scanned.values())

    async def prepare_connection(self, mac: str) -> RadioConnection:
        hit = self._scanned.get(mac)
        if hit is None:
            raise ValueError(f"Sensor with address {mac} not found in scan results")
        return RadioConnection(
            mac_address=hit.mac_address,
            device=hit.device,
            advertisement_data=hit.advertisement_data,
            managed_usb=None,
        )

    async def close(self) -> None:
        self.stop_scan()
        self._scanned.clear()
        self._scanner = None
