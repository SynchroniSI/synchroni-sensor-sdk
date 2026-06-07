from __future__ import annotations

import asyncio
import logging

from bleak import AdvertisementData, BLEDevice

from synchroni_sensor_sdk.async_api.driver.base import Driver
from synchroni_sensor_sdk.async_api.driver.gforce.constants import (
    OYM_CMD_NOTIFY_CHAR_UUID,
    OYM_DATA_NOTIFY_CHAR_UUID,
    RFSTAR_CMD_UUID,
    RFSTAR_DATA_UUID,
)
from synchroni_sensor_sdk.async_api.driver.gforce.data_context import DataContext
from synchroni_sensor_sdk.async_api.driver.gforce.device_info import parse_device_info_to_public
from synchroni_sensor_sdk.async_api.driver.gforce.protocol import GForceProtocol
from synchroni_sensor_sdk.core.data import SensorData
from synchroni_sensor_sdk.core.device import (
    BleChipType,
    DeviceInfo,
    DeviceParams,
    DeviceState,
    NeuCirAppControl,
    NeuCirMode,
    SetParamCommand,
)
from synchroni_sensor_sdk.core.exceptions import SensorNotInitializedError, SensorNotReadyError
from synchroni_sensor_sdk.core.params import FilterParam, NtfParam, ParamToggle


