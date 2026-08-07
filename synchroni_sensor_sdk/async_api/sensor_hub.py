"""Async sensor hub: scan, connect, optional multi-adapter routing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from synchroni_sensor_sdk.async_api.radio import RadioRegistry, RadioScanHit
from synchroni_sensor_sdk.async_api.radio.filter import RFSTAR_SERVICE_GUID, SERVICE_GUID
from synchroni_sensor_sdk.async_api.sensor import Sensor
from synchroni_sensor_sdk.core.bluetooth import (
    SYSTEM_DEFAULT_ADAPTER_ID,
    BluetoothAdapter,
    BluetoothCapability,
    ClaimResult,
    SensorRoute,
)
from synchroni_sensor_sdk.core.driver import driver_factory
from synchroni_sensor_sdk.core.exceptions import MultiAdapterDisabledError, SensorTerminatedError
from synchroni_sensor_sdk.core.logging_config import configure_logging as _configure_logging

if TYPE_CHECKING:
    from synchroni_sensor_sdk.async_api.multi_adapter.controller import MultiAdapterController
else:
    MultiAdapterController = object  # noqa: N816

# Re-export filter constants for callers that imported them from this module.
__all__ = [
    "RFSTAR_SERVICE_GUID",
    "SERVICE_GUID",
    "ScanResult",
    "SensorHub",
]


@dataclass
class ScanResult:
    """Discovered sensor summary for applications.

    When multi-adapter mode is enabled, ``adapter_id`` and ``routes`` describe
    which host HCI controller(s) observed the device. Separate dongles isolate
    controllers; they do not provide hardware sample-time synchronization.
    """

    mac_address: str
    name: str
    rssi: int
    adapter_id: str | None = None
    routes: list[SensorRoute] = field(default_factory=list)


class SensorHub:
    """
    Async entry point for scanning and connecting to sensors.

    Per-radio scan/connect transport lives on :class:`~synchroni_sensor_sdk.async_api.radio.RadioAdapter`
    instances (system Bleak vs managed USB). The hub is the public façade: fan-out,
    claims, connected-sensor map, and callbacks.

    Parameters
    ----------
    enable_multi_adapter:
        When True, enable inventory, managed-USB scan/connect, reservations, and
        optional WinUSB claim APIs. Requires optional extras for Bumble (and a
        claim helper on Windows when claiming). When False (default), only system
        Bluetooth via Bleak is used and multi-adapter methods raise
        :class:`~synchroni_sensor_sdk.core.exceptions.MultiAdapterDisabledError`.
    winusb_installer_path:
        Path to the WinUSB claim helper. When omitted, the SDK checks
        ``SYNCHRONI_WINUSB_INSTALLER``, package resources, then a user cache, and
        finally downloads ``winusb-installer`` from the public assets manifest
        (SHA-256 verified). See :func:`~synchroni_sensor_sdk.clear_winusb_installer_cache`.
    firmware_resource_dir:
        Directory of pinned dongle firmware blobs (size + SHA-256 verified when mapped).

    Must be used from a running asyncio event loop. For blocking usage, see
    :class:`~synchroni_sensor_sdk.sync_api.sensor_hub.SensorHub`.
    """

    def __init__(
        self,
        *,
        enable_multi_adapter: bool = False,
        winusb_installer_path: str | Path | None = None,
        firmware_resource_dir: str | Path | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._enable_multi_adapter = enable_multi_adapter
        self._connected_sensors: dict[str, Sensor] = {}
        # sensor mac -> adapter_id used for occupancy release
        self._sensor_adapters: dict[str, str] = {}
        self._terminated = False
        self._continuous_radio_id: str | None = None
        self._continuous_on_found: Callable[[list[ScanResult]], None] | None = None
        self._enable_callback: Callable[[bool], None] | None = None
        self._bluetooth_enabled = True
        self._multi: MultiAdapterController | None = None
        self._firmware_resource_dir = firmware_resource_dir

        if enable_multi_adapter:
            from synchroni_sensor_sdk.async_api.multi_adapter.controller import MultiAdapterController as _Ctrl

            self._multi = _Ctrl(
                winusb_installer_path=winusb_installer_path,
                firmware_resource_dir=firmware_resource_dir,
            )

        self._radios = RadioRegistry(
            self._multi,
            firmware_resource_dir=firmware_resource_dir,
        )

    @property
    def enable_multi_adapter(self) -> bool:
        """Whether this hub owns multi-adapter (dedicated dongle) routing."""
        return self._enable_multi_adapter

    def _require_multi(self) -> MultiAdapterController:
        if self._multi is None:
            raise MultiAdapterDisabledError("Multi-adapter APIs require SensorHub(enable_multi_adapter=True).")
        return self._multi

    def _ensure_active(self) -> None:
        if self._terminated:
            raise SensorTerminatedError("Sensor hub has been closed.")

    def _to_scan_result(
        self,
        hit: RadioScanHit,
        *,
        adapter_id: str,
        routes: list[SensorRoute] | None = None,
    ) -> ScanResult:
        if routes is None:
            routes = [SensorRoute(adapter_id=adapter_id, mac_address=hit.mac_address, rssi=hit.rssi)]
        return ScanResult(
            mac_address=hit.mac_address,
            name=hit.name,
            rssi=hit.rssi,
            adapter_id=adapter_id,
            routes=list(routes),
        )

    def _routes_for_mac(self, mac: str) -> list[SensorRoute]:
        routes: list[SensorRoute] = []
        for radio in self._radios.list_known():
            hit = radio.get_scanned(mac)
            if hit is not None:
                routes.append(SensorRoute(adapter_id=radio.adapter_id, mac_address=mac, rssi=hit.rssi))
        return routes

    def _lookup_hit(
        self,
        address: str,
        *,
        adapter_id: str | None = None,
    ) -> tuple[str, RadioScanHit] | None:
        """Return (adapter_id, hit) for a MAC; prefers system, then strongest RSSI."""
        if adapter_id is not None:
            for radio in self._radios.list_known():
                if radio.adapter_id == adapter_id:
                    hit = radio.get_scanned(address)
                    if hit is not None:
                        return radio.adapter_id, hit
            return None

        sys_hit = self._radios.system().get_scanned(address)
        if sys_hit is not None:
            return SYSTEM_DEFAULT_ADAPTER_ID, sys_hit

        best: tuple[str, RadioScanHit] | None = None
        for radio in self._radios.list_known():
            hit = radio.get_scanned(address)
            if hit is None:
                continue
            if best is None or hit.rssi > best[1].rssi:
                best = (radio.adapter_id, hit)
        return best

    @property
    def is_bluetooth_enabled(self) -> bool:
        """Whether Bluetooth is considered enabled for scanning."""
        return self._bluetooth_enabled

    def set_bluetooth_enable_callback(self, callback: Callable[[bool], None] | None) -> None:
        """Register a callback for Bluetooth adapter enable/disable changes."""
        self._enable_callback = callback

    def set_bluetooth_enabled(self, enabled: bool) -> None:
        """Update cached adapter state and notify listeners."""
        self._bluetooth_enabled = enabled
        if self._enable_callback is not None:
            self._enable_callback(enabled)

    def configure_logging(
        self,
        *,
        enabled: bool = True,
        path: str | None = None,
        level: int = logging.DEBUG,
    ) -> None:
        """Configure stdlib logging for the ``synchroni_sensor_sdk`` package."""
        _configure_logging(enabled=enabled, path=path, level=level)

    def get_bluetooth_capability(self) -> BluetoothCapability:
        """Report multi-adapter / managed-USB availability for this install.

        Safe to call even when ``enable_multi_adapter`` is False.
        """
        from synchroni_sensor_sdk.async_api.driver.managed_usb.inventory import (
            get_bluetooth_capability,
        )

        return get_bluetooth_capability()

    async def list_bluetooth_adapters(self) -> list[BluetoothAdapter]:
        """Refresh and return host Bluetooth adapters (multi-adapter mode)."""
        self._ensure_active()
        multi = self._require_multi()
        return await multi.refresh_adapters()

    async def claim_adapter(self, adapter_id: str) -> ClaimResult:
        """Install/bind WinUSB for a claim_required Windows dongle (elevated).

        Matching VID/PID devices stop acting as general Windows Bluetooth radios.
        """
        self._ensure_active()
        return await self._require_multi().claim_adapter(adapter_id)

    async def scan(
        self,
        timeout_ms: int,
        *,
        adapter_id: str | None = None,
        adapter_ids: Sequence[str] | None = None,
    ) -> list[ScanResult]:
        """Discover nearby sensors.

        Parameters
        ----------
        timeout_ms:
            Scan duration in milliseconds.
        adapter_id:
            Single radio. ``None`` / ``system:default`` uses Bleak. A ``usb:…``
            id requires multi-adapter mode.
        adapter_ids:
            When set (multi-adapter only), scan those radios concurrently.
            Each id may be ``system:default`` or a managed ``usb:…`` adapter.
            Overrides ``adapter_id``.
        """
        self._ensure_active()
        if not self._bluetooth_enabled:
            return []

        if adapter_ids is not None:
            self._require_multi()
            return await self._scan_radios(timeout_ms, adapter_ids=adapter_ids)

        aid = adapter_id
        if aid is None or aid == SYSTEM_DEFAULT_ADAPTER_ID:
            hits = await self._radios.system().scan(timeout_ms)
            return [self._to_scan_result(hit, adapter_id=SYSTEM_DEFAULT_ADAPTER_ID) for hit in hits]

        self._require_multi()
        return await self._scan_radios(timeout_ms, adapter_ids=[aid])

    async def scan_managed_usb(self, timeout_ms: int = 2000) -> list[ScanResult]:
        """Scan all free, ready managed USB dongles and return route-stamped results."""
        self._ensure_active()
        multi = self._require_multi()
        if not multi.list_cached_adapters():
            await multi.refresh_adapters()
        return await self._scan_radios(timeout_ms, adapter_ids=None)

    async def _scan_radios(
        self,
        timeout_ms: int,
        *,
        adapter_ids: Sequence[str] | None,
    ) -> list[ScanResult]:
        radios = await self._radios.list_managed_for_scan(adapter_ids)
        if not radios:
            return []
        batches = await asyncio.gather(*(radio.scan(timeout_ms) for radio in radios))
        results: list[ScanResult] = []
        for radio, hits in zip(radios, batches, strict=True):
            for hit in hits:
                routes = self._routes_for_mac(hit.mac_address)
                results.append(self._to_scan_result(hit, adapter_id=radio.adapter_id, routes=routes or None))
        return results

    async def start_scan(
        self,
        period_ms: int = 3000,
        *,
        adapter_id: str | None = None,
        on_device_found: Callable[[list[ScanResult]], None] | None = None,
    ) -> None:
        """Continuously discover sensors until :meth:`stop_scan` is called.

        Continuous scan is single-radio only. Pass ``adapter_id`` only in
        multi-adapter mode for a fixed USB dongle.

        Parameters
        ----------
        on_device_found:
            Optional listener for each continuous-scan pass. Scoped to this
            continuous session (cleared by :meth:`stop_scan`); one-shot
            :meth:`scan` returns results instead of using a hub-global callback.
        """
        self._ensure_active()
        if self.is_scanning():
            return
        aid = adapter_id if adapter_id is not None else SYSTEM_DEFAULT_ADAPTER_ID
        if aid != SYSTEM_DEFAULT_ADAPTER_ID:
            self._require_multi()
        radio = await self._radios.get(aid)
        self._continuous_radio_id = radio.adapter_id
        self._continuous_on_found = on_device_found

        def _on_continuous_hits(hits: list[RadioScanHit]) -> None:
            results = [
                self._to_scan_result(
                    hit, adapter_id=radio.adapter_id, routes=self._routes_for_mac(hit.mac_address) or None
                )
                for hit in hits
            ]
            cb = self._continuous_on_found
            if cb is not None and results:
                cb(results)

        # Radio continuous hook is hub-internal (ContinuousScanMixin).
        radio._set_scan_results_callback(_on_continuous_hits)  # type: ignore[attr-defined]
        await radio.start_scan(period_ms)

    def stop_scan(self) -> None:
        """Stop continuous scanning and clear the continuous session listener."""
        for radio in self._radios.list_known():
            if self._continuous_radio_id is None or radio.adapter_id == self._continuous_radio_id:
                radio._set_scan_results_callback(None)  # type: ignore[attr-defined]
                radio.stop_scan()
        self._continuous_radio_id = None
        self._continuous_on_found = None

    def is_scanning(self) -> bool:
        """Return whether continuous scanning is active on the hub's continuous radio."""
        if self._continuous_radio_id is not None:
            for radio in self._radios.list_known():
                if radio.adapter_id == self._continuous_radio_id:
                    return radio.is_scanning()
        return any(radio.is_scanning() for radio in self._radios.list_known())

    async def connect(self, address: str, *, adapter_id: str | None = None) -> Sensor:
        """Connect to a previously discovered sensor.

        Parameters
        ----------
        address:
            Product MAC from scan (service-data derived where possible).
        adapter_id:
            Host radio. Default system Bleak. Managed USB ids require multi-adapter
            mode; reserved temporarily for the connect attempt then claimed for the
            hub session (cleared on hub close, not on disconnect).
        """
        self._ensure_active()
        if address in self._connected_sensors:
            return self._connected_sensors[address]

        use_managed = adapter_id is not None and adapter_id != SYSTEM_DEFAULT_ADAPTER_ID
        if use_managed:
            assert adapter_id is not None
            return await self._connect_with_managed_adapter(address, adapter_id)

        radio = self._radios.system()
        connection = await radio.prepare_connection(address)
        driver = driver_factory(
            connection.mac_address,
            device=connection.device,
            advertisement_data=connection.advertisement_data,
        )
        sensor = await Sensor.create(
            connection.mac_address,
            driver=driver,
            adapter_id=SYSTEM_DEFAULT_ADAPTER_ID,
        )
        await sensor.connect()
        self._connected_sensors[address] = sensor
        self._sensor_adapters[address] = SYSTEM_DEFAULT_ADAPTER_ID
        return sensor

    async def _connect_with_managed_adapter(self, address: str, adapter_id: str) -> Sensor:
        """Connect via a multi-adapter managed USB radio.

        Temporarily reserves the dongle, connects, then records a hub-session
        claim (occupy) for that adapter + sensor MAC. Claims persist across
        disconnects and are cleared only when the hub is closed. On failure the
        temporary reserve is released; an existing session claim for the same MAC
        is left in place.
        """
        multi = self._require_multi()
        await multi.reserve(adapter_id, address)
        try:
            radio = await self._radios.get(adapter_id)
            connection = await radio.prepare_connection(address)
            if connection.managed_usb is None:
                raise ValueError(f"No managed USB backend for sensor {address}")
            driver = driver_factory(
                address,
                device=connection.device,
                advertisement_data=connection.advertisement_data,
                managed_usb=connection.managed_usb,
            )
            sensor = await Sensor.create(address, driver=driver, adapter_id=adapter_id)
            await sensor.connect()
            await multi.occupy(adapter_id, address)
            self._sensor_adapters[address] = adapter_id
        except Exception:
            await multi.release_reserve(adapter_id)
            raise
        self._connected_sensors[address] = sensor
        return sensor

    async def get_scanned_device(
        self,
        address: str,
        *,
        adapter_id: str | None = None,
    ) -> ScanResult | None:
        """Return a cached scan result for a MAC (and optional adapter), or ``None``.

        Without ``adapter_id``, prefers ``system:default``, then the strongest
        remaining radio that observed the MAC.
        """
        found = self._lookup_hit(address, adapter_id=adapter_id)
        if found is None:
            return None
        aid, hit = found
        return self._to_scan_result(hit, adapter_id=aid, routes=self._routes_for_mac(address) or None)

    async def get_connected_sensor(self, address: str) -> Sensor | None:
        """Return a connected sensor by MAC, or ``None`` if not connected."""
        return self._connected_sensors.get(address)

    async def get_sensor(self, address: str) -> Sensor | None:
        return await self.get_connected_sensor(address)

    async def list_connected_sensors(self) -> list[Sensor]:
        return list(self._connected_sensors.values())

    async def list_scanned_devices(self) -> list[ScanResult]:
        results: list[ScanResult] = []
        for radio in self._radios.list_known():
            for hit in radio.list_scanned():
                results.append(
                    self._to_scan_result(
                        hit,
                        adapter_id=radio.adapter_id,
                        routes=self._routes_for_mac(hit.mac_address) or None,
                    )
                )
        return results

    async def disconnect(self, address: str) -> None:
        """Tear down a connected sensor without revoking multi-adapter claims.

        Managed USB dongle claims remain on the hub until :meth:`close` so the
        adapter stays reserved for the same routing decision across reconnects.
        Temporary connect reserves for this MAC are cleared.
        """
        sensor = self._connected_sensors.pop(address, None)
        if sensor is not None:
            await sensor.destroy()
        if self._multi is not None:
            await self._multi.release_occupancy(address)
        self._sensor_adapters.pop(address, None)

    async def close(self) -> None:
        """Disconnect all sensors, stop scanning, revoke multi-adapter claims."""
        self.stop_scan()
        for address in list(self._connected_sensors):
            await self.disconnect(address)
        await self._radios.close_all()
        if self._multi is not None:
            await self._multi.close()
        self._terminated = True

    async def __aenter__(self) -> SensorHub:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
