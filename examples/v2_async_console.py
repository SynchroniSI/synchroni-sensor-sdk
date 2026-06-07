"""Console demo using synchroni_sensor_sdk (v2 async API). Mirrors examples/console.py."""

from __future__ import annotations

import asyncio

from synchroni_sensor_sdk.async_api import ScanResult, Sensor, SensorHub
from synchroni_sensor_sdk.core.data import NtfDataType, SensorData
from synchroni_sensor_sdk.core.device import DeviceState, SetParamCommand

SCAN_DEVICE_PERIOD_IN_MS = 3000
PACKAGE_COUNT = 10
POWER_REFRESH_PERIOD_IN_MS = 5000

console_params = SetParamCommand(
    enable_ntf_ecg=False,
    enable_ntf_imu=False,
    enable_filter_50hz=False,
    enable_filter_60hz=False,
    enable_filter_hpf=False,
    enable_filter_lpf=False,
)


class ConsoleSession:
    def __init__(self, hub: SensorHub) -> None:
        self.hub = hub
        self.current_sensor: Sensor | None = None
        self.current_device_name = ""

    def _sensor_label(self) -> str:
        if self.current_device_name:
            return self.current_device_name
        if self.current_sensor is not None:
            return self.current_sensor.address
        return "unknown"

    async def _device_display_name(self, address: str) -> str:
        device = await self.hub.get_scanned_device(address)
        return device.name if device is not None else address

    def on_data(self, data: SensorData) -> None:
        if data.data_type == NtfDataType.NTF_EEG and data.channel_samples:
            for sample in data.channel_samples[0]:
                print(sample.data)

    async def restart_scan(self) -> None:
        if self.current_sensor is not None:
            address = self.current_sensor.address
            await self.hub.disconnect(address)
            self.current_sensor = None

        await asyncio.sleep(2)
        if not self.hub.is_scanning():
            await self.hub.start_scan(SCAN_DEVICE_PERIOD_IN_MS, on_device_found=self.on_device_found)

    async def on_power_changed(self, power: int) -> None:
        print(f"connected sensor: {self._sensor_label()} power: {power}")
        if self.current_sensor is not None and not self.current_sensor.is_streaming():
            await self.restart_scan()

    async def on_state_changed(self, new_state: DeviceState) -> None:
        print(f"device: {self._sensor_label()}{new_state!s}")

    async def on_error(self, reason: str) -> None:
        print(f"device: {self._sensor_label()}{reason}")

    async def connect_device(self, device: ScanResult) -> None:
        print(f"found: {device.name}")
        self.current_device_name = device.name

        try:
            sensor = await self.hub.connect(device.mac_address)
        except ValueError:
            print(f"connect device: {device.name} failed (not in scan cache)")
            return

        self.current_sensor = sensor
        await sensor.register_data_callback(self.on_data)
        await sensor.register_power_callback(self.on_power_changed)
        await sensor.register_state_callback(self.on_state_changed)
        await sensor.register_error_callback(self.on_error)

        if sensor.device_state() != DeviceState.READY:
            print(f"connecting: {device.mac_address}")
            try:
                await sensor.connect()
            except Exception:
                print(f"connect device: {device.name} failed")
                return

        if sensor.device_state() == DeviceState.READY and not sensor.is_inited():
            # await sensor.set_param(SetParamCommand(debug_ble_data_path="d:/temp/test.csv"))
            await sensor.set_param(console_params)
            try:
                await sensor.init(PACKAGE_COUNT, POWER_REFRESH_PERIOD_IN_MS)
            except Exception:
                print(f"init device: {device.name} failed")
                return
            device_info = await sensor.device_info()
            print(f"deviceInfo: Model: {device_info.model}")

        if sensor.is_inited():
            print("start data transfer")
            try:
                await sensor.start_streaming()
            except Exception:
                print(f"start data transfer with device: {device.name} failed")

    async def on_devices_found(self, device_list: list[ScanResult]) -> None:
        print("stop scan")
        self.hub.stop_scan()

        filtered_devices = filter(
            lambda d: d.rssi > -80
            and (d.name.startswith("OB") or d.name.startswith("Sync") or d.name.startswith("Orion")),
            device_list,
        )
        for device in filtered_devices:
            await self.connect_device(device)
            break

    def on_device_found(self, device_list: list[ScanResult]) -> None:
        asyncio.get_running_loop().create_task(self.on_devices_found(device_list))

    async def setup(self) -> None:
        if not self.hub.is_bluetooth_enabled:
            print("please open bluetooth")
            return

        for sensor in await self.hub.list_connected_sensors():
            if sensor.is_inited():
                name = await self._device_display_name(sensor.address)
                print(f"{name} power: {await sensor.get_battery_level()}")
                await self.hub.disconnect(sensor.address)

        if not self.hub.is_scanning():
            print("start scan")
            await self.hub.start_scan(SCAN_DEVICE_PERIOD_IN_MS, on_device_found=self.on_device_found)

    async def run(self, duration_s: float = 100) -> None:
        await self.setup()
        await asyncio.sleep(duration_s)


async def main() -> None:
    async with SensorHub() as hub:
        session = ConsoleSession(hub)
        await session.run()


if __name__ == "__main__":
    asyncio.run(main())
