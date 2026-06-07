from __future__ import annotations

import logging
from collections.abc import Callable

from synchroni_sensor_sdk.async_api.sensor import Sensor as AsyncSensor
from synchroni_sensor_sdk.core.data import SensorData
from synchroni_sensor_sdk.core.device import BleChipType, DeviceInfo, DeviceParams, DeviceState, SetParamCommand
from synchroni_sensor_sdk.sync_api._bridge import SyncBridge
from synchroni_sensor_sdk.sync_api.runtime import EventLoopRunner


class Sensor(SyncBridge):
    """
    Synchronous wrapper around :class:`~synchroni_sensor_sdk.async_api.sensor.Sensor`.

    All I/O methods block the calling thread while work runs on the hub's
    background event loop.
    """

    def __init__(self, async_sensor: AsyncSensor, runner: EventLoopRunner) -> None:
        super().__init__(runner)
        self._logger = logging.getLogger(__name__)
        self._async = async_sensor

    @property
    def address(self) -> str:
        return self._async.address

    def device_state(self) -> DeviceState:
        return self._async.device_state()

    def is_inited(self) -> bool:
        return self._async.is_inited()

    def is_streaming(self) -> bool:
        return self._async.is_streaming()

    def ble_chip_type(self) -> BleChipType:
        return self._async.ble_chip_type()

    def get_params(self) -> DeviceParams:
        return self._async.get_params()

    @property
    def dropped_data_packets(self) -> int:
        return self._async.dropped_data_packets

    def connect(self) -> None:
        self._sync_method(self._async.connect)

    def is_connected(self) -> bool:
        return self._sync_method(self._async.is_connected)

    def disconnect(self) -> None:
        self._sync_method(self._async.disconnect)

    def device_info(self) -> DeviceInfo:
        return self._sync_method(self._async.device_info)

    def get_battery_level(self) -> int:
        return self._sync_method(self._async.get_battery_level)

    def get_cached_battery_level(self) -> int:
        return self._async.get_cached_battery_level()

    def get_temperature(self) -> float:
        return self._sync_method(self._async.get_temperature)

    def init(self, package_sample_count: int, power_refresh_interval: int) -> None:
        self._sync_method(self._async.init, package_sample_count, power_refresh_interval)

    def start_streaming(self) -> None:
        self._sync_method(self._async.start_streaming)

    def stop_streaming(self) -> None:
        self._sync_method(self._async.stop_streaming)

    def register_data_callback(self, callback: Callable[[SensorData], None]) -> None:
        self._sync_method(self._async.register_data_callback, callback)

    def register_power_callback(self, callback: Callable[[int], None]) -> None:
        self._sync_method(self._async.register_power_callback, callback)

    def register_state_callback(self, callback: Callable[[DeviceState], None]) -> None:
        self._sync_method(self._async.register_state_callback, callback)

    def register_error_callback(self, callback: Callable[[str], None]) -> None:
        self._sync_method(self._async.register_error_callback, callback)

    def set_param(self, command: SetParamCommand) -> None:
        self._sync_method(self._async.set_param, command)

    def power_off(self) -> None:
        self._sync_method(self._async.power_off)

    def system_reset(self) -> None:
        self._sync_method(self._async.system_reset)

    def destroy(self) -> None:
        self._sync_method(self._async.destroy)
