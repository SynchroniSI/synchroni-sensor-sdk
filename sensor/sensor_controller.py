import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Callable, Dict, List, Optional, Tuple

import bleak

from sensor import sensor_profile
from sensor import sensor_utils
from sensor.sensor_profile import DeviceStateEx, SensorProfile

from sensor.sensor_utils import async_call, sync_call, async_exec
from bleak import (
    BleakScanner,
    AdvertisementData,
)

SERVICE_GUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"


class SensorController:
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not hasattr(SensorController, "_instance"):
            with SensorController._instance_lock:
                if not hasattr(SensorController, "_instance"):
                    SensorController._instance = object.__new__(cls)

        return SensorController._instance

    """
    SensorController 类的操作包括扫描蓝牙设备以及回调，创建SensorProfile等。
    """

    def __init__(self):
        """
        初始化 SensorController 实例。
        """
        self._is_scanning = False
        self._scanner: BleakScanner = None
        self._device_callback: Callable[[List[sensor_profile.BLEDevice]], None] = None
        self._device_callback_period = 0
        self._enable_callback: Callable[[bool], None] = None
        self._sensor_profiles: Dict[str, SensorProfile] = dict()

    def __del__(self) -> None:
        """
        反初始化 SensorController 类的实例。

        """

    def terminate(self):
        sensor_utils._terminated = True

        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                sensor._destroy()

        sensor_utils.Terminate()

    def _match_device(self, _device: bleak.BLEDevice, adv: AdvertisementData):
        if _device.name == None:
            return False

        if SERVICE_GUID in adv.service_uuids:
            # print("Device found: {0}, RSSI: {1}".format(_device.name, adv.rssi))
            return True

        return False

    @property
    def isScanning(self) -> bool:
        """
        检查是否正在扫描。

        :return:            bool: 是否正在扫描
        """
        return self._is_scanning

    @property
    def isEnable(self) -> bool:
        """
        检查蓝牙是否启用。

        :return:            bool: 是否启用
        """
        return True

    @isEnable.setter
    def onEnableCallback(self, callback: Callable[[bool], None]):
        """
        设置蓝牙开关变化回调，当系统蓝牙开关发生变化时调用。

        :param            callback (Callable[[bool], None]): 扫描蓝牙开关状态回调函数
        """
        self._enable_callback = callback

    @property
    def hasDeviceFoundCallback(self) -> bool:
        """
        检查是否有扫描设备回调。

        :return:            bool: 是否有设备回调
        """
        return self._device_callback != None

    @hasDeviceFoundCallback.setter
    def onDeviceFoundCallback(self, callback: Callable[[List[sensor_profile.BLEDevice]], None]):
        """
        设置扫描设备回调。

        :param            callback (Callable[[List[BLEDevice]], None]): 扫描设备回调函数
        """
        self._device_callback = callback

    def _process_ble_devices(
        self, found_devices: Dict[str, Tuple[bleak.BLEDevice, AdvertisementData]]
    ) -> List[sensor_profile.BLEDevice]:
        devices: List[sensor_profile.BLEDevice] = list()
        deviceMap: Dict[str, SensorProfile] = self._sensor_profiles.copy()
        for uuid in found_devices:
            device = found_devices[uuid][0]
            if device.name == None:
                continue
            adv = found_devices[uuid][1]
            if SERVICE_GUID in adv.service_uuids:
                mac = None
                if adv.service_data.get(SERVICE_GUID) != None:
                    bytes_val = adv.service_data[SERVICE_GUID]
                    mac = ":".join(f"{byte:02X}" for byte in bytes_val)
                elif adv.service_data.get(RFSTAR_SERVICE_GUID) != None:
                    bytes_val = adv.service_data[RFSTAR_SERVICE_GUID]
                    mac = ":".join(f"{byte:02X}" for byte in reversed(bytes_val))

                if mac == None:
                    continue
                if deviceMap.get(mac) != None:
                    devices.append(self._sensor_profiles[mac].BLEDevice)
                else:
                    newSensor = SensorProfile(device, adv, mac)
                    deviceMap[mac] = newSensor
                    devices.append(newSensor.BLEDevice)

        self._sensor_profiles = deviceMap
        return devices

    def _init_scan(self):
        if self._scanner == None:
            self._scanner = BleakScanner(
                detection_callback=self._match_device,
                service_uuids=[SERVICE_GUID, RFSTAR_SERVICE_GUID],
            )

    async def _async_scan(self, period):
        self._is_scanning = True
        self._init_scan()
        found_devices = await self._scanner.discover(timeout=period / 1000, return_adv=True)
        self._is_scanning = False
        return self._process_ble_devices(found_devices)

    def scan(self, period) -> List[sensor_profile.BLEDevice]:
        """
        扫描一段时间后返回BLEDevice列表。

        :param            periodInMs (int): 扫描时长（毫秒）

        :return:           List[sensor_profile.BLEDevice]： BLEDevice列表
        """
        return sync_call(self._async_scan(period))

    async def asyncScan(self, period) -> List[sensor_profile.BLEDevice]:
        """
        扫描一段时间后返回BLEDevice列表。

        :param            periodInMs (int): 扫描时长（毫秒）

        :return:           List[sensor_profile.BLEDevice]： BLEDevice列表
        """
        return await async_call(self._async_scan(period))

    async def _device_scan_callback(self, devices: List[sensor_profile.BLEDevice]):
        if not sensor_utils._terminated and self._device_callback:
            try:
                asyncio.get_event_loop().run_in_executor(None, self._device_callback, devices)
            except Exception as e:
                raise RuntimeError("Scan device fail: %s" % (e))

        if not sensor_utils._terminated and self._is_scanning:
            async_exec(self._startScan())

    async def _startScan(self) -> bool:
        self._init_scan()
        found_devices = await self._scanner.discover(timeout=self._device_callback_period / 1000, return_adv=True)
        devices = self._process_ble_devices(found_devices)
        async_exec(self._device_scan_callback(devices))

    def startScan(self, periodInMs: int) -> bool:
        """
        开始扫描。

        :param            periodInMs (int): 扫描时长（毫秒）

        :return:            bool: 扫描是否成功启动
        """
        if self._is_scanning:
            return True

        self._is_scanning = True
        self._device_callback_period = periodInMs

        async_exec(self._startScan())
        return True

    def stopScan(self) -> None:
        """
        停止扫描。
        """
        if not self._is_scanning:
            return

        self._is_scanning = False

    def requireSensor(self, device: sensor_profile.BLEDevice) -> Optional[SensorProfile]:
        """
        根据设备信息获取或创建SensorProfile。

        :param            device (BLEDevice): 蓝牙设备信息

        :return:            Optional[SensorProfile]: SensorProfile
        """
        if self._sensor_profiles.get(device.Address) == None:
            newSensor = SensorProfile(device)
            self._sensor_profiles[device.Address] = newSensor

        return self._sensor_profiles[device.Address]

    def getSensor(self, deviceMac: str) -> Optional[SensorProfile]:
        """
        根据设备 MAC 地址获取SensorProfile。

        :params deviceMac (str): 设备 MAC 地址

        :return:  Optional[SensorProfile]: SensorProfile
        """
        return self._sensor_profiles[deviceMac]

    def getConnectedSensors(self) -> List[SensorProfile]:
        """
        获取已连接的SensorProfile列表。

        :return:            List[SensorProfile]: 已连接的SensorProfile列表
        """
        sensors: List[SensorProfile] = list()
        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                sensors.append(sensor)

        return sensors

    def getConnectedDevices(self) -> List[sensor_profile.BLEDevice]:
        """
        获取已连接的蓝牙设备列表。

        :return:            List[BLEDevice]: 已连接的蓝牙设备列表
        """
        devices: List[sensor_profile.BLEDevice] = list()
        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                devices.append(sensor.BLEDevice)

        return devices


SensorControllerInstance = SensorController()
