"""Console demo using synchroni_sensor_sdk (v2 sync API). Mirrors examples/console.py."""

from __future__ import annotations

import queue
import signal
import time

from synchroni_sensor_sdk import SensorHub
from synchroni_sensor_sdk.async_api.sensor_hub import ScanResult
from synchroni_sensor_sdk.core.data import NtfDataType, SensorData
from synchroni_sensor_sdk.core.device import DeviceState, SetParamCommand
from synchroni_sensor_sdk.sync_api.sensor import Sensor

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
    def __init__(self) -> None:
        self.hub = SensorHub()
        self.current_sensor: Sensor | None = None
        self.current_device_name = ""
        self._pending_devices: queue.Queue[ScanResult] = queue.Queue()
        self._pending_actions: queue.Queue[str] = queue.Queue()

    def _sensor_label(self) -> str:
        if self.current_device_name:
            return self.current_device_name
        if self.current_sensor is not None:
            return self.current_sensor.address
        return "unknown"

    def _device_display_name(self, address: str) -> str:
        device = self.hub.get_scanned_device(address)
        return device.name if device is not None else address

    def on_data(self, data: SensorData) -> None:
        if data.data_type == NtfDataType.NTF_EEG and data.channel_samples:
            for sample in data.channel_samples[0]:
                print(sample.data)

    def on_power_changed(self, power: int) -> None:
        print(f"connected sensor: {self._sensor_label()} power: {power}")
        if self.current_sensor is not None and not self.current_sensor.is_streaming():
            # Do not call blocking hub/sensor methods here — sync callbacks run on a worker thread.
            self._pending_actions.put("restart")

    def on_state_changed(self, new_state: DeviceState) -> None:
        print(f"device: {self._sensor_label()}{new_state!s}")

    def on_error(self, reason: str) -> None:
        print(f"device: {self._sensor_label()}{reason}")

    def connect_device(self, device: ScanResult) -> None:
        print(f"found: {device.name}")
        self.current_device_name = device.name

        try:
            sensor = self.hub.connect(device.mac_address)
        except ValueError:
            print(f"connect device: {device.name} failed (not in scan cache)")
            return

        self.current_sensor = sensor
        sensor.register_data_callback(self.on_data)
        sensor.register_power_callback(self.on_power_changed)
        sensor.register_state_callback(self.on_state_changed)
        sensor.register_error_callback(self.on_error)

        if sensor.device_state() != DeviceState.READY:
            print(f"connecting: {device.mac_address}")
            try:
                sensor.connect()
            except Exception:
                print(f"connect device: {device.name} failed")
                return

        if sensor.device_state() == DeviceState.READY and not sensor.is_inited():
            # sensor.set_param(SetParamCommand(debug_ble_data_path="d:/temp/test.csv"))
            sensor.set_param(console_params)
            try:
                sensor.init(PACKAGE_COUNT, POWER_REFRESH_PERIOD_IN_MS)
            except Exception:
                print(f"init device: {device.name} failed")
                return
            device_info = sensor.device_info()
            print(f"deviceInfo: Model: {device_info.model}")

        if sensor.is_inited():
            print("start data transfer")
            try:
                sensor.start_streaming()
            except Exception:
                print(f"start data transfer with device: {device.name} failed")

    def on_device_found(self, device_list: list[ScanResult]) -> None:
        print("stop scan")
        self.hub.stop_scan()

        filtered_devices = filter(
            lambda d: d.rssi > -80
            and (d.name.startswith("OB") or d.name.startswith("Sync") or d.name.startswith("Orion")),
            device_list,
        )
        for device in filtered_devices:
            # Runs on the hub's background event loop — enqueue for the main thread.
            self._pending_devices.put(device)
            break

    def setup(self) -> None:
        if not self.hub.is_bluetooth_enabled:
            print("please open bluetooth")
            return

        for sensor in self.hub.list_connected_sensors():
            if sensor.is_inited():
                name = self._device_display_name(sensor.address)
                print(f"{name} power: {sensor.get_battery_level()}")
                self.hub.disconnect(sensor.address)

        if not self.hub.is_scanning():
            print("start scan")
            self.hub.start_scan(SCAN_DEVICE_PERIOD_IN_MS, on_device_found=self.on_device_found)

    def process_pending(self) -> None:
        while True:
            try:
                device = self._pending_devices.get_nowait()
            except queue.Empty:
                break
            self.connect_device(device)

        while True:
            try:
                action = self._pending_actions.get_nowait()
            except queue.Empty:
                break
            if action != "restart" or self.current_sensor is None:
                continue

            address = self.current_sensor.address
            self.hub.disconnect(address)
            self.current_sensor = None
            time.sleep(2)
            if not self.hub.is_scanning():
                self.hub.start_scan(SCAN_DEVICE_PERIOD_IN_MS, on_device_found=self.on_device_found)

    def close(self) -> None:
        self.hub.close()

    def run(self, duration_s: float = 100) -> None:
        self.setup()
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self.process_pending()
            time.sleep(0.1)


def main() -> None:
    session = ConsoleSession()

    def terminate(_signum: int | None = None, _frame: object | None = None) -> None:
        session.close()
        raise SystemExit

    signal.signal(signal.SIGINT, terminate)
    try:
        session.run()
    finally:
        session.close()


if __name__ == "__main__":
    main()
