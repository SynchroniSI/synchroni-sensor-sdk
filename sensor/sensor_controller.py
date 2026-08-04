import asyncio
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import queue as queue_module
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from sensor import sensor_profile
from sensor import sensor_utils
from sensor.bleak_host import BleakHost
from sensor.sensor_profile import DeviceStateEx, SensorProfile

from sensor.sensor_utils import async_call, sync_call, async_exec
from sensor.sdk_log import SdkLog

_TAG = "SensorController"

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

    def __init__(self):

        self._is_scanning = False
        self._device_callback: Callable[[List[sensor_profile.BLEDevice]], None] = None
        self._device_callback_period = 0
        self._enable_callback: Callable[[bool], None] = None
        self._sensor_profiles: Dict[str, SensorProfile] = dict()
        self._profiles_lock = threading.Lock()


        self._bleak_host = BleakHost()
        self._bleak_host_started = False


        self._scan_once_event = threading.Event()
        self._scan_once_result: List[dict] = None
        self._scan_once_devices: List[sensor_profile.BLEDevice] = None


        self._callback_executor = ThreadPoolExecutor(max_workers=2)

    def _ensure_bleak_host(self):

        if not self._bleak_host_started:
            self._bleak_host.start()
            self._bleak_host.on_scan_once_result = self._on_bleak_scan_once_result
            self._bleak_host.on_scan_result = self._on_bleak_scan_result
            self._bleak_host.on_device_message = self._on_bleak_device_message
            self._bleak_host_started = True

    def __del__(self) -> None:
        pass

    def terminate(self):
        if getattr(self, '_terminated', False):
            return
        self._terminated = True
        SdkLog.controller(_TAG, "terminate called")
        sensor_utils._terminated = True

        for sensor in self._sensor_profiles.values():
            if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                sensor._destroy()


        self._callback_executor.shutdown(wait=False)


        if self._bleak_host_started:
            self._bleak_host.stop()
            self._bleak_host_started = False

        sensor_utils.Terminate()

    @property
    def isScanning(self) -> bool:

        return self._is_scanning

    @property
    def isEnable(self) -> bool:

        return True

    @isEnable.setter
    def onEnableCallback(self, callback: Callable[[bool], None]):

        self._enable_callback = callback

    @property
    def hasDeviceFoundCallback(self) -> bool:

        return self._device_callback != None

    @hasDeviceFoundCallback.setter
    def onDeviceFoundCallback(self, callback: Callable[[List[sensor_profile.BLEDevice]], None]):

        self._device_callback = callback

    def _on_bleak_scan_once_result(self, msg: dict):
        serialized_devices = msg.get("devices", [])
        self._scan_once_result = serialized_devices
        devices: List[sensor_profile.BLEDevice] = list()
        with self._profiles_lock:
            deviceMap: Dict[str, SensorProfile] = self._sensor_profiles.copy()
            for serialized in serialized_devices:
                mac = serialized.get("mac")
                if mac is None:
                    continue
                if deviceMap.get(mac) is not None:
                    devices.append(self._sensor_profiles[mac].BLEDevice)
                else:
                    newSensor = SensorProfile(serialized=serialized, bleak_host=self._bleak_host)
                    deviceMap[mac] = newSensor
                    devices.append(newSensor.BLEDevice)
            self._sensor_profiles = deviceMap
        self._scan_once_devices = devices
        self._scan_once_event.set()

    def _on_bleak_scan_result(self, msg: dict):
        serialized_devices = msg.get("devices", [])
        devices: List[sensor_profile.BLEDevice] = list()
        with self._profiles_lock:
            deviceMap: Dict[str, SensorProfile] = self._sensor_profiles.copy()
            for serialized in serialized_devices:
                mac = serialized.get("mac")
                if mac is None:
                    continue
                if deviceMap.get(mac) is not None:
                    self._sensor_profiles[mac].BLEDevice.RSSI = serialized.get("rssi")
                    devices.append(self._sensor_profiles[mac].BLEDevice)
                else:
                    newSensor = SensorProfile(serialized=serialized, bleak_host=self._bleak_host)
                    deviceMap[mac] = newSensor
                    devices.append(newSensor.BLEDevice)
            self._sensor_profiles = deviceMap
        if not sensor_utils._terminated and self._device_callback:
            SdkLog.controller(_TAG, f"onDeviceFoundCallback triggered with {len(devices)} devices")
            try:
                self._callback_executor.submit(self._device_callback, devices)
            except Exception as e:
                raise RuntimeError("Scan device fail: %s" % (e))
        if not sensor_utils._terminated and self._is_scanning:
            try:
                self._bleak_host.start_scan(self._device_callback_period)
            except Exception as e:
                SdkLog.exception(_TAG, "Error restarting scan")

    def _on_bleak_device_message(self, device_mac: str, msg: dict):
        if device_mac is not None and device_mac in self._sensor_profiles:
            try:
                self._sensor_profiles[device_mac]._on_subprocess_message(msg)
            except Exception as e:
                SdkLog.exception(_TAG, f"Error handling device message for {device_mac}")

    def scan(self, period) -> List[sensor_profile.BLEDevice]:
        SdkLog.controller(_TAG, f"scan called: period={period}")
        self._ensure_bleak_host()
        self._scan_once_event.clear()
        self._scan_once_result = None
        self._bleak_host.scan_once(period)

        timeout = sensor_utils._TIMEOUT + period / 1000
        if not self._scan_once_event.wait(timeout=timeout):
            return []

        if self._scan_once_devices is None:
            return []

        return self._scan_once_devices

    async def asyncScan(self, period) -> List[sensor_profile.BLEDevice]:
        SdkLog.controller(_TAG, f"asyncScan called: period={period}")
        self._ensure_bleak_host()
        self._scan_once_event.clear()
        self._scan_once_result = None
        self._bleak_host.scan_once(period)

        timeout = sensor_utils._TIMEOUT + period / 1000
        start = time.time()
        while not self._scan_once_event.is_set() and time.time() - start < timeout:
            await asyncio.sleep(0.05)

        if self._scan_once_devices is None:
            return []

        return self._scan_once_devices

    def startScan(self, periodInMs: int) -> bool:
        SdkLog.controller(_TAG, f"startScan called: period={periodInMs}")
        if self._is_scanning:
            return True

        self._ensure_bleak_host()
        self._is_scanning = True
        self._device_callback_period = periodInMs

        self._bleak_host.start_scan(periodInMs)
        return True

    def stopScan(self) -> None:
        SdkLog.controller(_TAG, "stopScan called")
        if not self._is_scanning:
            return

        self._is_scanning = False
        try:
            self._bleak_host.stop_scan()
        except Exception as e:
            SdkLog.exception(_TAG, "Error stopping scan")

    def requireSensor(self, device: sensor_profile.BLEDevice) -> Optional[SensorProfile]:
        SdkLog.controller(_TAG, f"requireSensor called: {device.Address if device else None}")
        with self._profiles_lock:
            if self._sensor_profiles.get(device.Address) == None:
                newSensor = SensorProfile(device=device, bleak_host=self._bleak_host)
                self._sensor_profiles[device.Address] = newSensor

            return self._sensor_profiles[device.Address]

    def getSensor(self, deviceMac: str) -> Optional[SensorProfile]:
        SdkLog.controller(_TAG, f"getSensor called: {deviceMac}")
        with self._profiles_lock:
            return self._sensor_profiles.get(deviceMac)

    def getConnectedSensors(self) -> List[SensorProfile]:
        SdkLog.controller(_TAG, "getConnectedSensors called")
        sensors: List[SensorProfile] = list()
        with self._profiles_lock:
            for sensor in self._sensor_profiles.values():
                if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                    sensors.append(sensor)

        return sensors

    def getConnectedDevices(self) -> List[sensor_profile.BLEDevice]:
        SdkLog.controller(_TAG, "getConnectedDevices called")
        devices: List[sensor_profile.BLEDevice] = list()
        with self._profiles_lock:
            for sensor in self._sensor_profiles.values():
                if sensor.deviceState == DeviceStateEx.Connected or sensor.deviceState == DeviceStateEx.Ready:
                    devices.append(sensor.BLEDevice)

        return devices

    # ------------------------------------------------------------------
    # Logging controls (SdkLog is not exposed publicly)
    # ------------------------------------------------------------------
    def setDebugEnabled(self, enabled: bool):
        """开启或关闭 SDK 调试日志。"""
        SdkLog.set_debug_enabled(enabled)

    def setLogPath(self, path: Optional[str] = None):
        """设置 SDK 普通日志文件路径；传空字符串关闭文件日志。"""
        SdkLog.set_log_path(path)

    def enableFileLog(self, enabled: bool = True):
        """开启或关闭 SDK 普通文件日志。"""
        SdkLog.enable_file_log(enabled)

    def setControllerLogPath(self, path: Optional[str] = None):
        """设置 SensorController 专用日志文件路径；传空字符串关闭；默认路径为
        ~/Documents/sensorsdklog/sensor_controller_log_YYYYMMDD_HHMMSS.txt。"""
        SdkLog.set_controller_log_path(path)

    def enableControllerLog(self, enabled: bool = True):
        """开启或关闭 SensorController 专用日志。"""
        SdkLog.enable_controller_log(enabled)


SensorControllerInstance = SensorController()
