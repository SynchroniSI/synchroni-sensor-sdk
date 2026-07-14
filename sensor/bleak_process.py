import asyncio
import multiprocessing
import os
import platform
import queue
import threading
import time

import bleak
from bleak import BleakScanner
from bleak import AdvertisementData

from sensor import sensor_utils
from sensor.sensor_device import BLEChipType
from sensor.sdk_log import SdkLog

_TAG = "BleakProcess"

SERVICE_GUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"

OYM_CMD_NOTIFY_CHAR_UUID = "f000ffe1-0451-4000-b000-000000000000"
OYM_DATA_NOTIFY_CHAR_UUID = "f000ffe2-0451-4000-b000-000000000000"

RFSTAR_CMD_UUID = "00000002-0000-1000-8000-00805f9b34fb"
RFSTAR_DATA_UUID = "00000003-0000-1000-8000-00805f9b34fb"


def _extract_mac(_device: bleak.BLEDevice, adv: AdvertisementData) -> str:

    mac = None
    if adv.service_data.get(SERVICE_GUID) is not None:
        bytes_val = adv.service_data[SERVICE_GUID]
        mac = ":".join(f"{byte:02X}" for byte in bytes_val)
    elif adv.service_data.get(RFSTAR_SERVICE_GUID) is not None:
        bytes_val = adv.service_data[RFSTAR_SERVICE_GUID]
        mac = ":".join(f"{byte:02X}" for byte in reversed(bytes_val))
    return mac


def _serialize_device(_device: bleak.BLEDevice, adv: AdvertisementData) -> dict:

    mac = _extract_mac(_device, adv)
    if mac is None:
        return None
    return {
        "address": _device.address,
        "name": _device.name,
        "rssi": adv.rssi,
        "mac": mac,
        "service_uuids": list(adv.service_uuids),
        "service_data": {
            k: v.hex() if isinstance(v, bytes) else v
            for k, v in adv.service_data.items()
        },
    }


def _match_device(_device: bleak.BLEDevice, adv: AdvertisementData):

    if _device.name is None:
        return False
    if SERVICE_GUID in adv.service_uuids:
        return True
    return False


