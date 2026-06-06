import asyncio
import csv
import platform
import struct
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, IntEnum
from queue import Queue

from sensor import sensor_utils
from sensor.exceptions import (
    DataContextInitError,
    DataContextInitInProgressError,
    DataContextNotTransferringError,
    DataContextReadSamplesError,
    DataContextStopStreamingError,
    DataNotificationInProgressError,
)
from sensor.gforce import DataSubscription, GForce, SamplingRate
from sensor.sensor_data import DataType, Sample, SensorData
from sensor.sensor_device import DeviceInfo


class SensorDataType(IntEnum):
    DATA_TYPE_EEG = 0
    DATA_TYPE_ECG = 1
    DATA_TYPE_ACC = 2
    DATA_TYPE_GYRO = 3
    DATA_TYPE_BRTH = 4
    DATA_TYPE_EMG = 5
    DATA_TYPE_MAG_ANGLE = 6
    DATA_TYPE_COUNT = 7


# 枚举 FeatureMaps 的 Python 实现 / Python implementation of FeatureMaps enum (feature flags).
class FeatureMaps(Enum):
    GFD_FEAT_EMG = 0x000002000
    GFD_FEAT_MAGANG = 0x00080000
    GFD_FEAT_EEG = 0x000400000
    GFD_FEAT_ECG = 0x000800000
    GFD_FEAT_IMPEDANCE = 0x001000000
    GFD_FEAT_IMU = 0x002000000
    GFD_FEAT_ADS = 0x004000000
    GFD_FEAT_BRTH = 0x008000000
    GFD_FEAT_CONCAT_BLE = 0x80000000


