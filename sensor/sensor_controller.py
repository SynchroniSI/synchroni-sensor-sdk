import asyncio
import threading
from collections.abc import Callable

import bleak
from bleak import (
    AdvertisementData,
    BleakScanner,
)

from sensor import sensor_profile, sensor_utils
from sensor.sensor_profile import DeviceStateEx, SensorProfile
from sensor.sensor_utils import async_call, async_exec, sync_call

SERVICE_GUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"


class SensorController:
    _instance_lock: threading.Lock = threading.Lock()

    def __new__(cls: type["SensorController"], *args: object, **kwargs: object) -> "SensorController":
        if not hasattr(SensorController, "_instance"):
            with SensorController._instance_lock:
                if not hasattr(SensorController, "_instance"):
                    SensorController._instance = object.__new__(cls)

        return SensorController._instance

    """
    SensorController 类的操作包括扫描蓝牙设备以及回调，创建SensorProfile等。
    SensorController: scan BLE devices, callbacks, and create SensorProfile instances.
    """

    def __init__(self) -> None:
        """
        初始化 SensorController 实例。 / Initialize a SensorController instance.
        """
        self._is_scanning: bool = False
        self._scanner: BleakScanner | None = None
        self._device_callback: Callable[[list[sensor_profile.BLEDevice]], None] | None = None
        self._device_callback_period: int = 0
        self._enable_callback: Callable[[bool], None] | None = None
        self._sensor_profiles: dict[str, SensorProfile] = dict()

    def __del__(self) -> None:
        """
        反初始化 SensorController 类的实例。 / Tear down the SensorController instance.
        """

    def terminate(self) -> None:
        sensor_utils._terminated = True

        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                sensor._destroy()

        sensor_utils.Terminate()

    def _match_device(self, _device: bleak.BLEDevice, adv: AdvertisementData) -> bool:
        if _device.name is None:
            return False

        if SERVICE_GUID in adv.service_uuids:
            print(f"Device found: {_device.name}, RSSI: {adv.rssi}")
            return True

        return False

    @property
    def isScanning(self) -> bool:
        """
        检查是否正在扫描。 / Check whether scanning is in progress.
        :return: bool: 是否正在扫描 / True if scanning.
        """
        return self._is_scanning

    @property
    def isEnable(self) -> bool:
        """
        检查蓝牙是否启用。 / Check whether Bluetooth is enabled.
        :return: bool: 是否启用 / True if enabled.
        """
        return True

    @isEnable.setter
    def onEnableCallback(self, callback: Callable[[bool], None]):
        """
        设置蓝牙开关变化回调，当系统蓝牙开关发生变化时调用。
        Set callback for Bluetooth enable/disable changes.

        :param callback: 蓝牙开关状态回调 / Callback(enabled: bool).
        """
        self._enable_callback = callback

    @property
    def hasDeviceFoundCallback(self) -> bool:
        """
        检查是否有扫描设备回调。 / Check whether device-found callback is set.
        :return: bool: 是否有设备回调 / True if callback is set.
        """
        return self._device_callback != None

    @hasDeviceFoundCallback.setter
    def onDeviceFoundCallback(self, callback: Callable[[list[sensor_profile.BLEDevice]], None]):
        """
        设置扫描设备回调。 / Set callback for discovered devices.
        :param callback: 扫描设备回调，接收 BLEDevice 列表 / Callback(devices: List[BLEDevice]).
        """
        self._device_callback = callback

    def _process_ble_devices(
        self, found_devices: dict[str, tuple[bleak.BLEDevice, AdvertisementData]]
    ) -> list[sensor_profile.BLEDevice]:
        devices: list[sensor_profile.BLEDevice] = list()
        deviceMap: dict[str, SensorProfile] = self._sensor_profiles.copy()
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

    def _init_scan(self) -> None:
        if self._scanner is None:
            self._scanner = BleakScanner(
                detection_callback=self._match_device,
                service_uuids=[SERVICE_GUID, RFSTAR_SERVICE_GUID],
            )

    async def _async_scan(self, period: int) -> list[sensor_profile.BLEDevice]:
        self._is_scanning = True
        self._init_scan()
        found_devices = await self._scanner.discover(timeout=period / 1000, return_adv=True)
        self._is_scanning = False
        return self._process_ble_devices(found_devices)

    def scan(self, period: int) -> list[sensor_profile.BLEDevice]:
        """
        扫描一段时间后返回 BLEDevice 列表。 / Scan for a period and return list of BLEDevice.
        :param period: 扫描时长（毫秒）/ Scan duration in milliseconds.
        :return: List[BLEDevice]: 发现的设备列表 / List of discovered devices.
        """
        return sync_call(self._async_scan(period))

    async def asyncScan(self, period: int) -> list[sensor_profile.BLEDevice]:
        """
        扫描一段时间后返回 BLEDevice 列表。 / Scan for a period and return list of BLEDevice (async).
        :param period: 扫描时长（毫秒）/ Scan duration in milliseconds.
        :return: List[BLEDevice]: 发现的设备列表 / List of discovered devices.
        """
        return await async_call(self._async_scan(period))

    async def _device_scan_callback(self, devices: list[sensor_profile.BLEDevice]) -> None:
        if not sensor_utils._terminated and self._device_callback:
            try:
                asyncio.get_event_loop().run_in_executor(None, self._device_callback, devices)
            except Exception as e:
                print(e)

        if not sensor_utils._terminated and self._is_scanning:
            async_exec(self._startScan())

    async def _startScan(self) -> None:
        self._init_scan()
        if self._scanner is None:
            return
        found_devices = await self._scanner.discover(timeout=self._device_callback_period / 1000, return_adv=True)
        devices = self._process_ble_devices(found_devices)
        async_exec(self._device_scan_callback(devices))

    def startScan(self, periodInMs: int) -> bool:
        """
        开始扫描。 / Start scanning.
        :param periodInMs: 扫描时长（毫秒）/ Scan duration in milliseconds.
        :return: bool: 是否成功启动 / True if started.
        """
        if self._is_scanning:
            return True

        self._is_scanning = True
        self._device_callback_period = periodInMs

        async_exec(self._startScan())
        return True

    def stopScan(self) -> None:
        """
        停止扫描。 / Stop scanning.
        """
        if not self._is_scanning:
            return

        self._is_scanning = False

    def requireSensor(self, device: sensor_profile.BLEDevice) -> SensorProfile | None:
        """
        根据设备信息获取或创建 SensorProfile。 / Get or create SensorProfile for a BLE device.
        :param device: 蓝牙设备信息 / BLE device info.
        :return: SensorProfile or None / The SensorProfile for this device.
        """
        if self._sensor_profiles.get(device.Address) == None:
            newSensor = SensorProfile(device)
            self._sensor_profiles[device.Address] = newSensor

        return self._sensor_profiles[device.Address]

    def getSensor(self, deviceMac: str) -> SensorProfile | None:
        """
        根据设备 MAC 地址获取 SensorProfile。 / Get SensorProfile by device MAC address.
        :param deviceMac: 设备 MAC 地址 / Device MAC address.
        :return: SensorProfile or None / The SensorProfile, or None.
        """
        return self._sensor_profiles[deviceMac]

    def getConnectedSensors(self) -> list[SensorProfile]:
        """
        获取已连接的 SensorProfile 列表。 / Get list of connected SensorProfiles.
        :return: List[SensorProfile]: 已连接的传感器列表 / List of connected sensors.
        """
        sensors: list[SensorProfile] = list()
        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                sensors.append(sensor)

        return sensors

    def getConnectedDevices(self) -> list[sensor_profile.BLEDevice]:
        """
        获取已连接的蓝牙设备列表。 / Get list of connected BLE devices.
        :return: List[BLEDevice]: 已连接的设备列表 / List of connected devices.
        """
        devices: list[sensor_profile.BLEDevice] = list()
        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                devices.append(sensor.BLEDevice)

        return devices


SensorControllerInstance = SensorController()
