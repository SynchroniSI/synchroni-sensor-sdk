# 设备状态枚举
# 该枚举类定义了设备的各种状态，用于表示设备在不同操作阶段的状态信息
from enum import Enum, IntEnum
from queue import Queue
import threading
import time
from typing import Callable, Optional

import bleak
from bleak import (
    BleakClient,
)

from sensor import sensor_utils
from sensor.gforce import GForce
from sensor.sensor_data import SensorData
import asyncio


from sensor.sensor_data_context import SensorProfileDataCtx
from sensor.sensor_device import BLEDevice, DeviceInfo, DeviceStateEx
from sensor.sensor_utils import async_call, sync_call, async_exec

SERVICE_GUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
OYM_CMD_NOTIFY_CHAR_UUID = "f000ffe1-0451-4000-b000-000000000000"
OYM_DATA_NOTIFY_CHAR_UUID = "f000ffe2-0451-4000-b000-000000000000"

RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"
RFSTAR_CMD_UUID = "00000002-0000-1000-8000-00805f9b34fb"
RFSTAR_DATA_UUID = "00000003-0000-1000-8000-00805f9b34fb"


class SensorProfile:
    """
    SensorProfile 类用于蓝牙设备的连接，获取详细设备信息，初始化，数据接收。

    包含回调函数，用于处理传感器的状态变化、错误、数据接收和电量变化等事件。
    """

    def __init__(
        self,
        device: bleak.BLEDevice,
        adv: bleak.AdvertisementData,
        mac: str,
    ):
        """
        初始化 SensorProfile 类的实例。

        :param            device (BLEDevice): 蓝牙设备对象，包含设备的名称、地址和信号强度等信息。
        """
        self._detail_device = device
        self._device = BLEDevice(device.name, mac, adv.rssi)
        self._device_state = DeviceStateEx.Disconnected
        self._on_state_changed: Callable[["SensorProfile", DeviceStateEx], None] = None
        self._on_error_callback: Callable[["SensorProfile", str], None] = None
        self._on_data_callback: Callable[["SensorProfile", SensorData], None] = None
        self._on_power_changed: Callable[["SensorProfile", int], None] = None
        self._power = -1
        self._power_interval = 0
        self._adv = adv
        self._data_ctx: SensorProfileDataCtx = None
        self._gforce: GForce = None
        self._data_event_loop: asyncio.AbstractEventLoop = None
        self._data_event_thread: threading.Thread = None
        self._gforce_event_loop: asyncio.AbstractEventLoop = None
        self._gforce_event_thread: threading.Thread = None
        self._event_loop: asyncio.AbstractEventLoop = None
        self._event_thread: threading.Thread = None
        self._is_starting = False
        self._is_setting_param = False

    def __del__(self) -> None:
        """
        反初始化 SensorProfile 类的实例。

        """
        self._destroy()

    def _destroy(self):
        if self._device_state == DeviceStateEx.Connected or self._device_state == DeviceStateEx.Ready:
            self.disconnect()
        if self._data_event_loop != None:
            try:
                self._data_event_loop.stop()
                self._data_event_loop.close()
                self._data_event_loop = None
            except Exception as e:
                pass
        if self._event_loop != None:
            try:
                self._event_loop.stop()
                self._event_loop.close()
                self._event_loop = None
            except Exception as e:
                pass

        if self._gforce_event_loop != None:
            try:
                self._gforce_event_loop.stop()
                self._gforce_event_loop.close()
                self._gforce_event_loop = None
            except Exception as e:
                pass

        self._is_starting = False
        self._is_setting_param = False

    @property
    def deviceState(self) -> DeviceStateEx:
        """
        获取蓝牙连接状态。

        :return:            DeviceStateEx: 设备的状态，如 Disconnected、Connecting、Connected 等。
        """
        return self._device_state

    def _set_device_state(self, newState: DeviceStateEx):
        if self._device_state != newState:
            self._device_state = newState
            if self._event_loop != None and self._on_state_changed != None:
                try:
                    asyncio.get_event_loop().run_in_executor(None, self._on_state_changed, self, newState)
                except Exception as e:
                    raise RuntimeError("Set device state %s fail: %s" % (self.BLEDevice.Name , e))

    @property
    def hasInited(self) -> bool:
        """
        检查传感器是否已经初始化。

        :return:            bool: 如果传感器已经初始化，返回 True；否则返回 False。
        """
        if self._data_ctx == None:
            return False
        return self._data_ctx.hasInit()

    @property
    def isDataTransfering(self) -> bool:
        """
        检查传感器是否正在进行数据传输。

        :return:            bool: 如果传感器正在进行数据传输，返回 True；否则返回 False。
        """
        if self._data_ctx == None:
            return False
        return self._data_ctx.isDataTransfering

    @property
    def BLEDevice(self) -> BLEDevice:
        """
        获取传感器的蓝牙设备信息。

        :return:            BLEDevice: 蓝牙设备对象，包含设备的名称、地址和信号强度等信息。
        """
        return self._device

    @property
    def onStateChanged(self) -> Callable[["SensorProfile", DeviceStateEx], None]:
        """
        获取状态变化的回调函数。

        :return:            Callable[['SensorProfile', DeviceStateEx], None]: 状态变化的回调函数。
        """
        return self._on_state_changed

    @onStateChanged.setter
    def onStateChanged(self, callback: Callable[["SensorProfile", DeviceStateEx], None]):
        """
        设置状态变化的回调函数。

        :param            callback (Callable[['SensorProfile', DeviceStateEx], None]): 状态变化的回调函数。
        """
        self._on_state_changed = callback

    @property
    def onErrorCallback(self) -> Callable[["SensorProfile", str], None]:
        """
        获取错误回调函数。

        :return:            Callable[['SensorProfile', str], None]: 错误回调函数。
        """
        return self._on_error_callback

    @onErrorCallback.setter
    def onErrorCallback(self, callback: Callable[["SensorProfile", str], None]):
        """
        设置错误回调函数。

        :param            callback (Callable[['SensorProfile', str], None]): 错误回调函数。
        """
        self._on_error_callback = callback

    @property
    def onDataCallback(self) -> Callable[["SensorProfile", SensorData], None]:
        """
        获取数据接收的回调函数。

        :return:            Callable[['SensorProfile', SensorData], None]: 数据接收的回调函数。
        """
        return self._on_data_callback

    @onDataCallback.setter
    def onDataCallback(self, callback: Callable[["SensorProfile", SensorData], None]):
        """
        设置数据接收的回调函数。

        :param            callback (Callable[['SensorProfile', SensorData], None]): 数据接收的回调函数。
        """
        self._on_data_callback = callback

    @property
    def onPowerChanged(self) -> Callable[["SensorProfile", int], None]:
        """
        获取电量变化的回调函数。

        :return:            Callable[['SensorProfile', int], None]: 电量变化的回调函数。
        """
        return self._on_power_changed

    @onPowerChanged.setter
    def onPowerChanged(self, callback: Callable[["SensorProfile", int], None]):
        """
        设置电量变化的回调函数。

        :param            callback (Callable[['SensorProfile', int], None]): 电量变化的回调函数。
        """
        self._on_power_changed = callback

    async def _initGforce(self):

        self._gforce_event_loop = asyncio.new_event_loop()
        self._gforce_event_thread = threading.Thread(target=sensor_utils.start_loop, args=(self._gforce_event_loop,))
        self._gforce_event_thread.daemon = True
        self._gforce_event_thread.name = self._detail_device.name + "raw event"
        self._gforce_event_thread.start()

        if self._gforce == None:
            if self._adv.service_data.get(SERVICE_GUID) != None:
                # print("OYM_SERVICE:" + self._detail_device.name)
                self._gforce = GForce(
                    self._detail_device,
                    OYM_CMD_NOTIFY_CHAR_UUID,
                    OYM_DATA_NOTIFY_CHAR_UUID,
                    False,
                    self._event_loop,
                    self._gforce_event_loop,
                )
            elif self._adv.service_data.get(RFSTAR_SERVICE_GUID) != None:
                # print("RFSTAR_SERVICE:" + self._detail_device.name)
                self._gforce = GForce(
                    self._detail_device, RFSTAR_CMD_UUID, RFSTAR_DATA_UUID, True, self._event_loop, self._gforce_event_loop
                )

            else:
                raise RuntimeError("Invalid device service uuid:" + self._detail_device.name + str(self._adv))

        self._data_event_loop = asyncio.new_event_loop()
        self._data_event_thread = threading.Thread(target=sensor_utils.start_loop, args=(self._data_event_loop,))
        self._data_event_thread.daemon = True
        self._data_event_thread.name = self._detail_device.name + "data event"
        self._data_event_thread.start()

        if self._data_ctx == None and self._gforce != None:
            self._data_ctx = SensorProfileDataCtx(self._gforce, self._device.Address, self._raw_data_buf)
        if self._data_ctx.isUniversalStream:
            async_exec(self._process_universal_data(), self._data_event_loop)
        else:
            async_exec(self._process_data(), self._data_event_loop)

    async def _connect(self) -> bool:
        if sensor_utils._terminated:
            return False

        if self._event_loop == None:
            self._event_loop = asyncio.new_event_loop()
            self._event_thread = threading.Thread(target=sensor_utils.start_loop, args=(self._event_loop,))
            self._event_thread.daemon = True
            self._event_thread.name = self._detail_device.name + "event"
            self._event_thread.start()

            self._data_buffer: Queue[SensorData] = Queue()
            self._raw_data_buf: Queue[bytes] = Queue()

        if self.deviceState == DeviceStateEx.Connected or self.deviceState == DeviceStateEx.Ready:
            return True

        self._set_device_state(DeviceStateEx.Connecting)

        await async_call(self._initGforce(), runloop=self._event_loop)

        def handle_disconnect(_: BleakClient):
            if self._data_ctx != None:
                self._data_ctx.close()
                time.sleep(0.2)
                self._data_buffer.queue.clear()
                self._data_ctx = None
                self._gforce = None
            self._set_device_state(DeviceStateEx.Disconnected)
            pass

        await self._gforce.connect(handle_disconnect, self._raw_data_buf)

        if self._gforce != None and self._gforce.client.is_connected:
            self._set_device_state(DeviceStateEx.Connected)
            self._set_device_state(DeviceStateEx.Ready)
            # if self._gforce.client.mtu_size >= 80:
            #     self._set_device_state(DeviceStateEx.Ready)
            # else:
            #     self.disconnect()
        else:
            self._set_device_state(DeviceStateEx.Disconnected)

        return True

    def connect(self) -> bool:
        """
        连接传感器。

        :return:            bool: 如果连接成功，返回 True；否则返回 False。

        """
        result = sync_call(self._connect())
        return result

    async def asyncConnect(self) -> bool:
        """
        连接传感器。

        :return:            bool: 如果连接成功，返回 True；否则返回 False。

        """
        return await async_call(self._connect())

    async def _waitForDisconnect(self) -> bool:
        while not sensor_utils._terminated and self.deviceState != DeviceStateEx.Disconnected:
            await asyncio.sleep(0.1)
        return True

    async def _disconnect(self) -> bool:
        if self.deviceState != DeviceStateEx.Connected and self.deviceState != DeviceStateEx.Ready:
            return True
        if self._data_ctx == None:
            return False
        self._set_device_state(DeviceStateEx.Disconnecting)
        await self._gforce.disconnect()
        await asyncio.wait_for(self._waitForDisconnect(), sensor_utils._TIMEOUT)

        return True

    def disconnect(self) -> bool:
        """
        断开传感器连接。

        :return:            bool: 如果断开连接成功，返回 True;否则返回 False。

        """
        return sync_call(self._disconnect())

    async def asyncDisconnect(self) -> bool:
        """
        断开传感器连接。

        :return:            bool: 如果断开连接成功，返回 True;否则返回 False。

        """
        return await async_call(self._disconnect())

    async def _process_data(self):
        await self._data_ctx.process_data(self._data_buffer, self, self._on_data_callback)

    async def _process_universal_data(self):
        await self._data_ctx.processUniversalData(self._data_buffer, self, self._on_data_callback)

    async def _startDataNotification(self) -> bool:
        if self.deviceState != DeviceStateEx.Ready:
            return False
        if self._data_ctx == None:
            return False
        if not self._data_ctx.hasInit():
            return False

        if self._data_ctx.isDataTransfering:
            return True

        self._raw_data_buf.queue.clear()
        self._data_buffer.queue.clear()

        result = await async_call(self._data_ctx.start_streaming(), runloop=None)
        await asyncio.sleep(0.2)

        return result

    def startDataNotification(self) -> bool:
        """
        开始数据通知。

        :return:            bool: 如果开始数据通知成功，返回 True；否则返回 False。

        """
        if self._is_starting:
            return False

        try:
            self._is_starting = True
            ret = sync_call(self._startDataNotification())
            self._is_starting = False
            return ret
        except Exception as e:
            self._is_starting = False
            raise(e)

    async def asyncStartDataNotification(self) -> bool:
        """
        开始数据通知。

        :return:            bool: 如果开始数据通知成功，返回 True；否则返回 False。

        """
        if self._is_starting:
            return False

        try:
            self._is_starting = True
            ret = await async_call(self._startDataNotification())
            self._is_starting = False
            return ret
        except Exception as e:
            self._is_starting = False
            raise(e)

    async def _stopDataNotification(self) -> bool:
        if self.deviceState != DeviceStateEx.Ready:
            return False
        if self._data_ctx == None:
            return False
        if not self._data_ctx.hasInit():
            return False

        if not self._data_ctx.isDataTransfering:
            return True

        result = await async_call(self._data_ctx.stop_streaming(), runloop=None)
        return result

    def stopDataNotification(self) -> bool:
        """
        停止数据通知。

        :return:            bool: 如果停止数据通知成功，返回 True；否则返回 False。

        """
        if self._is_starting:
            return False

        try:
            self._is_starting = True
            ret = sync_call(self._stopDataNotification())
            self._is_starting = False
            return ret
        except Exception as e:
            self._is_starting = False
            raise(e)

    async def asyncStopDataNotification(self) -> bool:
        """
        停止数据通知。

        :return:            bool: 如果停止数据通知成功，返回 True；否则返回 False。

        """
        if self._is_starting:
            return False

        try:
            self._is_starting = True
            ret = await async_call(self._stopDataNotification())
            self._is_starting = False
            return ret
        except Exception as e:
            self._is_starting = False
            raise(e)

    async def _refresh_power(self):
        while not sensor_utils._terminated and self.deviceState == DeviceStateEx.Ready:
            await asyncio.sleep(self._power_interval / 1000)

            self._power = await self._gforce.get_battery_level()

            if not sensor_utils._terminated and self._event_loop != None and self._on_power_changed != None:
                try:
                    asyncio.get_event_loop().run_in_executor(None, self._on_power_changed, self, self._power)
                except Exception as e:
                    raise e

    async def _init(self, packageSampleCount: int, powerRefreshInterval: int) -> bool:
        if self.deviceState != DeviceStateEx.Ready:
            return False
        if self._data_ctx == None:
            return False
        if self._data_ctx.hasInit():
            return True

        if await self._data_ctx.init(packageSampleCount):
            self._power_interval = powerRefreshInterval
            self._power = await self._gforce.get_battery_level()
            sensor_utils.async_exec(self._refresh_power())

        return self._data_ctx.hasInit()

    def init(self, packageSampleCount: int, powerRefreshInterval: int) -> bool:
        """
        初始化数据采集。

        :param    packageSampleCount (int): 数据包中的样本数量。
        :param    powerRefreshInterval (int): 电量刷新间隔。

        :return:            bool: 初始化结果。True 表示成功，False 表示失败。

        """
        return sync_call(
            self._init(packageSampleCount, powerRefreshInterval),
            20,
        )

    async def asyncInit(self, packageSampleCount: int, powerRefreshInterval: int) -> bool:
        """
        初始化数据采集。

        :param    packageSampleCount (int): 数据包中的样本数量。
        :param    powerRefreshInterval (int): 电量刷新间隔。

        :return:            bool: 初始化结果。True 表示成功，False 表示失败。

        """
        return await async_call(
            self._init(packageSampleCount, powerRefreshInterval),
            20,
        )

    async def _asyncGetBatteryLevel(self) -> int:
        if self.deviceState != DeviceStateEx.Ready:
            return -1
        if self._data_ctx == None:
            return -1
        self._power = await self._gforce.get_battery_level()
        return self._power

    async def asyncGetBatteryLevel(self) -> int:
        """
        获取传感器的电池电量。

        :return:            int: 传感器的电池电量。 正常0-100，-1为未知。

        """
        return await async_call(self._asyncGetBatteryLevel())

    def getBatteryLevel(self) -> int:
        """
        获取传感器的电池电量。

        :return:            int: 传感器的电池电量。 正常0-100，-1为未知。

        """
        return self._power

    def getDeviceInfo(self) -> Optional[DeviceInfo]:
        """
        获取传感器的设备信息。

        :return:            DeviceInfo: 传感器的设备信息。

        """
        if self.hasInited:
            return self._data_ctx._device_info
        return None

    async def _asyncSet_neucir_app_control(self, open:bool, close:bool, stop:bool) -> str:
        if self.deviceState != DeviceStateEx.Ready:
            return False
        if self._data_ctx == None:
            return False
        if not self._data_ctx.hasInit():
            return False

        ret = await self._gforce.set_neucir_app_control(open, close, stop)
        if ret:
            return "OK"
        else:
            return "Error: Unknow error"
    
    async def _asyncSet_neucir_mode(self, mode:int) -> str:
        if self.deviceState != DeviceStateEx.Ready:
            return False
        if self._data_ctx == None:
            return False
        if not self._data_ctx.hasInit():
            return False

        ret = await self._gforce.set_neucir_mode(mode)
        if ret:
            return "OK"
        else:
            return "Error: Unknow error"
        
    async def _setParam(self, key: str, value: str) -> str:
        result = "Error: Not supported"
        if self.deviceState != DeviceStateEx.Ready:
            result = "Error: Please connect first"

        if key in ["NTF_EMG", "NTF_EEG", "NTF_ECG", "NTF_IMU", "NTF_BRTH", "NTF_IMPEDANCE"]:
            if value in ["ON", "OFF"]:
                self._data_ctx.init_map[key] = value
                result = "OK"

        if key in ["FILTER_50HZ", "FILTER_60HZ", "FILTER_HPF", "FILTER_LPF"]:
            if value in ["ON", "OFF"]:
                result = await self._data_ctx.setFilter(key, value)

        if key == "DEBUG_BLE_DATA_PATH":
            result = await self._data_ctx.setDebugCSV(value)

        if key == "NEUCIR_SET_MODE":
            if value in ["APP_REMOTE"]:
                if value == "APP_REMOTE":
                    result = await self._asyncSet_neucir_mode(1)

        if key == "NEUCIR_APP_CONTROL":
            if value in ["OPEN", "CLOSE", "STOP"]:
                if value == "OPEN":
                    result = await self._asyncSet_neucir_app_control(True,False,False)
                elif value == "CLOSE":
                    result = await self._asyncSet_neucir_app_control(False,True,False)
                elif value == "STOP":
                    result = await self._asyncSet_neucir_app_control(False,False,True)

        return result

    def setParam(self, key: str, value: str) -> str:
        """
        设置传感器的参数。

        :param    key (str): 参数的键。
        :param    value (str): 参数的值。

        :return:            str: 设置参数的结果。

        """
        if self._is_setting_param:
            return "Error: Please wait for the previous operation to complete"

        try:
            self._is_setting_param = True
            ret = sync_call(
                self._setParam(key, value),
                1,
            )
            self._is_setting_param = False
            return ret
        except Exception as e:
            self._is_setting_param = False
            raise(e)

    async def asyncSetParam(self, key: str, value: str) -> str:
        """
        设置传感器的参数。

        :param    key (str): 参数的键。
        :param    value (str): 参数的值。

        :return:            str: 设置参数的结果。

        """
        if self._is_setting_param:
            return "Error: Please wait for the previous operation to complete"

        try:
            self._is_setting_param = True
            ret = await async_call(
                self._setParam(key, value),
                1,
            )
            self._is_setting_param = False
            return ret
        except Exception as e:
            self._is_setting_param = False
            raise(e)