class GForceDriver(Driver):
    """BLE protocol driver for the GForce / OYMotion device family.

    **Concurrency model**

    All driver work runs on one asyncio event loop (bound in :meth:`connect`).
    Bleak may deliver notifications and disconnect callbacks on platform threads,
    so ingress uses :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.schedule_publish_data`
    and the disconnect chain below — never touch parser state directly from
    those callbacks.

    **Data path (after connect)**

    ``GForceProtocol`` notify handler
      → ``call_soon_threadsafe`` → ``_raw_queue``
      → ``_process_task`` (:meth:`DataContext.process_data` or ``process_universal_data``)
      → :meth:`_publish_parsed_data` → :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.schedule_publish_data`
      → :class:`~synchroni_sensor_sdk.async_api.driver.buffer.DropOldestBuffer`
      → :class:`~synchroni_sensor_sdk.async_api.sensor.Sensor` callback tasks

    **Background tasks**

    * ``_process_task`` — drains ``_raw_queue`` and parses packets for the
      lifetime of the connection.
    * ``_power_task`` — periodic battery refresh after :meth:`init`; started
      and replaced there so only one refresh loop runs.

    **Teardown chain**

    Manual :meth:`disconnect`, unexpected link loss (:meth:`_on_disconnect`),
    and :meth:`destroy` all funnel through :meth:`_teardown_after_link_lost`
    (serialized by ``_teardown_lock``). Order matters: stop streaming, cancel
    and **await** background tasks (so they cannot publish after close),
    disconnect BLE when the link may still be up, then close
    :class:`~synchroni_sensor_sdk.async_api.driver.gforce.data_context.DataContext`.
    :meth:`destroy` also closes the outbound data buffer after teardown.
    """

    def __init__(
        self,
        address: str,
        *,
        device: BLEDevice | None = None,
        advertisement_data: AdvertisementData | None = None,
        is_universal_stream: bool = False,
        managed_usb_transport: str | None = None,
        managed_usb_peer_address: str | None = None,
    ) -> None:
        super().__init__(address)
        self._logger = logging.getLogger(__name__)
        self._device = device
        self._advertisement_data = advertisement_data
        self._is_universal_stream = is_universal_stream
        self._managed_usb_transport = managed_usb_transport
        self._managed_usb_peer_address = managed_usb_peer_address
        self._protocol: GForceProtocol | None = None
        self._data_context: DataContext | None = None
        self._raw_queue: asyncio.Queue[bytes] | None = None
        self._process_task: asyncio.Task[None] | None = None
        self._power_task: asyncio.Task[None] | None = None
        self._teardown_lock: asyncio.Lock | None = None
        self._power_interval_ms: int = 60_000
        self._cached_battery: int = -1
        self._state = DeviceState.DISCONNECTED
        self._inited = False
        self._streaming = False

    def is_inited(self) -> bool:
        return self._inited

    def is_streaming(self) -> bool:
        return self._streaming

    def device_state(self) -> DeviceState:
        return self._state

    def ble_chip_type(self) -> BleChipType:
        if self._is_universal_stream:
            return BleChipType.RFSTAR
        if self._device is not None or self._protocol is not None:
            return BleChipType.OYM
        return BleChipType.UNKNOWN

    def get_params(self) -> DeviceParams:
        if self._data_context is None:
            return DeviceParams(ntf={}, filters={}, debug_ble_data_path=None)
        return self._data_context.get_params_snapshot()

    def _cmd_data_chars(self) -> tuple[str, str]:
        if self._is_universal_stream:
            return RFSTAR_CMD_UUID, RFSTAR_DATA_UUID
        return OYM_CMD_NOTIFY_CHAR_UUID, OYM_DATA_NOTIFY_CHAR_UUID

    def set_state(self, state: DeviceState) -> None:
        """Update cached state and publish to state-callback consumers."""
        self._state = state
        super().set_state(state)

    def _ensure_teardown_lock(self) -> asyncio.Lock:
        """Create the teardown lock on first use so it binds to the running loop."""
        if self._teardown_lock is None:
            self._teardown_lock = asyncio.Lock()
        return self._teardown_lock

    def _on_disconnect(self, _client: object) -> None:
        """Bleak disconnect callback; may run off the driver event loop.

        Must not touch ``DataContext`` or tasks here — marshal onto the loop via
        :meth:`_schedule_unexpected_disconnect` so teardown runs in the same
        thread as the parser and buffer.
        """
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._schedule_unexpected_disconnect)

    def _schedule_unexpected_disconnect(self) -> None:
        """Run on the driver loop (via ``call_soon_threadsafe``).

        ``create_task`` is required because teardown is async and cannot be
        awaited from the synchronous ``call_soon_threadsafe`` callback.
        """
        asyncio.create_task(self._handle_unexpected_disconnect())

    async def _handle_unexpected_disconnect(self) -> None:
        """Teardown when the link drops without a user-initiated :meth:`disconnect`."""
        await self._teardown_after_link_lost(call_protocol_disconnect=False)

    async def _cancel_and_await_task(self, task: asyncio.Task[None]) -> None:
        """Cancel a background task and wait for it to finish.

        Cancellation alone is not enough: a cancelled ``_process_task`` may still
        run until its next ``await`` and call :meth:`_publish_parsed_data`.
        Awaiting ensures teardown does not race with the parser or power loop.
        """
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _cancel_background_tasks(self) -> None:
        """Stop and join ``_power_task`` and ``_process_task`` before releasing context."""
        if self._power_task is not None:
            await self._cancel_and_await_task(self._power_task)
            self._power_task = None
        if self._process_task is not None:
            await self._cancel_and_await_task(self._process_task)
            self._process_task = None

    async def _teardown_after_link_lost(self, *, call_protocol_disconnect: bool) -> None:
        """Shared shutdown for manual disconnect, unexpected disconnect, and destroy.

        ``call_protocol_disconnect`` is ``True`` when the BLE client may still be
        connected (user :meth:`disconnect` / :meth:`destroy`) and ``False`` when
        Bleak already tore down the link — in the latter case use
        :meth:`DataContext.abort_streaming` instead of stop commands that would fail.

        The lock prevents concurrent teardown from a user ``disconnect`` and an
        in-flight ``_on_disconnect`` callback.
        """
        async with self._ensure_teardown_lock():
            if self._state == DeviceState.DISCONNECTED and self._protocol is None and self._data_context is None:
                return

            if self._state != DeviceState.DISCONNECTING:
                self.set_state(DeviceState.DISCONNECTING)

            if self._streaming:
                if self._data_context is not None:
                    if call_protocol_disconnect:
                        try:
                            await self._data_context.stop_streaming()
                        except Exception:
                            self._logger.warning("Failed to stop streaming during teardown", exc_info=True)
                    else:
                        self._data_context.abort_streaming()
                self._streaming = False

            await self._cancel_background_tasks()

            if call_protocol_disconnect and self._protocol is not None:
                try:
                    await self._protocol.disconnect()
                except Exception:
                    self._logger.warning("Protocol disconnect failed during teardown", exc_info=True)

            if self._data_context is not None:
                self._data_context.close()
                self._data_context = None

            self._protocol = None
            self._inited = False
            self.set_state(DeviceState.DISCONNECTED)

    def _publish_parsed_data(self, data: SensorData) -> None:
        """Bound as ``DataContext`` publish callback; safe from parser task thread context."""
        self.schedule_publish_data(data)

    def _publish_error(self, message: str) -> None:
        """Bound as ``DataContext`` error callback."""
        self.schedule_publish_error(message)

    async def connect(self) -> None:
        """Open BLE, start the parser task, and wire the raw-byte queue.

        After the protocol connects, ``_process_task`` is started immediately so
        notify data is not dropped before :meth:`start_streaming`; parsing only
        emits samples once streaming is active inside ``DataContext``.
        """
        if self._device is None:
            raise RuntimeError("BLE device handle is required to connect")
        loop = self._bind_loop()
        self.set_state(DeviceState.CONNECTING)
        cmd_char, data_char = self._cmd_data_chars()
        self._protocol = GForceProtocol(
            self._device,
            cmd_char,
            data_char,
            self._is_universal_stream,
            loop,
            managed_usb_transport=self._managed_usb_transport,
            managed_usb_peer_address=self._managed_usb_peer_address,
        )
        self._raw_queue = asyncio.Queue()
        await self._protocol.connect(self._on_disconnect, self._raw_queue)
        if self._protocol.client is None or not self._protocol.client.is_connected:
            self.set_state(DeviceState.DISCONNECTED)
            raise RuntimeError("Failed to connect to device")
        self._data_context = DataContext(
            self._protocol,
            self._address,
            self._raw_queue,
            publish_data=self._publish_parsed_data,
            on_error=self._publish_error,
        )
        if self._data_context.isUniversalStream:
            self._process_task = asyncio.create_task(self._data_context.process_universal_data())
        else:
            self._process_task = asyncio.create_task(self._data_context.process_data())
        self.set_state(DeviceState.CONNECTED)
        self.set_state(DeviceState.READY)

    async def disconnect(self) -> None:
        """Gracefully stop streaming and close the BLE link."""
        await self._teardown_after_link_lost(call_protocol_disconnect=True)

    def is_connected(self) -> bool:
        return self._protocol is not None and self._protocol.client is not None and self._protocol.client.is_connected

    async def device_info(self) -> DeviceInfo:
        if self._data_context is None or self._data_context._device_info is None:
            return DeviceInfo(model="", hardware_version="", firmware_version="", channel_counts={})
        return parse_device_info_to_public(self._data_context._device_info)

    async def get_battery_level(self) -> int:
        if not self.is_connected() or self._protocol is None:
            return -1
        if self._cached_battery >= 0:
            return self._cached_battery
        level = await self._protocol.get_battery_level()
        self._cached_battery = level
        return level

    async def get_temperature(self) -> float:
        if not self.is_connected() or self._protocol is None:
            return 0.0
        return float(await self._protocol.get_temperature())

    async def _refresh_power(self) -> None:
        """Background battery polling loop started by :meth:`init`.

        Exits when state leaves :attr:`DeviceState.READY` or teardown clears
        ``_protocol``. Publishes via :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.schedule_publish_power`
        so battery callbacks stay on the driver loop.
        """
        while self._state == DeviceState.READY and self._protocol is not None:
            await asyncio.sleep(self._power_interval_ms / 1000)
            if self._protocol is None:
                break
            try:
                self._cached_battery = await self._protocol.get_battery_level()
                self.schedule_publish_power(self._cached_battery)
            except Exception:
                self._logger.exception("Failed to refresh battery level")

    async def init(self, package_sample_count: int, power_refresh_interval: int) -> None:
        """Probe channels and start ``_power_task``.

        Any existing power task is cancelled and awaited first so ``init`` can
        be retried without two refresh loops running.
        """
        if self._data_context is None:
            raise SensorNotReadyError("Cannot init: device is not connected.")
        self._power_interval_ms = power_refresh_interval
        await self._data_context.init(package_sample_count)
        self._inited = True
        self._cached_battery = await self.get_battery_level()
        if self._power_task is not None:
            await self._cancel_and_await_task(self._power_task)
            self._power_task = None
        self._power_task = asyncio.create_task(self._refresh_power())

    async def start_streaming(self) -> None:
        if self._data_context is None or not self._inited:
            raise SensorNotInitializedError("Cannot start streaming: sensor has not been initialized.")
        await self._data_context.start_streaming()
        self._streaming = True

    async def stop_streaming(self) -> None:
        if self._data_context is None:
            return
        await self._data_context.stop_streaming()
        self._streaming = False

    async def set_param(self, command: SetParamCommand) -> None:
        if self._data_context is None:
            raise SensorNotReadyError("Cannot set parameters: device was not connected.")

        ctx = self._data_context
        ntf_changed = False

        def _set_ntf(key: NtfParam, enabled: bool) -> None:
            nonlocal ntf_changed
            ctx.init_map[key] = ParamToggle.from_bool(enabled)
            ntf_changed = True

        if command.enable_ntf_emg is not None:
            _set_ntf(NtfParam.NTF_EMG, command.enable_ntf_emg)
        if command.enable_ntf_eeg is not None:
            _set_ntf(NtfParam.NTF_EEG, command.enable_ntf_eeg)
        if command.enable_ntf_ecg is not None:
            _set_ntf(NtfParam.NTF_ECG, command.enable_ntf_ecg)
        if command.enable_ntf_brth is not None:
            _set_ntf(NtfParam.NTF_BRTH, command.enable_ntf_brth)
        if command.enable_ntf_impedance is not None:
            _set_ntf(NtfParam.NTF_IMPEDANCE, command.enable_ntf_impedance)
        if command.enable_ntf_mag_angle is not None:
            _set_ntf(NtfParam.NTF_MAG_ANGLE, command.enable_ntf_mag_angle)
        if command.enable_ntf_gest is not None:
            _set_ntf(NtfParam.NTF_GEST, command.enable_ntf_gest)
        if command.enable_ntf_ppg is not None:
            _set_ntf(NtfParam.NTF_PPG, command.enable_ntf_ppg)
        if command.enable_ntf_spo2 is not None:
            _set_ntf(NtfParam.NTF_SPO2, command.enable_ntf_spo2)

        if command.enable_ntf_imu is not None:
            ctx.apply_imu_master(command.enable_ntf_imu)
            ntf_changed = True
        else:
            sub_changed = False
            if command.enable_ntf_acc is not None:
                _set_ntf(NtfParam.NTF_GFORCE_ACC, command.enable_ntf_acc)
                sub_changed = True
            if command.enable_ntf_gyro is not None:
                _set_ntf(NtfParam.NTF_GFORCE_GYRO, command.enable_ntf_gyro)
                sub_changed = True
            if command.enable_ntf_euler is not None:
                _set_ntf(NtfParam.NTF_GFORCE_EULER, command.enable_ntf_euler)
                sub_changed = True
            if command.enable_ntf_quat is not None:
                _set_ntf(NtfParam.NTF_GFORCE_QUAT, command.enable_ntf_quat)
                sub_changed = True
            if sub_changed:
                ctx.sync_imu_master_from_subs()

        # Legacy mutual exclusion for old EMG when both EMG and GEST are on.
        if ntf_changed and not ctx.isNewEMG and ctx._ntf_on(NtfParam.NTF_EMG) and ctx._ntf_on(NtfParam.NTF_GEST):
            if command.enable_ntf_gest is True:
                ctx.init_map[NtfParam.NTF_EMG] = ParamToggle.OFF
            elif command.enable_ntf_emg is True:
                ctx.init_map[NtfParam.NTF_GEST] = ParamToggle.OFF

        if command.enable_filter_50hz is not None:
            await ctx.setFilter(FilterParam.FILTER_50HZ, ParamToggle.from_bool(command.enable_filter_50hz))
        if command.enable_filter_60hz is not None:
            await ctx.setFilter(FilterParam.FILTER_60HZ, ParamToggle.from_bool(command.enable_filter_60hz))
        if command.enable_filter_hpf is not None:
            await ctx.setFilter(FilterParam.FILTER_HPF, ParamToggle.from_bool(command.enable_filter_hpf))
        if command.enable_filter_lpf is not None:
            await ctx.setFilter(FilterParam.FILTER_LPF, ParamToggle.from_bool(command.enable_filter_lpf))

        if command.debug_ble_data_path is not None:
            await ctx.setDebugCSV(command.debug_ble_data_path)

        if (command.neucir_mode is not None or command.neucir_app_control is not None) and not self._inited:
            raise SensorNotInitializedError("Cannot set NeuCir parameters: sensor has not been initialized.")

        if (
            command.neucir_mode is not None
            and self._protocol is not None
            and command.neucir_mode == NeuCirMode.APP_REMOTE
        ):
            await self._protocol.set_neucir_mode(1)

        if command.neucir_app_control is not None and self._protocol is not None:
            control = command.neucir_app_control
            await self._protocol.set_neucir_app_control(
                control == NeuCirAppControl.OPEN,
                control == NeuCirAppControl.CLOSE,
                control == NeuCirAppControl.STOP,
            )

        # After init, push NTF map to device; restart stream if active.
        if ntf_changed and self._inited:
            was_streaming = self._streaming
            if was_streaming:
                await self.stop_streaming()
            await ctx.apply_subscription()
            if was_streaming:
                await self.start_streaming()

    async def power_off(self) -> None:
        if self._protocol is not None:
            await self._protocol.power_off()

    async def system_reset(self) -> None:
        if self._protocol is not None:
            await self._protocol.system_reset()

    async def destroy(self) -> None:
        """Full driver shutdown: teardown connection then close the data buffer.

        Called from :meth:`~synchroni_sensor_sdk.async_api.sensor.Sensor.destroy`
        after callback tasks are stopped, so no consumer is reading the buffer
        when it is closed.
        """
        if self.is_connected() or self._protocol is not None or self._data_context is not None:
            await self._teardown_after_link_lost(call_protocol_disconnect=self.is_connected())
        await self.close_data_buffer()
