from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from synchroni_sensor_sdk.async_api.driver.base import Driver
from synchroni_sensor_sdk.core.data import SensorData
from synchroni_sensor_sdk.core.device import BleChipType, DeviceInfo, DeviceParams, DeviceState, SetParamCommand
from synchroni_sensor_sdk.core.driver import driver_factory


class Sensor:
    """Async facade for a single sensor device.

    Device-specific protocol logic lives in a :class:`~synchroni_sensor_sdk.async_api.driver.base.Driver`
    implementation selected by :func:`~synchroni_sensor_sdk.core.driver.driver_factory`.

    **Callback task chain**

    Each ``register_*_callback`` spawns a long-lived asyncio task
    (``_handle_*_notifications``) that reads from the driver's notification
    generators and dispatches to the user callback. Tasks are replaced under
    ``_callback_lock``: the old task is cancelled and **awaited** before the
    new one starts, so two consumers never read the same buffer.

    **Lifecycle**

    ``destroy`` stops callback tasks first, then calls ``driver.destroy()`` so
    parsing and publishing cease before the outbound data buffer is closed.
    """

    def __init__(self, address: str, driver: Driver, *, adapter_id: str | None = None) -> None:
        self._address = address
        self._driver = driver
        self._adapter_id = adapter_id
        self._data_callback_task: asyncio.Task[None] | None = None
        self._power_callback_task: asyncio.Task[None] | None = None
        self._state_callback_task: asyncio.Task[None] | None = None
        self._error_callback_task: asyncio.Task[None] | None = None
        self._cached_battery: int = -1

        self._callback_lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)

    @property
    def address(self) -> str:
        return self._address

    @property
    def adapter_id(self) -> str | None:
        """Host radio used for this connection (``system:default`` or managed ``usb:…``)."""
        return self._adapter_id

    def device_state(self) -> DeviceState:
        return self._driver.device_state()

    def is_inited(self) -> bool:
        return self._driver.is_inited()

    def is_streaming(self) -> bool:
        return self._driver.is_streaming()

    def ble_chip_type(self) -> BleChipType:
        return self._driver.ble_chip_type()

    def get_params(self) -> DeviceParams:
        return self._driver.get_params()

    @property
    def dropped_data_packets(self) -> int:
        return self._driver.dropped_data_packets

    @classmethod
    async def create(
        cls,
        address: str,
        driver: Driver | None = None,
        *,
        adapter_id: str | None = None,
    ) -> Sensor:
        """Create a sensor and its protocol driver for *address*."""
        if driver is None:
            driver = driver_factory(address)
        return cls(address, driver, adapter_id=adapter_id)

    async def connect(self) -> None:
        await self._driver.connect()

    async def is_connected(self) -> bool:
        return self._driver.is_connected()

    async def disconnect(self) -> None:
        if self.is_streaming():
            await self.stop_streaming()
        await self._driver.disconnect()

    async def device_info(self) -> DeviceInfo:
        return await self._driver.device_info()

    async def get_battery_level(self) -> int:
        level = await self._driver.get_battery_level()
        if level >= 0:
            self._cached_battery = level
        return level

    def get_cached_battery_level(self) -> int:
        """Return the last known battery level without a BLE round-trip."""
        return self._cached_battery

    async def get_temperature(self) -> float:
        return await self._driver.get_temperature()

    async def init(self, package_sample_count: int, power_refresh_interval: int) -> None:
        await self._driver.init(package_sample_count, power_refresh_interval)
        self._cached_battery = await self.get_battery_level()

    async def start_streaming(self) -> None:
        await self._driver.start_streaming()

    async def stop_streaming(self) -> None:
        await self._driver.stop_streaming()

    async def _dispatch_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        """Invoke a user callback without blocking the driver event loop.

        Async callbacks run directly. Sync callbacks use ``asyncio.to_thread`` so
        slow user code does not stall BLE parsing. Do **not** call blocking
        ``Sensor`` / ``SensorHub`` methods from a sync callback — the loop is
        waiting on that thread and will deadlock.
        """
        if asyncio.iscoroutinefunction(callback):
            await callback(*args)
        else:
            await asyncio.to_thread(callback, *args)

    async def _handle_data_notifications(self, callback: Callable[[SensorData], Any]) -> None:
        """Consume :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.data_notifications`."""
        try:
            async for data in self._driver.data_notifications():
                await self._dispatch_callback(callback, data)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Error handling data notifications")

    async def _handle_power_notifications(self, callback: Callable[[int], Any]) -> None:
        """Consume :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.power_notifications`."""
        try:
            async for power in self._driver.power_notifications():
                self._cached_battery = power
                await self._dispatch_callback(callback, power)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Error handling power notifications")

    async def _handle_state_notifications(self, callback: Callable[[DeviceState], Any]) -> None:
        """Consume :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.state_notifications`."""
        try:
            async for state in self._driver.state_notifications():
                await self._dispatch_callback(callback, state)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Error handling state notifications")

    async def _handle_error_notifications(self, callback: Callable[[str], Any]) -> None:
        """Consume :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.error_notifications`."""
        try:
            async for error in self._driver.error_notifications():
                await self._dispatch_callback(callback, error)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Error handling error notifications")

    async def _await_task(self, task: asyncio.Task[None] | None) -> None:
        """Wait for a callback task to finish after cancellation."""
        if task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _cancel_callback_tasks(self) -> None:
        """Stop all callback consumer tasks before driver teardown."""
        async with self._callback_lock:
            tasks = [
                self._data_callback_task,
                self._power_callback_task,
                self._state_callback_task,
                self._error_callback_task,
            ]
            for task in tasks:
                if task is not None:
                    task.cancel()
            for task in tasks:
                await self._await_task(task)
            self._data_callback_task = None
            self._power_callback_task = None
            self._state_callback_task = None
            self._error_callback_task = None

    async def _replace_callback_task(
        self,
        current: asyncio.Task[None] | None,
        factory: Callable[[], asyncio.Task[None]],
    ) -> asyncio.Task[None]:
        """Swap one callback consumer; await the old task so buffers are not shared."""
        if current is not None:
            current.cancel()
            await self._await_task(current)
        return factory()

    async def register_data_callback(self, callback: Callable[[SensorData], Any]) -> None:
        async with self._callback_lock:
            self._data_callback_task = await self._replace_callback_task(
                self._data_callback_task,
                lambda: asyncio.create_task(self._handle_data_notifications(callback)),
            )

    async def register_power_callback(self, callback: Callable[[int], Any]) -> None:
        async with self._callback_lock:
            self._power_callback_task = await self._replace_callback_task(
                self._power_callback_task,
                lambda: asyncio.create_task(self._handle_power_notifications(callback)),
            )

    async def register_state_callback(self, callback: Callable[[DeviceState], Any]) -> None:
        async with self._callback_lock:
            self._state_callback_task = await self._replace_callback_task(
                self._state_callback_task,
                lambda: asyncio.create_task(self._handle_state_notifications(callback)),
            )

    async def register_error_callback(self, callback: Callable[[str], Any]) -> None:
        async with self._callback_lock:
            self._error_callback_task = await self._replace_callback_task(
                self._error_callback_task,
                lambda: asyncio.create_task(self._handle_error_notifications(callback)),
            )

    async def set_param(self, command: SetParamCommand) -> None:
        await self._driver.set_param(command)

    async def power_off(self) -> None:
        await self._driver.power_off()

    async def system_reset(self) -> None:
        await self._driver.system_reset()

    async def destroy(self) -> None:
        """Release callbacks then tear down the driver.

        See :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.destroy`.
        """
        await self._cancel_callback_tasks()
        await self._driver.destroy()