class SensorProfileDataCtx:
    def __init__(self, gForce: GForce, deviceMac: str, buf: Queue[bytes]) -> None:
        self.featureMap: int = 0
        self.notifyDataFlag: DataSubscription = 0

        self.gForce: GForce = gForce
        self.deviceMac: str = deviceMac
        self._device_info: DeviceInfo | None = None

        self._is_initing: bool = False
        self._is_running: bool = True
        self._is_data_transfering: bool = False
        self.isUniversalStream: bool = gForce._is_universal_stream
        self._rawDataBuffer: Queue[bytes] = buf
        self._concatDataBuffer: bytearray = bytearray()

        self.sensorDatas: list[SensorData] = list()
        for idx in range(0, SensorDataType.DATA_TYPE_COUNT):
            self.sensorDatas.append(SensorData())
        self.impedanceData: list[float] = list()
        self.saturationData: list[float] = list()
        self.dataPool: ThreadPoolExecutor = ThreadPoolExecutor(1, "data")
        self.init_map: dict[str, str] = {
            "NTF_EMG": "ON",
            "NTF_EEG": "ON",
            "NTF_ECG": "ON",
            "NTF_IMU": "ON",
            "NTF_BRTH": "ON",
            "NTF_MAG_ANGLE": "ON",
            "NTF_IMPEDANCE": "ON",
        }
        self.filter_map: dict[str, str] = {
            "FILTER_50HZ": "ON",
            "FILTER_60HZ": "ON",
            "FILTER_HPF": "ON",
            "FILTER_LPF": "ON",
        }
        self.debugCSVWriter: object | None = None
        self.debugCSVPath: str | None = None

    def close(self) -> None:
        self._is_running = False
        if self.debugCSVWriter is not None:
            self.debugCSVWriter = None

    def clear(self) -> None:
        for sensorData in self.sensorDatas:
            sensorData.clear()
        self.impedanceData.clear()
        self.saturationData.clear()
        self._concatDataBuffer.clear()
        self._rawDataBuffer.queue.clear()

    def reset(self) -> None:
        self.notifyDataFlag = 0
        self.clear()

    @property
    def isDataTransfering(self) -> bool:
        """
        检查传感器是否正在进行数据传输。
        Check whether data transfer is in progress.

        :return: bool: 正在传输为 True，否则 False / True if transferring, False otherwise.
        """
        return self._is_data_transfering

    def hasInit(self) -> bool:
        return not self._is_initing and self.featureMap != 0 and self.notifyDataFlag != 0

    def hasEMG(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_EMG.value) != 0

    def hasEEG(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_EEG.value) != 0

    def hasECG(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_ECG.value) != 0

    def hasImpedance(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_IMPEDANCE.value) != 0

    def hasIMU(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_IMU.value) != 0

    def hasBrth(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_BRTH.value) != 0

    def hasMagAngle(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_MAGANG.value) != 0

    def hasConcatBLE(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_CONCAT_BLE.value) != 0

    async def initEMG(self, packageCount: int) -> int:
        config = await self.gForce.get_emg_raw_data_config()
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_EMG
        data.sampleRate = 500
        data.resolutionBits = 0
        data.channelCount = 8
        data.channelMask = config.channel_mask
        data.minPackageSampleCount = packageCount
        data.packageSampleCount = 8

        data.clear()
        isNewEMG = True
        try:
            if (
                self._device_info.DeviceName.startswith("gForce")
                or self._device_info.DeviceName.startswith("OHand")
                or self._device_info.DeviceName.startswith("ORE-")
                or self._device_info.DeviceName.startswith("OYEM-")
                or self._device_info.DeviceName.startswith("ORehab")
            ):
                isNewEMG = False
        except Exception:
            pass

        if isNewEMG:
            # new emg
            data.packageIndexLength = 2
            data.packageSampleCount = 8
            data.resolutionBits = 0
            gain = 6
            data.K = 4000000.0 / 8388607.0 / gain
            config.resolution = 8
        else:
            # old emg
            data.packageIndexLength = 1
            data.packageSampleCount = 8
            data.resolutionBits = 7
            gain = 1200
            min_voltage = -1.25 * 1000000
            max_voltage = 1.25 * 100000
            div = 127.0
            conversion_factor = (max_voltage - min_voltage) / gain / div
            data.K = conversion_factor
            config.resolution = 8

        config.fs = SamplingRate.HZ_500
        config.channel_mask = 255
        config.batch_len = 128

        if isNewEMG:
            await self.gForce.set_function_switch(2)

        await self.gForce.set_emg_raw_data_config(config)
        await self.gForce.set_package_id(True)

        self.sensorDatas[SensorDataType.DATA_TYPE_EMG] = data
        self.notifyDataFlag |= DataSubscription.EMG_RAW

        return data.channelCount

    async def initEEG(self, packageCount: int) -> int:
        config = await self.gForce.get_eeg_raw_data_config()
        cap = await self.gForce.get_eeg_raw_data_cap()
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_EEG
        data.sampleRate = config.fs
        data.resolutionBits = config.resolution
        data.channelCount = cap.channel_count
        data.channelMask = config.channel_mask
        data.minPackageSampleCount = packageCount
        data.packageSampleCount = config.batch_len
        data.K = config.K
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_EEG] = data
        self.notifyDataFlag |= DataSubscription.DNF_EEG
        return data.channelCount

    async def initECG(self, packageCount: int) -> int:
        config = await self.gForce.get_ecg_raw_data_config()
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_ECG
        data.sampleRate = config.fs
        data.resolutionBits = config.resolution
        data.channelCount = 1
        data.channelMask = config.channel_mask
        data.minPackageSampleCount = packageCount
        data.packageSampleCount = config.batch_len
        data.K = config.K
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_ECG] = data
        self.notifyDataFlag |= DataSubscription.DNF_ECG
        return data.channelCount

    async def initIMU(self, packageCount: int) -> int:
        config = await self.gForce.get_imu_raw_data_config()
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_ACC
        data.sampleRate = config.fs
        data.resolutionBits = 16
        data.channelCount = config.channel_count
        data.channelMask = 255
        data.minPackageSampleCount = 1
        data.packageSampleCount = config.batch_len
        data.K = config.accK
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_ACC] = data

        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_GYRO
        data.sampleRate = config.fs
        data.resolutionBits = 16
        data.channelCount = config.channel_count
        data.channelMask = 255
        data.minPackageSampleCount = 1
        data.packageSampleCount = config.batch_len
        data.K = config.gyroK
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_GYRO] = data

        self.notifyDataFlag |= DataSubscription.DNF_IMU

        return data.channelCount

    async def initBrth(self, packageCount: int) -> int:
        config = await self.gForce.get_brth_raw_data_config()
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_BRTH
        data.sampleRate = config.fs
        data.resolutionBits = config.resolution
        data.channelCount = 1
        data.channelMask = config.channel_mask
        data.minPackageSampleCount = packageCount
        data.packageSampleCount = config.batch_len
        data.K = config.K
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_BRTH] = data
        self.notifyDataFlag |= DataSubscription.DNF_ECG
        return data.channelCount

    async def initMagAngle(self, packageCount: int) -> int:
        await self.gForce.set_package_id(True)

        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_MAG_ANGLE_DATA
        data.sampleRate = 40
        data.resolutionBits = 8
        data.channelCount = 1
        data.channelMask = 1
        data.minPackageSampleCount = 1
        data.packageSampleCount = 1
        data.K = 1
        data.packageIndexLength = 2
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_MAG_ANGLE] = data
        self.notifyDataFlag |= DataSubscription.DNF_MAG_ANGLE_EXT
        return data.channelCount

    async def initDataTransfer(self, isGetFeature: bool) -> int:
        if isGetFeature:
            self.featureMap = await self.gForce.get_feature_map()
            if self.hasImpedance():
                self.notifyDataFlag |= DataSubscription.DNF_IMPEDANCE
            return self.featureMap
        else:
            await self.gForce.set_subscription(self.notifyDataFlag)
            return self.notifyDataFlag

    async def fetchDeviceInfo(self) -> DeviceInfo:
        info = DeviceInfo()
        if platform.system() != "Linux":
            info.MTUSize = self.gForce.client.mtu_size
        else:
            info.MTUSize = 0
        # print("get_device_name")
        info.DeviceName = await self.gForce.get_device_name()
        # print("get_model_number")
        info.ModelName = await self.gForce.get_model_number()
        # print("get_hardware_revision")
        info.HardwareVersion = await self.gForce.get_hardware_revision()
        # print("get_firmware_revision")
        info.FirmwareVersion = await self.gForce.get_firmware_revision()
        return info

    async def init(self, packageCount: int) -> bool:
        if self._is_initing:
            raise DataContextInitInProgressError("Data context init already in progress.")
        try:
            self._is_initing = True
            info = await self.fetchDeviceInfo()
            self._device_info = info
            await self.initDataTransfer(True)

            if self.hasConcatBLE():
                self.notifyDataFlag |= DataSubscription.DNF_CONCAT_BLE

            if self.hasImpedance() and (self.init_map["NTF_IMPEDANCE"] == "ON"):
                self.notifyDataFlag |= DataSubscription.DNF_IMPEDANCE

            if self.hasEMG() and (self.init_map["NTF_EMG"] == "ON"):
                info.EmgChannelCount = await self.initEMG(packageCount)
                info.EmgSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_EMG].sampleRate

            if self.hasEEG() and (self.init_map["NTF_EEG"] == "ON"):
                info.EegChannelCount = await self.initEEG(packageCount)
                info.EegSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_EEG].sampleRate

            if self.hasECG() and (self.init_map["NTF_ECG"] == "ON"):
                info.EcgChannelCount = await self.initECG(packageCount)
                info.EcgSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_ECG].sampleRate

            if self.hasBrth() and (self.init_map["NTF_BRTH"] == "ON"):
                info.BrthChannelCount = await self.initBrth(packageCount)
                info.BrthSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_BRTH].sampleRate

            if self.hasIMU() and (self.init_map["NTF_IMU"] == "ON"):
                imuChannelCount = await self.initIMU(packageCount)
                info.AccChannelCount = imuChannelCount
                info.GyroChannelCount = imuChannelCount
                info.AccSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_ACC].sampleRate
                info.GyroSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_GYRO].sampleRate

            if self.hasMagAngle() and (self.init_map["NTF_MAG_ANGLE"] == "ON"):
                magAngleChannelCount = await self.initMagAngle(packageCount)
                info.MagAngleChannelCount = magAngleChannelCount
                info.MagAngleSampleRate = self.sensorDatas[SensorDataType.DATA_TYPE_MAG_ANGLE].sampleRate

            self._device_info = info

            if not self.isUniversalStream:
                await self.initDataTransfer(False)

            self._is_initing = False
            return True
        except Exception as e:
            self._is_initing = False
            raise DataContextInitError("Data context init failed: %s" % (e,)) from e

    async def start_streaming(self) -> bool:
        if self._is_data_transfering:
            raise DataNotificationInProgressError("Data collection is already in progress.")
            return True
        self._is_data_transfering = True
        self._rawDataBuffer.queue.clear()
        self._concatDataBuffer.clear()
        self.clear()

        if not self.isUniversalStream:
            await self.gForce.start_streaming(self._rawDataBuffer)
        else:
            await self.gForce.set_subscription(self.notifyDataFlag)

        return True

    async def stop_streaming(self) -> bool:
        if not self._is_data_transfering:
            return True

        self._is_data_transfering = False

        try:
            if not self.isUniversalStream:
                await self.gForce.stop_streaming()
            else:
                await self.gForce.set_subscription(0)

            while self._is_running and not self._rawDataBuffer.empty():
                await asyncio.sleep(0.1)

        except Exception as e:
            raise DataContextStopStreamingError("Failed to stop streaming: %s" % (e,)) from e

        return True

    async def setFilter(self, filter: str, value: str) -> str:
        self.filter_map[filter] = value
        switch = 0
        for filter in self.filter_map.keys():
            value = self.filter_map[filter]
            if filter == "FILTER_50HZ":
                if value == "ON":
                    switch |= 1
            elif filter == "FILTER_60HZ":
                if value == "ON":
                    switch |= 2
            elif filter == "FILTER_HPF":
                if value == "ON":
                    switch |= 4
            elif filter == "FILTER_LPF":
                if value == "ON":
                    switch |= 8
        try:
            ret = await self.gForce.set_firmware_filter_switch(switch)
            if ret == None:
                return "ERROR: not success"
            return "OK"
        except Exception as e:
            return "ERROR: " + str(e)

    async def setDebugCSV(self, debugFilePath: str | None) -> str:
        if self.debugCSVWriter != None:
            self.debugCSVWriter = None
        if debugFilePath != None:
            self.debugCSVPath = debugFilePath
            try:
                if self.debugCSVPath != "":
                    CSVWriter = csv.writer(open(self.debugCSVPath, "w", newline=""), delimiter=",")
                    CSVWriter = None
            except Exception as e:
                return "ERROR: " + str(e)
        return "OK"

    ####################################################################################

    async def process_data(
        self,
        buf: Queue[SensorData],
        sensor: object,
        callback: Callable[..., None] | None,
    ) -> None:
        while self._is_running:
            while self._is_running and self._rawDataBuffer.empty():
                if self._is_running and self.isDataTransfering and not buf.empty():
                    sensorData: SensorData = None
                    try:
                        sensorData = buf.get_nowait()
                    except Exception:
                        break
                    if not sensor_utils._terminated and sensorData != None and callback != None:
                        try:
                            asyncio.get_event_loop().run_in_executor(self.dataPool, callback, sensor, sensorData)
                        except Exception as e:
                            print(e)

                    buf.task_done()
                else:
                    await asyncio.sleep(0.01)
                continue

            try:
                while self._is_running and not self._rawDataBuffer.empty():
                    data = self._rawDataBuffer.get_nowait()

                    if self.notifyDataFlag & DataSubscription.DNF_CONCAT_BLE != 0:
                        self._concatDataBuffer.extend(data)
                    else:
                        self._processDataPackage(data, buf, sensor)

                    self._rawDataBuffer.task_done()
            except Exception:
                pass

            if self.notifyDataFlag & DataSubscription.DNF_CONCAT_BLE != 0:
                index = 0
                last_cut = -1
                data_size = len(self._concatDataBuffer)

                while self._is_running:
                    if index >= data_size:
                        break

                    if self._concatDataBuffer[index] == 0x55:
                        if (index + 1) >= data_size:
                            index = data_size
                            continue
                        n = self._concatDataBuffer[index + 1]
                        if n < 2 or (index + 1 + n + 1) >= data_size:
                            index += 1
                            continue
                        crc8 = self._concatDataBuffer[index + 1 + n + 1]
                        calc_crc = sensor_utils.calc_crc8(self._concatDataBuffer[index + 2 : index + 2 + n])
                        if crc8 != calc_crc:
                            index += 1
                            continue
                        if self._is_data_transfering:
                            data_package = bytes(self._concatDataBuffer[index + 2 : index + 2 + n])
                            self._processDataPackage(data_package, buf, sensor)
                        last_cut = index = index + 2 + n
                        index += 1
                    else:
                        index += 1

                if last_cut > 0:
                    self._concatDataBuffer = self._concatDataBuffer[last_cut + 1 :]
                    last_cut = -1
                    index = 0

    def _processDataPackage(self, data: bytes, buf: Queue[SensorData], sensor: object) -> None:
        v = data[0] & 0x7F
        if v == DataType.NTF_IMPEDANCE:
            offset = 1
            # packageIndex = ((data[offset + 1] & 0xff) << 8) | (data[offset] & 0xff)
            offset += 2

            impedanceData = []
            saturationData = []

            dataCount = (len(data) - 3) // 4 // 2

            for index in range(dataCount):
                impedance = struct.unpack_from("<f", data, offset)[0]
                offset += 4
                impedanceData.append(impedance)

            for index in range(dataCount):
                saturation = struct.unpack_from("<f", data, offset)[0]
                offset += 4
                saturationData.append(saturation / 10)  # firmware value range 0 - 1000

            self.impedanceData = impedanceData
            self.saturationData = saturationData
        elif v == DataType.NTF_IMPEDANCE_EXT:
            offset = 1
            # packageIndex = ((data[offset + 1] & 0xff) << 8) | (data[offset] & 0xff)
            offset += 2

            impedanceData = []
            saturationData = []

            dataCount = self._device_info.EegChannelCount + self._device_info.EcgChannelCount

            for index in range(dataCount):
                impedance = struct.unpack_from("<f", data, offset)[0]
                offset += 4
                impedanceData.append(impedance)

            for index in range(dataCount):
                saturation = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                saturationData.append(saturation / 10)  # firmware value range 0 - 1000

            self.impedanceData = impedanceData
            self.saturationData = saturationData
        elif v == DataType.NTF_MAG_ANGLE_DATA:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_MAG_ANGLE]
            if self.checkReadSamples(sensor, data, sensor_data, 4, 0):
                self.sendSensorData(sensor_data, buf)
        elif v == DataType.NTF_EMG:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_EMG]
            if self.checkReadSamples(sensor, data, sensor_data, sensor_data.packageIndexLength + 1, 0):
                self.sendSensorData(sensor_data, buf)
        elif v == DataType.NTF_EEG:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_EEG]
            if self.checkReadSamples(sensor, data, sensor_data, 3, 0):
                self.sendSensorData(sensor_data, buf)
        elif v == DataType.NTF_ECG:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_ECG]
            if self.checkReadSamples(sensor, data, sensor_data, 3, 0):
                self.sendSensorData(sensor_data, buf)
        elif v == DataType.NTF_BRTH:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_BRTH]
            if self.checkReadSamples(sensor, data, sensor_data, 3, 0):
                self.sendSensorData(sensor_data, buf)
        elif v == DataType.NTF_IMU:
            sensor_data_acc = self.sensorDatas[SensorDataType.DATA_TYPE_ACC]
            if self.checkReadSamples(sensor, data, sensor_data_acc, 3, 6):
                self.sendSensorData(sensor_data_acc, buf)

            sensor_data_gyro = self.sensorDatas[SensorDataType.DATA_TYPE_GYRO]
            if self.checkReadSamples(sensor, data, sensor_data_gyro, 9, 6):
                self.sendSensorData(sensor_data_gyro, buf)

    def checkReadSamples(
        self,
        sensor: object,
        data: bytes,
        sensorData: SensorData,
        dataOffset: int,
        dataGap: int,
    ) -> bool:
        offset = 1

        if not self._is_data_transfering:
            raise DataContextNotTransferringError(
                "checkReadSamples called while not transferring data (device may have stopped streaming)."
            )
        try:
            packageIndex = 0
            maxPackageIndex = 0
            if sensorData.packageIndexLength == 2:
                packageIndex = ((data[offset + 1] & 0xFF) << 8) | (data[offset] & 0xFF)
                maxPackageIndex = 65535
            elif sensorData.packageIndexLength == 1:
                packageIndex = data[offset] & 0xFF
                maxPackageIndex = 255

            if sensorData.packageIndexLength <= 0:
                if sensorData.lastPackageCounter < 0:
                    sensorData.lastPackageIndex = 0
                    sensorData.lastPackageCounter = 0
            else:
                offset += sensorData.packageIndexLength
                newPackageIndex = packageIndex
                lastPackageIndex = sensorData.lastPackageIndex
                if sensorData.lastPackageCounter < 0 and newPackageIndex > 0:
                    sensorData.lastPackageIndex = lastPackageIndex = newPackageIndex - 1
                    sensorData.lastPackageCounter = 0

                if packageIndex < lastPackageIndex:
                    packageIndex += maxPackageIndex + 1
                elif packageIndex == lastPackageIndex:
                    return False

                deltaPackageIndex = packageIndex - lastPackageIndex
                if deltaPackageIndex > 1:
                    lostSampleCount = sensorData.packageSampleCount * (deltaPackageIndex - 1)

                    if lostSampleCount < 100:
                        self.readSamples(data, sensorData, 0, dataGap, lostSampleCount)

                    if newPackageIndex == 0:
                        sensorData.lastPackageIndex = maxPackageIndex
                    else:
                        sensorData.lastPackageIndex = newPackageIndex - 1
                    sensorData.lastPackageCounter += deltaPackageIndex - 1

                    lostLog = (
                        "MSG|LOST SAMPLE|MAC|"
                        + str(sensorData.deviceMac)
                        + "|TYPE|"
                        + str(sensorData.dataType)
                        + "|COUNT|"
                        + str(lostSampleCount)
                    )
                    # print(lostLog)
                    if (
                        not sensor_utils._terminated
                        and sensor._event_loop != None
                        and sensor._on_error_callback != None
                    ):
                        try:
                            asyncio.get_event_loop().run_in_executor(None, sensor._on_error_callback, sensor, lostLog)
                        except Exception:
                            pass

                sensorData.lastPackageIndex = newPackageIndex

            if dataGap >= 0:
                self.readSamples(data, sensorData, dataOffset, dataGap, 0)

            sensorData.lastPackageCounter += 1
        except Exception as e:
            raise DataContextReadSamplesError("Error in checkReadSamples: %s" % (e,)) from e
        return True

    def transTrainData(self, data: int) -> int:
        xout = data >> 4
        exp = data & 0x0000000F
        xout = xout << exp
        return xout

    def readSamples(
        self,
        data: bytes,
        sensorData: SensorData,
        offset: int,
        dataGap: int,
        lostSampleCount: int,
    ) -> None:
        sampleCount = sensorData.packageSampleCount
        sampleInterval = 1000 // sensorData.sampleRate
        if lostSampleCount > 0:
            sampleCount = lostSampleCount

        K = sensorData.K
        lastSampleIndex = sensorData.lastPackageCounter * sensorData.packageSampleCount

        _impedanceData = self.impedanceData.copy()
        _saturationData = self.saturationData.copy()

        channelSamples = sensorData.channelSamples
        if not channelSamples:
            for channelIndex in range(sensorData.channelCount):
                channelSamples.append([])

        for sampleIndex in range(sampleCount):
            for channelIndex, impedanceChannelIndex in enumerate(range(sensorData.channelCount)):
                if (sensorData.channelMask & (1 << channelIndex)) != 0:
                    samples = channelSamples[channelIndex]
                    impedance = 0.0
                    saturation = 0.0

                    if sensorData.dataType == DataType.NTF_ECG:
                        impedanceChannelIndex = self.sensorDatas[SensorDataType.DATA_TYPE_EEG].channelCount

                    if impedanceChannelIndex < len(_impedanceData):
                        impedance = _impedanceData[impedanceChannelIndex]
                        saturation = _saturationData[impedanceChannelIndex]

                    impedanceChannelIndex += 1

                    dataItem = Sample()
                    dataItem.channelIndex = channelIndex
                    dataItem.sampleIndex = lastSampleIndex
                    dataItem.timeStampInMs = lastSampleIndex * sampleInterval
                    if lostSampleCount > 0:
                        dataItem.rawData = 0
                        dataItem.data = 0.0
                        dataItem.impedance = impedance
                        dataItem.saturation = saturation
                        dataItem.isLost = True
                    else:
                        rawData = 0
                        if sensorData.resolutionBits == 7:
                            rawData = data[offset]
                            rawData -= 119
                            offset += 1
                        elif sensorData.resolutionBits == 8:
                            rawData = data[offset] & 0xFF
                            offset += 1
                        elif sensorData.resolutionBits == 12 or sensorData.resolutionBits == 16:
                            rawData = int.from_bytes(
                                data[offset : offset + 2],
                                byteorder="little",
                                signed=True,
                            )
                            offset += 2
                        elif sensorData.resolutionBits == 24:
                            rawData = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]
                            rawData -= 8388608
                            offset += 3
                        elif sensorData.resolutionBits == 0:
                            rawData = struct.unpack_from("<h", data, offset)[0]
                            offset += 2
                            rawData = self.transTrainData(rawData)

                        converted = rawData * K
                        dataItem.rawData = rawData
                        dataItem.data = converted
                        dataItem.impedance = impedance
                        dataItem.saturation = saturation
                        dataItem.isLost = False

                    samples.append(dataItem)

            lastSampleIndex += 1
            offset += dataGap

    def sendSensorData(self, sensorData: SensorData, buf: Queue[SensorData]) -> None:
        oldChannelSamples = sensorData.channelSamples

        if not self.isDataTransfering or len(oldChannelSamples) == 0:
            return

        realSampleCount = 0
        if len(oldChannelSamples) > 0:
            realSampleCount = len(oldChannelSamples[0])

        if realSampleCount < sensorData.minPackageSampleCount:
            return

        sensorData.channelSamples = []
        batchCount = realSampleCount // sensorData.minPackageSampleCount
        # leftSampleSize = realSampleCount - sensorData.minPackageSampleCount * batchCount

        sensorDataList = []
        startIndex = 0
        for batchIndex in range(batchCount):
            resultChannelSamples = []
            for channelIndex in range(sensorData.channelCount):
                oldSamples = oldChannelSamples[channelIndex]
                newSamples = []
                for sampleIndex in range(sensorData.minPackageSampleCount):
                    newSamples.append(oldSamples[startIndex + sampleIndex])
                resultChannelSamples.append(newSamples)

            sensorDataResult = SensorData()
            sensorDataResult.channelSamples = resultChannelSamples
            sensorDataResult.dataType = sensorData.dataType
            sensorDataResult.deviceMac = sensorData.deviceMac
            sensorDataResult.sampleRate = sensorData.sampleRate
            sensorDataResult.channelCount = sensorData.channelCount
            sensorDataResult.minPackageSampleCount = sensorData.minPackageSampleCount
            sensorDataList.append(sensorDataResult)

            if self.debugCSVPath != None and self.debugCSVPath != "" and self.debugCSVWriter == None:
                try:
                    self.debugCSVWriter = csv.writer(open(self.debugCSVPath, "w", newline="", encoding="utf-8"))
                    header_append_keys = ["dataType", "sampleRate"]
                    channel_samples_header = list(vars(sensorDataResult.channelSamples[0][0]).keys())
                    for key_item in header_append_keys:
                        channel_samples_header.append(key_item)
                    self.debugCSVWriter.writerow(channel_samples_header)
                except Exception as e:
                    print(e)

            if self.debugCSVWriter != None:
                try:
                    for i, channel_sample_list in enumerate(sensorDataResult.channelSamples):
                        for channel_sample in channel_sample_list:
                            row_data = []

                            for key in vars(channel_sample).keys():
                                row_data.append(getattr(channel_sample, key))
                            row_data.append(sensorDataResult.dataType)
                            row_data.append(sensorDataResult.sampleRate)
                            self.debugCSVWriter.writerow(row_data)
                except Exception as e:
                    print(e)

            startIndex += sensorData.minPackageSampleCount

        leftChannelSamples = []
        for channelIndex in range(sensorData.channelCount):
            oldSamples = oldChannelSamples[channelIndex]
            newSamples = []
            for sampleIndex in range(startIndex, len(oldSamples)):
                newSamples.append(oldSamples[sampleIndex])

            leftChannelSamples.append(newSamples)

        sensorData.channelSamples = leftChannelSamples

        for sensorDataResult in sensorDataList:
            buf.put(sensorDataResult)

    async def processUniversalData(
        self,
        buf: Queue[SensorData],
        sensor: object,
        callback: Callable[..., None] | None,
    ) -> None:
        while self._is_running:
            while self._is_running and self._rawDataBuffer.empty():
                if self._is_running and self.isDataTransfering and not buf.empty():
                    sensorData: SensorData = None
                    try:
                        sensorData = buf.get_nowait()
                    except Exception:
                        break
                    if not sensor_utils._terminated and sensorData != None and callback != None:
                        try:
                            asyncio.get_event_loop().run_in_executor(self.dataPool, callback, sensor, sensorData)
                        except Exception as e:
                            print(e)

                    buf.task_done()
                else:
                    await asyncio.sleep(0.01)
                continue

            try:
                while self._is_running and not self._rawDataBuffer.empty():
                    data = self._rawDataBuffer.get_nowait()
                    self._concatDataBuffer.extend(data)
                    self._rawDataBuffer.task_done()
            except Exception:
                pass

            index = 0
            last_cut = -1
            data_size = len(self._concatDataBuffer)

            while self._is_running:
                if index >= data_size:
                    break

                if self._concatDataBuffer[index] == 0x55:
                    if (index + 1) >= data_size:
                        index = data_size
                        continue
                    n = self._concatDataBuffer[index + 1]
                    if n < 2 or (index + 1 + n + 2) >= data_size:
                        index += 1
                        continue
                    crc16 = (self._concatDataBuffer[index + 1 + n + 2] << 8) | self._concatDataBuffer[index + 1 + n + 1]
                    calc_crc = sensor_utils.crc16_cal(self._concatDataBuffer[index + 2 : index + 2 + n], n)
                    if crc16 != calc_crc:
                        index += 1
                        continue
                    if self._is_data_transfering:
                        data_package = bytes(self._concatDataBuffer[index + 2 : index + 2 + n])
                        self._processDataPackage(data_package, buf, sensor)
                    last_cut = index = index + 2 + n + 1
                    index += 1
                elif self._concatDataBuffer[index] == 0xAA:
                    if (index + 1) >= data_size:
                        index = data_size
                        continue
                    n = self._concatDataBuffer[index + 1]
                    if n < 2 or (index + 1 + n + 2) >= data_size:
                        index += 1
                        continue
                    crc16 = (self._concatDataBuffer[index + 1 + n + 2] << 8) | self._concatDataBuffer[index + 1 + n + 1]
                    calc_crc = sensor_utils.crc16_cal(self._concatDataBuffer[index + 2 : index + 2 + n], n)
                    if crc16 != calc_crc:
                        index += 1
                        continue
                    data_package = bytes(self._concatDataBuffer[index + 2 : index + 2 + n])

                    if not sensor_utils._terminated:
                        await sensor_utils.async_call(
                            self.gForce.async_on_cmd_response(data_package), runloop=sensor._event_loop
                        )
                    last_cut = index = index + 2 + n + 1
                    index += 1
                else:
                    index += 1

            if last_cut > 0:
                self._concatDataBuffer = self._concatDataBuffer[last_cut + 1 :]
                last_cut = -1
                index = 0
