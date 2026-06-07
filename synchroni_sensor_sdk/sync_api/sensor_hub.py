from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from synchroni_sensor_sdk.async_api.sensor_hub import ScanResult
from synchroni_sensor_sdk.async_api.sensor_hub import SensorHub as AsyncSensorHub
from synchroni_sensor_sdk.core.bluetooth import BluetoothAdapter, BluetoothCapability, ClaimResult
from synchroni_sensor_sdk.sync_api._bridge import SyncBridge
from synchroni_sensor_sdk.sync_api.runtime import EventLoopRunner
from synchroni_sensor_sdk.sync_api.sensor import Sensor


class SensorHub(SyncBridge):
    """
    Synchronous entry point for the SDK.

    Owns a background asyncio event loop and schedules the async
    :class:`~synchroni_sensor_sdk.async_api.sensor_hub.SensorHub` onto it.

    Parameters
    ----------
    enable_multi_adapter:
        Gate multi-dongle inventory, managed USB scan/connect, and claim APIs.
    winusb_installer_path:
        Optional path to the elevated WinUSB claim helper on Windows. When
        omitted, the SDK uses env/package/cache or downloads from the public
        assets manifest (SHA-256 verified).
    firmware_resource_dir:
        Optional directory of pinned dongle firmware blobs.

    Example::

        with SensorHub() as hub:
            devices = hub.scan(timeout_ms=3000)
            sensor = hub.connect(devices[0].mac_address)
            sensor.start_streaming()

    Multi-adapter example::

        with SensorHub(enable_multi_adapter=True) as hub:
            for a in hub.list_bluetooth_adapters():
                if a.claim_required:
                    hub.claim_adapter(a.id)
            devices = hub.scan_managed_usb(timeout_ms=2000)
            sensor = hub.connect(devices[0].mac_address, adapter_id=devices[0].adapter_id)
    """

    def __init__(
        self,
        *,
        enable_multi_adapter: bool = False,
        winusb_installer_path: str | Path | None = None,
        firmware_resource_dir: str | Path | None = None,
    ) -> None:
        self._runner = EventLoopRunner()
        self._runner.start()
        super().__init__(self._runner)
        self._logger = logging.getLogger(__name__)
        self._enable_multi_adapter = enable_multi_adapter
        self._winusb_installer_path = winusb_installer_path
        self._firmware_resource_dir = firmware_resource_dir
        self._async_hub = self._run(self._create_async_hub())

    async def _create_async_hub(self) -> AsyncSensorHub:
        return AsyncSensorHub(
            enable_multi_adapter=self._enable_multi_adapter,
            winusb_installer_path=self._winusb_installer_path,
            firmware_resource_dir=self._firmware_resource_dir,
        )

    @property
    def enable_multi_adapter(self) -> bool:
        return self._async_hub.enable_multi_adapter

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The background asyncio loop used for all BLE I/O."""
        return self._runner.loop

    def get_bluetooth_capability(self) -> BluetoothCapability:
        return self._async_hub.get_bluetooth_capability()

    def list_bluetooth_adapters(self) -> list[BluetoothAdapter]:
        return self._run(self._async_hub.list_bluetooth_adapters())

    def claim_adapter(self, adapter_id: str) -> ClaimResult:
        return self._run(self._async_hub.claim_adapter(adapter_id))

    def scan(
        self,
        timeout_ms: int,
        *,
        adapter_id: str | None = None,
        adapter_ids: Sequence[str] | None = None,
    ) -> list[ScanResult]:
        return self._run(self._async_hub.scan(timeout_ms, adapter_id=adapter_id, adapter_ids=adapter_ids))

    def scan_managed_usb(self, timeout_ms: int = 2000) -> list[ScanResult]:
        return self._run(self._async_hub.scan_managed_usb(timeout_ms))

    def start_scan(
        self,
        period_ms: int = 3000,
        *,
        adapter_id: str | None = None,
        on_device_found: Callable[[list[ScanResult]], None] | None = None,
    ) -> None:
        self._sync_method(
            self._async_hub.start_scan,
            period_ms,
            adapter_id=adapter_id,
            on_device_found=on_device_found,
        )

    def stop_scan(self) -> None:
        self._async_hub.stop_scan()

    def is_scanning(self) -> bool:
        return self._async_hub.is_scanning()

    @property
    def is_bluetooth_enabled(self) -> bool:
        return self._async_hub.is_bluetooth_enabled

    def set_bluetooth_enable_callback(self, callback: Callable[[bool], None] | None) -> None:
        self._async_hub.set_bluetooth_enable_callback(callback)

    def set_bluetooth_enabled(self, enabled: bool) -> None:
        self._async_hub.set_bluetooth_enabled(enabled)

    def configure_logging(
        self,
        *,
        enabled: bool = True,
        path: str | None = None,
        level: int = logging.DEBUG,
    ) -> None:
        self._async_hub.configure_logging(enabled=enabled, path=path, level=level)

    def connect(self, address: str, *, adapter_id: str | None = None) -> Sensor:
        async_sensor = self._run(self._async_hub.connect(address, adapter_id=adapter_id))
        return Sensor(async_sensor, self._runner)

    def get_scanned_device(self, address: str, *, adapter_id: str | None = None) -> ScanResult | None:
        return self._run(self._async_hub.get_scanned_device(address, adapter_id=adapter_id))

    def get_connected_sensor(self, address: str) -> Sensor | None:
        async_sensor = self._run(self._async_hub.get_connected_sensor(address))
        if async_sensor is None:
            return None
        return Sensor(async_sensor, self._runner)

    def get_sensor(self, address: str) -> Sensor | None:
        return self.get_connected_sensor(address)

    def list_connected_sensors(self) -> list[Sensor]:
        return [Sensor(s, self._runner) for s in self._run(self._async_hub.list_connected_sensors())]

    def list_scanned_devices(self) -> list[ScanResult]:
        return self._run(self._async_hub.list_scanned_devices())

    def disconnect(self, address: str) -> None:
        self._sync_method(self._async_hub.disconnect, address)

    def close(self) -> None:
        self._sync_method(self._async_hub.close)
        if self._runner.is_running:
            self._runner.stop()

    def __enter__(self) -> SensorHub:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


_sensor_hub: SensorHub | None = None


# Only instantiate the singleton on access
def __getattr__(name: str) -> SensorHub:
    global _sensor_hub
    if name == "sensor_hub":
        if _sensor_hub is None:
            _sensor_hub = SensorHub()
        return _sensor_hub
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
