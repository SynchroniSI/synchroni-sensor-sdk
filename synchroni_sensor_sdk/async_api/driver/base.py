from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from synchroni_sensor_sdk.async_api.driver.buffer import DEFAULT_DATA_BUFFER_MAXSIZE, DropOldestBuffer
from synchroni_sensor_sdk.core.data import SensorData
from synchroni_sensor_sdk.core.device import BleChipType, DeviceInfo, DeviceParams, DeviceState, SetParamCommand


class Driver(ABC):
    """
    Device-family protocol implementation (e.g. GForce).

    All methods are async and must be awaited from the caller's event loop.

    Sensor data uses a bounded :class:`DropOldestBuffer`; power, state, and error
    events use unbounded asyncio queues.

    **Publishing sensor data**

    The data buffer is owned by one asyncio event loop and is not thread-safe.
    Implementations must use two paths depending on where packets are produced:

    * :meth:`publish_data` — call from a coroutine already running on the driver's
      loop (e.g. after parsing inside an ``await`` chain).
    * :meth:`schedule_publish_data` — call from a BLE/platform callback that may
      run on another thread. This uses ``loop.call_soon_threadsafe`` to run
      :meth:`~synchroni_sensor_sdk.async_api.driver.buffer.DropOldestBuffer.publish_on_loop`
      on the loop thread.

    This is separate from the sync API wrapper, which uses
    ``asyncio.run_coroutine_threadsafe`` to invoke coroutines from the user's
    main thread. Subclasses should call :meth:`_bind_loop` from :meth:`connect`.
    """

    def __init__(self, address: str, *, data_buffer_maxsize: int = DEFAULT_DATA_BUFFER_MAXSIZE) -> None:
        self._logger = logging.getLogger(__name__)
        self._address = address
        self._loop: asyncio.AbstractEventLoop | None = None
        self._data_buffer: DropOldestBuffer[SensorData] = DropOldestBuffer(data_buffer_maxsize)
        self._power_queue: asyncio.Queue[int] = asyncio.Queue()
        self._state_queue: asyncio.Queue[DeviceState] = asyncio.Queue()
        self._error_queue: asyncio.Queue[str] = asyncio.Queue()

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        """
        Record the running event loop.

        Required for :meth:`schedule_publish_data`. Call at the start of
        :meth:`connect` while a loop is running.
        """
        self._loop = asyncio.get_running_loop()
        return self._loop

    @property
    def address(self) -> str:
        return self._address

    @property
    def dropped_data_packets(self) -> int:
        """Packets dropped because the data buffer was full."""
        return self._data_buffer.dropped

    async def publish_data(self, data: SensorData) -> None:
        """
        Enqueue sensor data, dropping the oldest packet when the buffer is full.

        Use when the caller is already on the driver's event loop. For BLE
        notification handlers that may run on a platform thread, use
        :meth:`schedule_publish_data` instead.
        """
        await self._data_buffer.publish(data)

    def schedule_publish_data(self, data: SensorData) -> None:
        """
        Enqueue sensor data from any thread.

        BLE stacks (including Bleak on some platforms) may deliver notification
        bytes on a platform or worker thread. The data buffer is not
        thread-safe, so this method schedules
        :meth:`~synchroni_sensor_sdk.async_api.driver.buffer.DropOldestBuffer.publish_on_loop`
        on the driver's loop via ``loop.call_soon_threadsafe``.

        The loop must already be bound by :meth:`connect` (through
        :meth:`_bind_loop`). Does not block the calling thread.

        This is not used by the sync API wrapper; that layer schedules whole
        coroutines with ``asyncio.run_coroutine_threadsafe`` instead.
        """
        if self._loop is None:
            raise RuntimeError("Driver event loop is not bound; call connect() first")
        self._loop.call_soon_threadsafe(self._data_buffer.publish_on_loop, data)

    def _publish_power_on_loop(self, level: int) -> None:
        self._power_queue.put_nowait(level)

    def _publish_state_on_loop(self, state: DeviceState) -> None:
        self._state_queue.put_nowait(state)

    def _publish_error_on_loop(self, message: str) -> None:
        self._error_queue.put_nowait(message)

    def schedule_publish_power(self, level: int) -> None:
        if self._loop is None:
            raise RuntimeError("Driver event loop is not bound; call connect() first")
        self._loop.call_soon_threadsafe(self._publish_power_on_loop, level)

    def schedule_publish_state(self, state: DeviceState) -> None:
        if self._loop is None:
            raise RuntimeError("Driver event loop is not bound; call connect() first")
        self._loop.call_soon_threadsafe(self._publish_state_on_loop, state)

    def schedule_publish_error(self, message: str) -> None:
        if self._loop is None:
            raise RuntimeError("Driver event loop is not bound; call connect() first")
        self._loop.call_soon_threadsafe(self._publish_error_on_loop, message)

    def set_state(self, state: DeviceState) -> None:
        """Update device state and notify state callbacks."""
        self.schedule_publish_state(state)

    async def close_data_buffer(self) -> None:
        """Stop :meth:`data_notifications` after the buffer is drained."""
        await self._data_buffer.close()

    async def data_notifications(self) -> AsyncGenerator[SensorData, None]:
        """Stream sensor data notifications."""
        async for data in self._data_buffer.consume():
            yield data

    async def power_notifications(self) -> AsyncGenerator[int, None]:
        """Stream power notifications."""
        while True:
            power = await self._power_queue.get()
            yield power

    async def state_notifications(self) -> AsyncGenerator[DeviceState, None]:
        """Stream device state notifications."""
        while True:
            state = await self._state_queue.get()
            yield state

    async def error_notifications(self) -> AsyncGenerator[str, None]:
        """Stream error notifications."""
        while True:
            error = await self._error_queue.get()
            yield error

    @abstractmethod
    def device_state(self) -> DeviceState:
        """Current device lifecycle state."""

    @abstractmethod
    def is_inited(self) -> bool:
        """Whether :meth:`init` has completed successfully."""

    @abstractmethod
    def is_streaming(self) -> bool:
        """Whether sensor data notifications are active."""

    @abstractmethod
    def ble_chip_type(self) -> BleChipType:
        """BLE chip / protocol family (OYM vs RFSTAR)."""

    @abstractmethod
    def get_params(self) -> DeviceParams:
        """Snapshot of current NTF/filter/debug parameter state."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the BLE connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the BLE connection."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return whether the BLE link is up."""

    @abstractmethod
    async def device_info(self) -> DeviceInfo:
        """Return static device capabilities."""

    @abstractmethod
    async def get_battery_level(self) -> int:
        """Return battery level 0–100, or -1 if unknown."""

    @abstractmethod
    async def get_temperature(self) -> float:
        """Return device temperature."""

    @abstractmethod
    async def init(self, package_sample_count: int, power_refresh_interval: int) -> None:
        """Negotiate capabilities and configure channels."""

    @abstractmethod
    async def start_streaming(self) -> None:
        """Begin sensor data notifications."""

    @abstractmethod
    async def stop_streaming(self) -> None:
        """Stop sensor data notifications."""

    @abstractmethod
    async def set_param(self, command: SetParamCommand) -> None:
        """Apply device configuration parameters."""

    async def power_off(self) -> None:
        """Power off the device."""
        raise NotImplementedError

    async def system_reset(self) -> None:
        """Reset the device."""
        raise NotImplementedError

    @abstractmethod
    async def destroy(self) -> None:
        """Release timers, notifications, and other resources."""