class BleakProcess(multiprocessing.Process):


    def __init__(
        self,
        cmd_queue: multiprocessing.Queue,
        result_queue: multiprocessing.Queue,
        data_queue: multiprocessing.Queue,
        log_path: str = None,
    ):
        super().__init__(daemon=True)
        self.cmd_queue = cmd_queue
        self.result_queue = result_queue
        self.data_queue = data_queue
        self._log_path = log_path
        self._scanner = None
        self._is_scanning = False
        self._should_exit = False

        # Per-device state (all in sub-process)
        self._devices: dict = {}          # mac -> bleak.BLEDevice
        self._gforces: dict = {}          # mac -> GForce
        self._data_ctxs: dict = {}        # mac -> SensorProfileDataCtx
        self._raw_bufs: dict = {}         # mac -> queue.Queue
        self._device_states: dict = {}    # mac -> str state name
        self._power_intervals: dict = {}  # mac -> int (ms)
        self._data_tasks: dict = {}       # mac -> asyncio.Task
        self._battery_tasks: dict = {}    # mac -> asyncio.Task

        # Per-device event loops and threads
        self._event_loops: dict = {}      # mac -> asyncio.AbstractEventLoop
        self._event_threads: dict = {}    # mac -> threading.Thread
        self._data_event_loops: dict = {}    # mac -> asyncio.AbstractEventLoop
        self._data_event_threads: dict = {}  # mac -> threading.Thread
        self._cleanup_locks: dict = {}    # mac -> asyncio.Lock

        # Singleton gforce loop for all bleak object creation/access
        self._gforce_event_loop = None
        self._gforce_event_thread = None

    # Message types that can be dropped when the result queue is full.
    _DROPABLE_MSG_TYPES = ("sensor_data", "devices", "scan_once_result", "error")

    def _publish(self, msg_type: str, **kwargs):

        try:
            msg = {"type": msg_type, **kwargs}

            # Memory guard: prevent the internal message queue from growing without bound.
            if self._msg_queue.qsize() > sensor_utils.BLEAK_RESULT_QUEUE_MAXSIZE:
                if msg_type in self._DROPABLE_MSG_TYPES:
                    return
                # For non-droppable messages, try to evict one old droppable message.
                try:
                    old_msg = self._msg_queue.get_nowait()
                    old_type = old_msg.get("type")
                    if old_type not in self._DROPABLE_MSG_TYPES:
                        # If the oldest message is also non-droppable, put it back.
                        self._msg_queue.put_nowait(old_msg)
                except queue.Empty:
                    pass
                except Exception:
                    pass

            try:
                self._msg_queue.put_nowait(msg)
            except queue.Full:
                # If still full, use a short blocking put for important messages.
                if msg_type not in self._DROPABLE_MSG_TYPES:
                    try:
                        self._msg_queue.put(msg, timeout=1.0)
                    except queue.Full:
                        SdkLog.w(_TAG, f"Message queue still full, dropping important message: {msg_type}")
        except Exception as e:
            SdkLog.exception(_TAG, "Unexpected error")

    def _flush_msg_queue(self):

        def _route_and_put(msg):
            msg_type = msg.get("type")
            if msg_type == "sensor_data":
                try:
                    self.data_queue.put_nowait(msg)
                except queue.Full:
                    pass
            elif msg_type in self._DROPABLE_MSG_TYPES:
                self.result_queue.put_nowait(msg)
            else:
                # Use a short timeout during shutdown so the child process can exit cleanly.
                self.result_queue.put(msg, timeout=2.0)

        while True:
            try:
                msg = self._msg_queue.get_nowait()
                _route_and_put(msg)
            except queue.Empty:
                break
            except queue.Full:
                # Drop one old droppable message to make room for important messages.
                SdkLog.w(_TAG, "Result queue full, trying to drop one old droppable message")
                try:
                    old_msg = self._msg_queue.get_nowait()
                    old_type = old_msg.get("type")
                    if old_type not in self._DROPABLE_MSG_TYPES:
                        self._msg_queue.put_nowait(old_msg)
                except queue.Empty:
                    break
                except Exception:
                    break
            except Exception:
                break

    async def _publisher_task(self):
        # Throttle queue-full logs to avoid blocking stdout on Windows.
        _last_queue_full_log = 0.0
        _last_data_queue_full_log = 0.0

        while True:
            msg = None
            msg_type = None
            try:
                msg = self._msg_queue.get_nowait()
                msg_type = msg.get("type")
                if msg_type == "sensor_data":
                    # High-frequency data: use a dedicated drop-capable queue so it cannot
                    # block control/result messages in the main result queue.
                    try:
                        self.data_queue.put_nowait(msg)
                    except queue.Full:
                        now = time.time()
                        if now - _last_data_queue_full_log >= 2.0:
                            _last_data_queue_full_log = now
                            SdkLog.w(_TAG, "Data queue is full, dropping sensor_data")
                elif msg_type in self._DROPABLE_MSG_TYPES:
                    self.result_queue.put_nowait(msg)
                else:
                    # Use a short timeout for important messages so the publisher does not stall.
                    self.result_queue.put(msg, timeout=2.0)
            except queue.Empty:
                if self._should_exit:
                    break
                await asyncio.sleep(0.001)
            except queue.Full as e:
                if msg is not None:
                    if msg_type not in self._DROPABLE_MSG_TYPES:
                        # Put important messages back so they can be retried later.
                        try:
                            self._msg_queue.put_nowait(msg)
                        except queue.Full:
                            pass
                    else:
                        # Droppable messages are discarded to avoid unbounded memory growth.
                        pass
                # Log at most once every 2 seconds to prevent stdout flooding.
                now = time.time()
                if now - _last_queue_full_log >= 2.0:
                    _last_queue_full_log = now
                    SdkLog.w(_TAG, f"Result queue is full, dropping message: {e}")
                await asyncio.sleep(0.01)
            except Exception as e:
                now = time.time()
                if now - _last_queue_full_log >= 2.0:
                    _last_queue_full_log = now
                    SdkLog.e(_TAG, f"Error in publisher_task: {e}")
                await asyncio.sleep(0.001)

    def _init_scanner(self):
        if self._scanner is None:
            self._scanner = BleakScanner(
                detection_callback=_match_device,
                service_uuids=[RFSTAR_SERVICE_GUID, SERVICE_GUID],
            )

    def _ensure_gforce_loop(self):
        from sensor import sensor_utils
        if self._gforce_event_loop is None or self._gforce_event_loop.is_closed():
            self._gforce_event_loop = asyncio.new_event_loop()
            self._gforce_event_thread = threading.Thread(
                target=sensor_utils.start_loop, args=(self._gforce_event_loop,)
            )
            self._gforce_event_thread.daemon = True
            self._gforce_event_thread.name = "gforce_event"
            self._gforce_event_thread.start()
        return self._gforce_event_loop

    def _ensure_device_loops(self, device_mac: str):
        from sensor import sensor_utils

        event_loop = self._event_loops.get(device_mac)
        data_event_loop = self._data_event_loops.get(device_mac)
        if (event_loop is not None and not event_loop.is_closed() and
                data_event_loop is not None and not data_event_loop.is_closed()):
            self._cleanup_locks.setdefault(device_mac, asyncio.Lock())
            return

        event_loop = asyncio.new_event_loop()
        event_thread = threading.Thread(target=sensor_utils.start_loop, args=(event_loop,))
        event_thread.daemon = True
        event_thread.name = device_mac + "_event"
        event_thread.start()

        gforce_event_loop = self._ensure_gforce_loop()

        data_event_loop = asyncio.new_event_loop()
        data_event_thread = threading.Thread(target=sensor_utils.start_loop, args=(data_event_loop,))
        data_event_thread.daemon = True
        data_event_thread.name = device_mac + "_data_event"
        data_event_thread.start()

        self._event_loops[device_mac] = event_loop
        self._event_threads[device_mac] = event_thread
        self._data_event_loops[device_mac] = data_event_loop
        self._data_event_threads[device_mac] = data_event_thread
        self._cleanup_locks[device_mac] = asyncio.Lock()

    def _stop_device_loops(self, device_mac: str):

        # Cancel battery task if still running in event_loop
        if device_mac in self._battery_tasks:
            task = self._battery_tasks.pop(device_mac, None)
            if task:
                try:
                    loop = self._event_loops.get(device_mac)
                    if loop and not loop.is_closed():
                        loop.call_soon_threadsafe(task.cancel)
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")

        # Cancel data task first if still running in data_event_loop
        if device_mac in self._data_tasks:
            task = self._data_tasks.pop(device_mac, None)
            if task:
                try:
                    loop = self._data_event_loops.get(device_mac)
                    if loop and not loop.is_closed():
                        loop.call_soon_threadsafe(task.cancel)
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")

        # Stop loops and join threads (gforce loop is singleton, do not stop here)
        for loop_dict, thread_dict in [
            (self._data_event_loops, self._data_event_threads),
            (self._event_loops, self._event_threads),
        ]:
            loop = loop_dict.pop(device_mac, None)
            thread = thread_dict.pop(device_mac, None)
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")
            if thread is not None and thread.is_alive():
                try:
                    thread.join(timeout=2)
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")

        self._cleanup_locks.pop(device_mac, None)

    def run(self):

        if self._log_path:
            SdkLog.set_log_path(self._log_path)

        data_log_enabled = os.environ.get("SENSORSKD_DATA_LOG_ENABLED", "0") == "1"
        SdkLog.set_data_log_enabled(data_log_enabled)

        if platform.system() == "Windows":
            try:
                from bleak.backends.winrt.util import allow_sta
                allow_sta()
            except ImportError as e:
                SdkLog.exception(_TAG, "Unexpected error")


        # "cannot pickle '_thread.lock' object"
        self._msg_queue = queue.Queue()

        async def _run_main():
            publisher_task = asyncio.create_task(self._publisher_task())
            try:
                await self._main_loop()
            finally:
                self._should_exit = True

                try:
                    await asyncio.wait_for(publisher_task, timeout=2.0)
                except asyncio.TimeoutError:
                    publisher_task.cancel()
                    try:
                        await publisher_task
                    except asyncio.CancelledError as e:
                        SdkLog.exception(_TAG, "Unexpected error")

                self._flush_msg_queue()

        asyncio.run(_run_main())

        # Stop singleton gforce loop
        if self._gforce_event_loop is not None and not self._gforce_event_loop.is_closed():
            try:
                self._gforce_event_loop.call_soon_threadsafe(self._gforce_event_loop.stop)
            except Exception as e:
                SdkLog.exception(_TAG, "Unexpected error")
        if self._gforce_event_thread is not None and self._gforce_event_thread.is_alive():
            try:
                self._gforce_event_thread.join(timeout=2)
            except Exception as e:
                SdkLog.exception(_TAG, "Unexpected error")

        for device_mac in list(self._event_loops.keys()):
            self._stop_device_loops(device_mac)

    async def _main_loop(self):

        while not self._should_exit:
            try:
                cmd = self.cmd_queue.get_nowait()
                asyncio.create_task(self._handle_command(cmd))
            except multiprocessing.queues.Empty:
                await asyncio.sleep(0.05)


        await self._cleanup_all_devices()

    async def _cleanup_all_devices(self):
        for device_mac in list(self._gforces.keys()):
            loop = self._event_loops.get(device_mac)
            if loop is not None and not loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._cleanup_device(device_mac, disconnect_client=False), loop
                    )
                    await asyncio.wait_for(asyncio.wrap_future(future), timeout=3)
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")
            self._stop_device_loops(device_mac)

    async def _handle_command(self, cmd: dict):
        cmd_type = cmd.get("type")

        # Scan commands run in singleton gforce loop
        if cmd_type == "scan_once":
            loop = self._ensure_gforce_loop()
            asyncio.run_coroutine_threadsafe(
                self._do_scan_once(cmd.get("period", 5000)), loop
            )
            return
        if cmd_type == "start_scan":
            loop = self._ensure_gforce_loop()
            asyncio.run_coroutine_threadsafe(
                self._do_start_scan(cmd.get("period", 5000)), loop
            )
            return
        if cmd_type == "stop_scan":
            self._is_scanning = False
            return
        if cmd_type == "terminate":
            self._is_scanning = False
            self._should_exit = True
            return

        # Connect runs in main loop because it creates device loops
        if cmd_type == "connect":
            await self._do_connect(cmd)
            return

        # All other device commands run in the device's event_loop
        device_cmd_handlers = {
            "disconnect": self._do_disconnect,
            "init": self._do_init,
            "start_notification": self._do_start_notification,
            "stop_notification": self._do_stop_notification,
            "get_battery": self._do_get_battery,
            "get_param": self._do_get_param,
            "set_neucir_app_control": self._do_set_neucir_app_control,
            "set_neucir_mode": self._do_set_neucir_mode,
            "set_param": self._do_set_param,
        }

        if cmd_type not in device_cmd_handlers:
            return

        device_mac = cmd.get("device_mac")
        loop = self._event_loops.get(device_mac)
        if loop is None or loop.is_closed():
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Device not connected",
            )
            return

        handler = device_cmd_handlers[cmd_type]
        future = asyncio.run_coroutine_threadsafe(handler(cmd), loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=25)
        except asyncio.TimeoutError:
            SdkLog.e(_TAG, f"_handle_command timeout: {cmd_type}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Timeout",
            )
        except Exception as e:
            SdkLog.exception(_TAG, f"_handle_command failed: {cmd_type}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )

    # ------------------------------------------------------------------
    # Scan commands (existing)
    # ------------------------------------------------------------------
    async def _do_scan_once(self, period: int):

        try:
            self._init_scanner()
            found_devices = await self._scanner.discover(
                timeout=period / 1000, return_adv=True
            )
            devices = self._process_ble_devices(found_devices)
            self._publish("scan_once_result", devices=devices)
        except Exception as e:
            SdkLog.exception(_TAG, f"scan_once failed: {e}")
            self._publish("error", message=f"scan_once failed: {e}")

    async def _do_start_scan(self, period: int):

        try:
            self._init_scanner()
            found_devices = await self._scanner.discover(
                timeout=period / 1000, return_adv=True
            )
            devices = self._process_ble_devices(found_devices)
            self._publish("devices", devices=devices)
        except Exception as e:
            SdkLog.exception(_TAG, f"start_scan failed: {e}")
            self._publish("error", message=f"start_scan failed: {e}")

    def _process_ble_devices(self, found_devices: dict) -> list:

        devices = []
        for uuid in found_devices:
            device = found_devices[uuid][0]
            if device.name is None:
                continue
            adv = found_devices[uuid][1]
            if SERVICE_GUID in adv.service_uuids:
                serialized = _serialize_device(device, adv)
                if serialized is not None:
                    mac = serialized.get("mac")
                    if mac:
                        self._devices[mac] = device
                    devices.append(serialized)
        return devices

    # ------------------------------------------------------------------
    # Device connection commands
    # ------------------------------------------------------------------
    async def _do_connect(self, cmd: dict):

        device_mac = cmd["device_mac"]

        if device_mac in self._gforces:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=True,
                result=True,
            )
            return

        self._ensure_device_loops(device_mac)
        gforce_loop = self._ensure_gforce_loop()

        future = asyncio.run_coroutine_threadsafe(self._do_connect_inner(cmd), gforce_loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=25)
        except asyncio.TimeoutError:
            SdkLog.e(_TAG, f"_do_connect timeout: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Connect timeout",
            )
        except Exception as e:
            SdkLog.exception(_TAG, f"_do_connect failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )

    async def _do_connect_inner(self, cmd: dict):

        from sensor.gforce import GForce
        from sensor.sensor_data_context import SensorProfileDataCtx

        device_mac = cmd["device_mac"]
        device_address = cmd["device_address"]
        name = cmd.get("name", "")
        service_data = cmd.get("service_data", {})

        event_loop = self._event_loops.get(device_mac)
        gforce_event_loop = self._gforce_event_loop
        data_event_loop = self._data_event_loops.get(device_mac)

        if event_loop is None or gforce_event_loop is None or data_event_loop is None:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Event loops not initialized",
            )
            return

        # Determine service type and BLE chip type
        chip_type = BLEChipType.Unknown
        if service_data.get(SERVICE_GUID) is not None:
            cmd_char = OYM_CMD_NOTIFY_CHAR_UUID
            data_char = OYM_DATA_NOTIFY_CHAR_UUID
            is_universal = False
            chip_type = BLEChipType.OYM
        elif service_data.get(RFSTAR_SERVICE_GUID) is not None:
            cmd_char = RFSTAR_CMD_UUID
            data_char = RFSTAR_DATA_UUID
            is_universal = True
            chip_type = BLEChipType.RFSTAR
        else:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Invalid device service uuid",
            )
            return

        # Read bleak BLEDevice from scanned devices
        bleak_device = self._devices.get(device_mac)
        if bleak_device is None:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Device not found in scanned devices",
            )
            return

        # Create raw data buffer (local to sub-process)
        raw_buf = queue.Queue(maxsize=sensor_utils.BLEAK_RESULT_QUEUE_MAXSIZE)

        # Create GForce with per-device event loops
        gforce = GForce(bleak_device, cmd_char, data_char, is_universal, event_loop, gforce_event_loop, chip_type)

        # Define disconnect callback: schedule cleanup in event_loop
        def handle_disconnect(_):
            loop = self._event_loops.get(device_mac)
            if loop is not None and not loop.is_closed():
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._cleanup_device(device_mac, disconnect_client=False), loop
                    )
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")

        try:
            await gforce.connect(handle_disconnect, raw_buf)
        except Exception as e:
            SdkLog.exception(_TAG, f"gforce.connect failed: {device_mac}")
            await self._cleanup_device(device_mac, disconnect_client=False)
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )
            return

        if not gforce.client or not gforce.client.is_connected:
            await self._cleanup_device(device_mac, disconnect_client=False)
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Connection failed",
            )
            return

        # Store state
        self._gforces[device_mac] = gforce
        self._raw_bufs[device_mac] = raw_buf
        self._device_states[device_mac] = "Connected"

        # Create data context
        data_ctx = SensorProfileDataCtx(gforce, device_mac, raw_buf)
        self._data_ctxs[device_mac] = data_ctx

        # Start data processing loop in data_event_loop
        asyncio.run_coroutine_threadsafe(self._device_data_loop(device_mac), data_event_loop)

        # Publish states
        self._publish("state_changed", device_mac=device_mac, state="Connected")
        self._publish("state_changed", device_mac=device_mac, state="Ready")
        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=True,
            chip_type=chip_type.value,
        )

    async def _cleanup_device(self, device_mac: str, disconnect_client: bool = False):

        lock = self._cleanup_locks.get(device_mac)
        if lock is None:
            return

        async with lock:
            if device_mac not in self._gforces and not disconnect_client:
                return  # Already cleaned

            # Cancel data task (running in data_event_loop)
            if device_mac in self._data_tasks:
                task = self._data_tasks.pop(device_mac, None)
                if task is not None:
                    try:
                        task.cancel()
                    except Exception as e:
                        SdkLog.exception(_TAG, "Unexpected error")

            # Cancel battery task
            if device_mac in self._battery_tasks:
                task = self._battery_tasks.pop(device_mac, None)
                if task is not None:
                    try:
                        task.cancel()
                    except Exception as e:
                        SdkLog.exception(_TAG, "Unexpected error")

            # Stop streaming and optionally disconnect client
            if disconnect_client and device_mac in self._gforces:
                if device_mac in self._data_ctxs:
                    ctx = self._data_ctxs[device_mac]
                    if ctx.isDataTransfering:
                        try:
                            await ctx.stop_streaming()
                        except Exception as e:
                            SdkLog.exception(_TAG, "Unexpected error")
                try:
                    await self._gforces[device_mac].disconnect()
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")

            # Close data context
            if device_mac in self._data_ctxs:
                try:
                    self._data_ctxs[device_mac].close()
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")
                self._data_ctxs.pop(device_mac, None)

            # Pop state
            self._gforces.pop(device_mac, None)
            self._raw_bufs.pop(device_mac, None)
            self._device_states[device_mac] = "Disconnected"

        self._publish("state_changed", device_mac=device_mac, state="Disconnected")

    async def _do_disconnect(self, cmd: dict):

        device_mac = cmd["device_mac"]
        state = self._device_states.get(device_mac, "Disconnected")

        if state not in ("Connected", "Ready"):
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=True,
                result=True,
            )
            return

        self._device_states[device_mac] = "Disconnecting"
        self._publish("state_changed", device_mac=device_mac, state="Disconnecting")

        await self._cleanup_device(device_mac, disconnect_client=True)

        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=True,
        )

    async def _do_init(self, cmd: dict):

        device_mac = cmd["device_mac"]
        package_sample_count = cmd.get("package_sample_count", 16)
        power_refresh_interval = cmd.get("power_refresh_interval", 0)

        if device_mac not in self._data_ctxs:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Not connected",
            )
            return

        ctx = self._data_ctxs[device_mac]

        try:
            success = await ctx.init(package_sample_count)
        except Exception as e:
            SdkLog.exception(_TAG, f"_do_init failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )
            return

        if success:
            self._power_intervals[device_mac] = power_refresh_interval

            # Get initial battery
            if device_mac in self._gforces:
                try:
                    power = await self._gforces[device_mac].get_battery_level()
                    self._publish("power_changed", device_mac=device_mac, power=power)
                except Exception as e:
                    SdkLog.exception(_TAG, "Unexpected error")

            # Start battery polling task (runs in event_loop)
            if power_refresh_interval > 0:
                self._battery_tasks[device_mac] = asyncio.create_task(
                    self._battery_loop(device_mac)
                )

        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=success,
            result=success,
            device_info=ctx._device_info if success else None,
        )

    async def _battery_loop(self, device_mac: str):

        interval = self._power_intervals.get(device_mac, 0)
        while interval > 0 and device_mac in self._gforces:
            await asyncio.sleep(interval / 1000)
            try:
                power = await self._gforces[device_mac].get_battery_level()
                self._publish("power_changed", device_mac=device_mac, power=power)
            except Exception:
                break

    async def _device_data_loop(self, device_mac: str):

        from sensor.sensor_data import SensorData

        ctx = self._data_ctxs.get(device_mac)
        if ctx is None:
            return

        local_buf = queue.Queue(maxsize=sensor_utils.BLEAK_RESULT_QUEUE_MAXSIZE)

        def on_data(sensor_data: SensorData):
            self._publish("sensor_data", device_mac=device_mac, data=sensor_data)

        def on_error(message: str):
            self._publish("error", device_mac=device_mac, message=message)

        try:
            if ctx.isUniversalStream:
                await ctx._processUniversalData(local_buf, on_data, on_error)
            else:
                await ctx._process_data(local_buf, on_data, on_error)
        except asyncio.CancelledError as e:
            SdkLog.exception(_TAG, "Data loop cancelled")
        except Exception as e:
            SdkLog.exception(_TAG, "Error in data loop")

    async def _do_start_notification(self, cmd: dict):

        device_mac = cmd["device_mac"]
        if device_mac not in self._data_ctxs:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Not initialized",
            )
            return

        ctx = self._data_ctxs[device_mac]
        if not ctx.hasInit():
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Not initialized",
            )
            return

        try:
            result = await ctx.start_streaming()
        except Exception as e:
            SdkLog.exception(_TAG, f"_do_start_notification failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )
            return

        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=result,
        )

    async def _do_stop_notification(self, cmd: dict):

        device_mac = cmd["device_mac"]
        if device_mac not in self._data_ctxs:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Not initialized",
            )
            return

        ctx = self._data_ctxs[device_mac]
        try:
            result = await ctx.stop_streaming()
        except Exception as e:
            SdkLog.exception(_TAG, f"_do_stop_notification failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )
            return

        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=result,
        )

    async def _do_get_battery(self, cmd: dict):

        device_mac = cmd["device_mac"]
        if device_mac not in self._gforces:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=-1,
            )
            return

        try:
            power = await self._gforces[device_mac].get_battery_level()
        except Exception:
            SdkLog.exception(_TAG, f"_do_get_battery failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=-1,
            )
            return

        self._publish("power_changed", device_mac=device_mac, power=power)
        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=power,
        )

    async def _do_set_neucir_app_control(self, cmd: dict):

        device_mac = cmd["device_mac"]
        if device_mac not in self._gforces:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Not connected",
            )
            return

        try:
            ret = await self._gforces[device_mac].set_neucir_app_control(
                cmd.get("open", False),
                cmd.get("close", False),
                cmd.get("stop", False),
            )
        except Exception as e:
            SdkLog.exception(_TAG, f"_do_set_neucir_app_control failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )
            return

        result = "OK" if ret else "Error: Unknown error"
        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=result,
        )

    async def _do_set_neucir_mode(self, cmd: dict):

        device_mac = cmd["device_mac"]
        if device_mac not in self._gforces:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Not connected",
            )
            return

        try:
            ret = await self._gforces[device_mac].set_neucir_mode(cmd.get("mode", 0))
        except Exception as e:
            SdkLog.exception(_TAG, f"_do_set_neucir_mode failed: {device_mac}")
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result=str(e),
            )
            return

        result = "OK" if ret else "Error: Unknown error"
        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=True,
            result=result,
        )

    async def _do_get_param(self, cmd: dict):

        device_mac = cmd["device_mac"]
        key = cmd.get("key", "")

        if device_mac not in self._data_ctxs:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Error: Please connect first",
            )
            return

        ctx = self._data_ctxs[device_mac]
        if not ctx.hasInit():
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Error: Not initialized",
            )
            return

        result = "Error: Not supported"

        if key == "FILTER":
            sorted_keys = sorted(ctx.filter_map.keys())
            result = "|".join(f"{k}|{ctx.filter_map[k]}" for k in sorted_keys)

        if key == "NTF":
            sorted_keys = sorted(ctx.notify_map.keys())
            result = "|".join(f"{k}|{ctx.notify_map[k]}" for k in sorted_keys)

        if key == "DEBUG_LOG_PATH":
            result = SdkLog.get_log_path() or ""

        if key == "DEBUG_BLE_DATA_PATH":
            result = ctx._data_log_path if ctx._data_log_enabled else ""

        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=(not result.startswith("Error")),
            result=result,
        )

    async def _do_set_param(self, cmd: dict):

        device_mac = cmd["device_mac"]
        key = cmd.get("key", "")
        value = cmd.get("value", "")

        if device_mac not in self._data_ctxs:
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Error: Please connect first",
            )
            return

        ctx = self._data_ctxs[device_mac]
        if not ctx.hasInit():
            self._publish(
                "command_result",
                cmd_id=cmd.get("cmd_id"),
                device_mac=device_mac,
                success=False,
                result="Error: Not initialized",
            )
            return

        result = "Error: Not supported"
        needs_restart = False

        ntf_keys = [
            "NTF_GEST", "NTF_EMG", "NTF_EEG", "NTF_ECG", "NTF_IMU", "NTF_BRTH", "NTF_IMPEDANCE",
            "NTF_MAG_ANGLE", "NTF_PPG", "NTF_PPG_RAW", "NTF_SPO2",
            "NTF_GFORCE_EULER", "NTF_GFORCE_QUAT",
            "NTF_GFORCE_ACC", "NTF_GFORCE_GYRO",
        ]
        if key in ntf_keys:
            if value in ["ON", "OFF"]:
                # 统一 PPG 开关别名
                map_key = key
                if key == "NTF_PPG_RAW":
                    map_key = "NTF_PPG"

                # 老版本 EMG 设备上 Gesture 与 EMG 互斥，自动切换
                if not ctx.isNewEMG:
                    if map_key == "NTF_GEST" and value == "ON" and ctx.notify_map.get("NTF_EMG") == "ON":
                        ctx.notify_map["NTF_EMG"] = "OFF"
                        ctx.notify_map[map_key] = value
                        result = "OK"
                    elif map_key == "NTF_EMG" and value == "ON" and ctx.notify_map.get("NTF_GEST") == "ON":
                        ctx.notify_map["NTF_GEST"] = "OFF"
                        ctx.notify_map[map_key] = value
                        result = "OK"
                    else:
                        ctx.notify_map[map_key] = value
                        result = "OK"
                else:
                    ctx.notify_map[map_key] = value
                    result = "OK"
                if result == "OK" and ctx.hasInit() and ctx.isDataTransfering:
                    ctx._buildNotifyDataFlag()
                    needs_restart = True
                    if ctx.getChipType() == BLEChipType.OYM:
                        try:
                            await ctx.gForce.set_subscription(ctx.notifyDataFlag)
                        except Exception as e:
                            SdkLog.exception(_TAG, f"_do_set_param set_subscription failed: {device_mac}")
                            result = "ERROR: set_subscription fail: " + str(e)

        if key in ["FILTER_50HZ", "FILTER_60HZ", "FILTER_HPF", "FILTER_LPF"]:
            if value in ["ON", "OFF"]:
                try:
                    result = await ctx.setFilter(key, value)
                    if result == "OK" and ctx.hasInit() and ctx.isDataTransfering:
                        needs_restart = True
                except Exception as e:
                    SdkLog.exception(_TAG, f"_do_set_param setFilter failed: {device_mac} {key}={value}")
                    result = "ERROR: " + str(e)

        if needs_restart:
            try:
                await ctx.stop_streaming()
                await ctx.start_streaming()
            except Exception as e:
                SdkLog.exception(_TAG, f"_do_set_param restart stream failed: {device_mac}")
                result = "ERROR: restart stream fail: " + str(e)

        if key == "DEBUG_LOG_PATH":
            try:
                if value == "False" or value == "":
                    SdkLog.set_log_path("")
                elif value == "True":
                    path = SdkLog.get_default_log_path()
                    SdkLog.set_log_path(path)
                else:
                    SdkLog.set_log_path(value)
                result = "OK"
            except Exception as e:
                SdkLog.exception(_TAG, f"_do_set_param DEBUG_LOG_PATH failed: {e}")
                result = "ERROR: " + str(e)

        if key == "DEBUG_BLE_DATA_PATH":
            try:
                if value == "False" or value == "":
                    SdkLog.set_data_log_enabled(False)
                    await ctx.setDebugCSV("")
                elif value == "True":
                    SdkLog.set_data_log_enabled(True)
                    path = SdkLog.get_default_data_log_path()
                    await ctx.setDebugCSV(path)
                else:
                    SdkLog.set_data_log_enabled(True)
                    await ctx.setDebugCSV(value)
                result = "OK"
            except Exception as e:
                SdkLog.exception(_TAG, f"_do_set_param setDebugCSV failed: {device_mac} path={value}")
                result = "ERROR: " + str(e)

        if key == "NEUCIR_SET_MODE":
            if value in ["APP_REMOTE"]:
                try:
                    ret = await self._gforces[device_mac].set_neucir_mode(1)
                    result = "OK" if ret else "Error: Unknown error"
                except Exception as e:
                    SdkLog.exception(_TAG, f"_do_set_param NEUCIR_SET_MODE failed: {device_mac}")
                    result = "ERROR: " + str(e)

        if key == "NEUCIR_APP_CONTROL":
            if value in ["OPEN", "CLOSE", "STOP"]:
                try:
                    if value == "OPEN":
                        ret = await self._gforces[device_mac].set_neucir_app_control(True, False, False)
                    elif value == "CLOSE":
                        ret = await self._gforces[device_mac].set_neucir_app_control(False, True, False)
                    elif value == "STOP":
                        ret = await self._gforces[device_mac].set_neucir_app_control(False, False, True)
                    result = "OK" if ret else "Error: Unknown error"
                except Exception as e:
                    SdkLog.exception(_TAG, f"_do_set_param NEUCIR_APP_CONTROL failed: {device_mac} {value}")
                    result = "ERROR: " + str(e)

        self._publish(
            "command_result",
            cmd_id=cmd.get("cmd_id"),
            device_mac=device_mac,
            success=(not result.startswith("Error")),
            result=result,
        )
