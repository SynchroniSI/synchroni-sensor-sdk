"""Managed USB host radio via Bumble (libusb / WinUSB)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from synchroni_sensor_sdk.async_api.radio.base import RadioAdapter, RadioConnection, RadioScanHit
from synchroni_sensor_sdk.async_api.radio.continuous import ContinuousScanMixin
from synchroni_sensor_sdk.core.bluetooth import ManagedUsbBackend
from synchroni_sensor_sdk.core.exceptions import (
    BluetoothAdapterNotFoundError,
    ManagedUsbUnavailableError,
)

if TYPE_CHECKING:
    from synchroni_sensor_sdk.async_api.multi_adapter.controller import MultiAdapterController

logger = logging.getLogger(__name__)


class ManagedUsbRadioAdapter(ContinuousScanMixin, RadioAdapter):
    """One dedicated USB HCI dongle for scan + connection handle preparation."""

    def __init__(
        self,
        *,
        adapter_id: str,
        multi: MultiAdapterController,
        firmware_resource_dir: Path | str | None = None,
    ) -> None:
        ContinuousScanMixin.__init__(self)
        self._adapter_id = adapter_id
        self._multi = multi
        self._firmware_resource_dir = firmware_resource_dir
        self._scanned: dict[str, RadioScanHit] = {}

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def _resolve_transport(self) -> str:
        adapter = self._multi.resolve_adapter(self._adapter_id)
        self._adapter_id = adapter.id
        if adapter.usb_transport:
            return adapter.usb_transport
        raise BluetoothAdapterNotFoundError(f"Adapter {self._adapter_id!r} has no Bumble transport string.")

    async def scan(self, timeout_ms: int) -> list[RadioScanHit]:
        from synchroni_sensor_sdk.async_api.driver.managed_usb.backend import (
            scan_managed_usb_devices,
        )

        try:
            transport = self._resolve_transport()
        except BluetoothAdapterNotFoundError:
            logger.warning("Managed USB scan skipped for %s: not in inventory", self._adapter_id)
            return []

        timeout_s = max(timeout_ms / 1000.0, 0.1)
        try:
            raw_hits = await scan_managed_usb_devices(transport_name=transport, timeout_s=timeout_s)
        except Exception:
            logger.warning("Managed USB scan failed for %s", self._adapter_id, exc_info=True)
            return []

        hits: list[RadioScanHit] = []
        for hit in raw_hits:
            entry = RadioScanHit(
                mac_address=hit.mac_address,
                name=hit.name,
                rssi=hit.rssi,
                device=hit.device,
                advertisement_data=hit.advertisement_data,
                peer_address=hit.peer_address,
                transport_name=transport,
            )
            self._scanned[hit.mac_address] = entry
            hits.append(entry)
        return hits

    async def start_scan(self, period_ms: int = 3000) -> None:
        await self._start_continuous(period_ms, self.scan)

    def get_scanned(self, mac: str) -> RadioScanHit | None:
        return self._scanned.get(mac)

    def list_scanned(self) -> list[RadioScanHit]:
        return list(self._scanned.values())

    async def prepare_connection(self, mac: str) -> RadioConnection:
        from synchroni_sensor_sdk.async_api.driver.managed_usb.firmware import ensure_firmware
        from synchroni_sensor_sdk.async_api.driver.managed_usb.windows_claim import require_claimable

        try:
            adapter = self._multi.resolve_adapter(self._adapter_id)
        except BluetoothAdapterNotFoundError:
            await self._multi.refresh_adapters()
            adapter = self._multi.resolve_adapter(self._adapter_id)
        self._adapter_id = adapter.id

        if adapter.source != "managed_usb" and adapter.claim_required:
            require_claimable(adapter)
        if adapter.source != "managed_usb":
            raise ManagedUsbUnavailableError(
                f"Adapter {self._adapter_id} is not a managed USB dongle. "
                "Select a managed_usb adapter after claim_adapter() if needed."
            )
        require_claimable(adapter)
        if not adapter.usb_transport:
            raise BluetoothAdapterNotFoundError(f"Adapter {self._adapter_id} has no Bumble transport string.")

        ensure_firmware(adapter, resource_dir=self._firmware_resource_dir)

        hit = self._scanned.get(mac)
        if hit is None or not hit.peer_address:
            from synchroni_sensor_sdk.async_api.driver.managed_usb.backend import rediscover_peer_address

            rediscovered = await rediscover_peer_address(
                transport_name=adapter.usb_transport,
                target_mac=mac,
                timeout_s=1.0,
            )
            if rediscovered is None:
                raise BluetoothAdapterNotFoundError(f"Sensor {mac} was not observed on adapter {self._adapter_id}.")
            hit = RadioScanHit(
                mac_address=mac,
                name=rediscovered.name,
                rssi=rediscovered.rssi,
                device=rediscovered.device,
                advertisement_data=rediscovered.advertisement_data,
                peer_address=rediscovered.peer_address,
                transport_name=adapter.usb_transport,
            )
            self._scanned[mac] = hit

        assert hit.peer_address is not None
        backend = ManagedUsbBackend(
            transport_name=adapter.usb_transport,
            peer_address=hit.peer_address,
            adapter_id=self._adapter_id,
        )
        return RadioConnection(
            mac_address=mac,
            device=hit.device,
            advertisement_data=hit.advertisement_data,
            managed_usb=backend,
        )

    async def close(self) -> None:
        self.stop_scan()
        self._scanned.clear()
