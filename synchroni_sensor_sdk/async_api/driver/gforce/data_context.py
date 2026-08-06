import asyncio
import contextlib
import csv
import logging
import platform
import struct
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, IntEnum
from typing import Any, TextIO

from synchroni_sensor_sdk.async_api.driver.gforce.convert import sensor_data_to_public
from synchroni_sensor_sdk.async_api.driver.gforce.crc_utils import calc_crc8, crc16_cal
from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import DataType, DeviceInfo, Sample, SensorData
from synchroni_sensor_sdk.async_api.driver.gforce.protocol import (
    DataSubscription,
    GForceProtocol,
    ImuRawDataConfig,
    SampleResolution,
    SamplingRate,
)
from synchroni_sensor_sdk.core.data import SensorData as PublicSensorData
from synchroni_sensor_sdk.core.device import DeviceParams
from synchroni_sensor_sdk.core.exceptions import (
    DataContextInitError,
    DataContextInitInProgressError,
    DataContextNotTransferringError,
    DataContextReadSamplesError,
    DataContextStopStreamingError,
    DataNotificationInProgressError,
)
from synchroni_sensor_sdk.core.params import (
    DEFAULT_FILTER_PARAMS,
    DEFAULT_NTF_PARAMS,
    IMU_SUB_PARAMS,
    FilterParam,
    NtfParam,
    ParamToggle,
)

_terminated = False


class SensorDataType(IntEnum):
    DATA_TYPE_EEG = 0
    DATA_TYPE_ECG = 1
    DATA_TYPE_ACC = 2
    DATA_TYPE_GYRO = 3
    DATA_TYPE_BRTH = 4
    DATA_TYPE_EMG = 5
    DATA_TYPE_MAG_ANGLE = 6
    DATA_TYPE_QUATERNION = 7
    DATA_TYPE_PPG = 8
    DATA_TYPE_SPO2 = 9
    DATA_TYPE_EULER = 10
    DATA_TYPE_GFORCE_QUAT = 11
    DATA_TYPE_IMPEDANCE = 12
    DATA_TYPE_GEST = 13
    DATA_TYPE_COUNT = 14


# 枚举 FeatureMaps 的 Python 实现 / Python implementation of FeatureMaps enum (feature flags).
class FeatureMaps(Enum):
    GFD_FEAT_GEST = 0x000001000
    GFD_FEAT_EMG = 0x000002000
    GFD_FEAT_EULER = 0x000000200
    GFD_FEAT_QUAT = 0x000000400
    GFD_FEAT_ACC = 0x000000040
    GFD_FEAT_GYRO = 0x000000080
    GFD_FEAT_MAGANG = 0x00080000
    GFD_FEAT_EEG = 0x000400000
    GFD_FEAT_ECG = 0x000800000
    GFD_FEAT_IMPEDANCE = 0x001000000
    GFD_FEAT_IMU = 0x002000000
    GFD_FEAT_ADS = 0x004000000
    GFD_FEAT_BRTH = 0x008000000
    GFD_FEAT_PPG = 0x10000000
    GFD_FEAT_CONCAT_BLE = 0x80000000


class PPGDataMode(IntEnum):
    SPO2_AND_HR = 0
    PPG_RAW = 1
    PPG_AND_SPO2 = 2


_MAX_ALLOWED_PACKAGE_INDEX_DELTA = 50
_WATCHDOG_STALL_S = 5.0


class ReadSamplesResult(IntEnum):
    OK = 0
    REPEATED = 1
    ERROR = 2


class DataContext:
    """Parse raw GForce BLE bytes into batched samples for the driver buffer.

    Runs on the driver's event loop. Raw bytes arrive on ``_rawDataBuffer`` via
    ``GForceProtocol`` (``call_soon_threadsafe`` from notify handlers). A driver-
    owned ``_process_task`` calls :meth:`process_data` or :meth:`process_universal_data`
    for the lifetime of the connection. Parsed packets are passed to the driver's
    ``publish_data`` callback — never enqueue directly to the public buffer.
    """

    @staticmethod
    def _drain_queue(q: asyncio.Queue[bytes]) -> None:
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break

    def __init__(
        self,
        gForce: GForceProtocol,
        deviceMac: str,
        buf: asyncio.Queue[bytes],
        *,
        publish_data: Callable[[PublicSensorData], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self.featureMap: int = 0
        self.notifyDataFlag: int = 0

        self.gForce: GForceProtocol = gForce
        self.deviceMac: str = deviceMac
        self._device_info: DeviceInfo | None = None

        self._is_initing: bool = False
        self._is_running: bool = True
        self._is_data_transfering: bool = False
        self.isUniversalStream: bool = gForce._is_universal_stream
        self._rawDataBuffer: asyncio.Queue[bytes] = buf
        self._publish_data = publish_data
        self._on_error = on_error
        self._concatDataBuffer: bytearray = bytearray()

        self.isNewEMG: bool = False
        self.isContainQAT6: bool = False
        self.ppgModel: PPGDataMode = PPGDataMode.PPG_AND_SPO2
        self._last_progress_time: float = 0.0
        self._watchdog_restart_pending: bool = False

        self.sensorDatas: list[SensorData] = []
        for _idx in range(0, SensorDataType.DATA_TYPE_COUNT):
            self.sensorDatas.append(SensorData())
        self.impedanceData: list[float] = []
        self.saturationData: list[float] = []
        self.dataPool: ThreadPoolExecutor = ThreadPoolExecutor(1, "data")
        self.init_map: dict[NtfParam, ParamToggle] = dict(DEFAULT_NTF_PARAMS)
        # Match legacy defaults: non-RFSTAR devices leave IMU off until explicit enable.
        if not self.isUniversalStream:
            self.init_map[NtfParam.NTF_IMU] = ParamToggle.OFF
            for sub in IMU_SUB_PARAMS:
                self.init_map[sub] = ParamToggle.OFF
        self.filter_map: dict[FilterParam, ParamToggle] = dict(DEFAULT_FILTER_PARAMS)
        self.debugCSVWriter: Any = None
        self._debug_csv_file: TextIO | None = None
        self.debugCSVPath: str | None = None

    def abort_streaming(self) -> None:
        """Stop accepting samples without sending BLE stop commands.

        Used during unexpected disconnect when the link is already gone and
        :meth:`stop_streaming` would fail; prevents :meth:`checkReadSamples`
        from processing further packets.
        """
        self._is_data_transfering = False

    def close(self) -> None:
        """Signal parser loops to exit (``_is_running = False``).

        Called from driver teardown after ``_process_task`` is cancelled and
        awaited, so loops stop cleanly without racing ``sendSensorData``.
        """
        self.abort_streaming()
        self._is_running = False
        if self._debug_csv_file is not None:
            self._debug_csv_file.close()
            self._debug_csv_file = None
        self.debugCSVWriter = None

    def clear(self) -> None:
        for sensorData in self.sensorDatas:
            sensorData.clear()
        self.impedanceData.clear()
        self.saturationData.clear()
        self._concatDataBuffer.clear()
        self._drain_queue(self._rawDataBuffer)

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

    def hasGEST(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_GEST.value) != 0

    def hasPPG(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_PPG.value) != 0

    def hasEuler(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_EULER.value) != 0

    def hasQuat(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_QUAT.value) != 0

    def hasAcc(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_ACC.value) != 0

    def hasGyro(self) -> bool:
        return (self.featureMap & FeatureMaps.GFD_FEAT_GYRO.value) != 0

    def _ntf_on(self, key: NtfParam) -> bool:
        return self.init_map.get(key, ParamToggle.OFF) == ParamToggle.ON

    def build_notify_data_flag(self) -> int:
        """Rebuild subscription mask from features + ``init_map``."""
        flag = 0
        if self.hasConcatBLE():
            flag |= DataSubscription.DNF_CONCAT_BLE
        if self.hasEMG() and self._ntf_on(NtfParam.NTF_EMG):
            flag |= DataSubscription.EMG_RAW
        if self.hasGEST() and self._ntf_on(NtfParam.NTF_GEST):
            flag |= DataSubscription.DNF_TYPE_GEST_EXT
        if self.hasEEG() and self._ntf_on(NtfParam.NTF_EEG):
            flag |= DataSubscription.DNF_EEG
        if self.hasECG() and self._ntf_on(NtfParam.NTF_ECG):
            flag |= DataSubscription.DNF_ECG
        if self.hasImpedance() and self._ntf_on(NtfParam.NTF_IMPEDANCE):
            flag |= DataSubscription.DNF_IMPEDANCE
        if self.hasBrth() and self._ntf_on(NtfParam.NTF_BRTH):
            flag |= DataSubscription.DNF_BRTH
        if self.hasIMU() and self._ntf_on(NtfParam.NTF_IMU):
            flag |= DataSubscription.DNF_IMU
        if self.hasEuler() and self._ntf_on(NtfParam.NTF_GFORCE_EULER):
            flag |= DataSubscription.EULERANGLE
        if self.hasQuat() and self._ntf_on(NtfParam.NTF_GFORCE_QUAT):
            flag |= DataSubscription.QUATERNION
        if self.hasAcc() and self._ntf_on(NtfParam.NTF_GFORCE_ACC):
            flag |= DataSubscription.ACCELERATE
        if self.hasGyro() and self._ntf_on(NtfParam.NTF_GFORCE_GYRO):
            flag |= DataSubscription.GYROSCOPE
        if self.hasPPG() and (self._ntf_on(NtfParam.NTF_PPG) or self._ntf_on(NtfParam.NTF_SPO2)):
            flag |= DataSubscription.DNF_PPG
        if self.hasMagAngle() and self._ntf_on(NtfParam.NTF_MAG_ANGLE):
            flag |= DataSubscription.DNF_MAG_ANGLE_EXT
        self.notifyDataFlag = flag
        return flag

    def get_params_snapshot(self) -> DeviceParams:
        return DeviceParams(
            ntf={k.value: v.as_bool() for k, v in self.init_map.items()},
            filters={k.value: v.as_bool() for k, v in self.filter_map.items()},
            debug_ble_data_path=self.debugCSVPath,
        )

    def sync_imu_master_from_subs(self) -> None:
        all_on = all(self._ntf_on(sub) for sub in IMU_SUB_PARAMS)
        self.init_map[NtfParam.NTF_IMU] = ParamToggle.ON if all_on else ParamToggle.OFF

    def apply_imu_master(self, enabled: bool) -> None:
        toggle = ParamToggle.from_bool(enabled)
        self.init_map[NtfParam.NTF_IMU] = toggle
        for sub in IMU_SUB_PARAMS:
            self.init_map[sub] = toggle

    async def apply_function_switch(self) -> None:
        if not self.isNewEMG:
            return
        emg_bit = 1 if self._ntf_on(NtfParam.NTF_EMG) else 0
        gest_bit = 1 if (emg_bit and self._ntf_on(NtfParam.NTF_GEST)) else 0
        await self.gForce.set_function_switch(emg_bit | (gest_bit << 1))

    async def apply_subscription(self) -> None:
        """Push current NTF map to the device (after init)."""
        self.build_notify_data_flag()
        await self.apply_function_switch()
        if not self.isUniversalStream:
            await self.gForce.set_subscription(self.notifyDataFlag)

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
        device_info = self._device_info
        if device_info is not None:
            device_name = device_info.DeviceName
            if (
                device_name.startswith("gForce")
                or device_name.startswith("OHand")
                or device_name.startswith("ORE-")
                or device_name.startswith("OYEM-")
                or device_name.startswith("ORehab")
            ):
                isNewEMG = False
        self.isNewEMG = isNewEMG

        if isNewEMG:
            # new emg
            data.packageIndexLength = 2
            data.packageSampleCount = 8
            data.resolutionBits = 0
            gain = 6
            data.K = 4000000.0 / 8388607.0 / gain
            config.resolution = SampleResolution.BITS_8
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
            config.resolution = SampleResolution.BITS_8

        config.fs = SamplingRate.HZ_500
        config.channel_mask = 255
        config.batch_len = 128

        if isNewEMG:
            await self.apply_function_switch()

        await self.gForce.set_emg_raw_data_config(config)
        await self.gForce.set_package_id(True)

        self.sensorDatas[SensorDataType.DATA_TYPE_EMG] = data
        return data.channelCount

    async def initGesture(self, _packageCount: int) -> int:
        emg_rate = 0
        if self._device_info is not None:
            emg_rate = self._device_info.EmgSampleRate
        if emg_rate <= 0 and self.sensorDatas[SensorDataType.DATA_TYPE_EMG].sampleRate > 0:
            emg_rate = int(self.sensorDatas[SensorDataType.DATA_TYPE_EMG].sampleRate)
        if emg_rate <= 0:
            return 0

        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_GEST
        data.sampleRate = max(1, int(emg_rate / 32.0)) if emg_rate else 0
        data.resolutionBits = 0
        data.channelCount = 1
        data.channelMask = 1
        data.minPackageSampleCount = 1
        data.packageSampleCount = 1
        data.K = 1
        if not self.isNewEMG:
            data.packageIndexLength = 1
            if self._ntf_on(NtfParam.NTF_EMG):
                self.init_map[NtfParam.NTF_GEST] = ParamToggle.OFF
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_GEST] = data
        if self._device_info is not None:
            self._device_info.GestChannelCount = data.channelCount
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
        return data.channelCount

    async def initIMU(self, _packageCount: int) -> int:
        IMU_TYPE_QAT6 = 0x0004
        min_package_sample_count = 1
        self.isContainQAT6 = False

        imu_cap = await self.gForce.get_imu_cap_data_config()
        if imu_cap is not None:
            channel_mask, samp_rate, _sample_count = imu_cap
            if (channel_mask & IMU_TYPE_QAT6) == IMU_TYPE_QAT6:
                self.isContainQAT6 = True
            cfg = ImuRawDataConfig()
            cfg.channel_count = channel_mask
            cfg.fs = samp_rate
            cfg.batch_len = min_package_sample_count
            await self.gForce.set_imu_raw_data_config(cfg)

        config = await self.gForce.get_imu_raw_data_config()
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_ACC
        data.sampleRate = config.fs
        data.resolutionBits = 16
        data.channelCount = 3
        data.channelMask = 255
        data.minPackageSampleCount = min_package_sample_count
        data.packageSampleCount = config.batch_len
        data.K = config.accK
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_ACC] = data

        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_GYRO
        data.sampleRate = config.fs
        data.resolutionBits = 16
        data.channelCount = 3
        data.channelMask = 255
        data.minPackageSampleCount = min_package_sample_count
        data.packageSampleCount = config.batch_len
        data.K = config.gyroK
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_GYRO] = data

        if self.isContainQAT6:
            data = SensorData()
            data.deviceMac = self.deviceMac
            data.dataType = DataType.NTF_QUATERNION
            data.sampleRate = config.fs
            data.resolutionBits = 31
            data.channelCount = 4
            data.channelMask = 0b1110
            data.minPackageSampleCount = min_package_sample_count
            data.packageSampleCount = config.batch_len
            data.K = 1.0 / 1073741824.0
            data.clear()
            self.sensorDatas[SensorDataType.DATA_TYPE_QUATERNION] = data

            data = SensorData()
            data.deviceMac = self.deviceMac
            data.dataType = DataType.NTF_EULER_DATA
            data.sampleRate = config.fs
            data.resolutionBits = 0
            data.channelCount = 3
            data.channelMask = 0b0111
            data.packageIndexLength = 0
            data.minPackageSampleCount = min_package_sample_count
            data.packageSampleCount = config.batch_len
            data.K = 1.0
            data.clear()
            self.sensorDatas[SensorDataType.DATA_TYPE_EULER] = data

        if self._device_info is not None:
            self._device_info.AccChannelCount = 3
            self._device_info.GyroChannelCount = 3
            self._device_info.AccSampleRate = config.fs
            self._device_info.GyroSampleRate = config.fs
            if self.isContainQAT6:
                self._device_info.QuatChannelCount = 4
                self._device_info.EulerChannelCount = 3
                self._device_info.QuatSampleRate = config.fs
                self._device_info.EulerSampleRate = config.fs

        return 3

    async def initEuler(self, _packageCount: int) -> int:
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_EULER_DATA
        data.sampleRate = 40
        data.resolutionBits = 32
        data.channelCount = 3
        data.channelMask = 0b0111
        data.packageIndexLength = 1
        data.minPackageSampleCount = 1
        data.packageSampleCount = 1
        data.K = 1.0
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_EULER] = data
        return data.channelCount

    async def initGForceQuat(self, _packageCount: int) -> int:
        data = SensorData()
        data.deviceMac = self.deviceMac
        data.dataType = DataType.NTF_QUATERNION
        data.sampleRate = 40
        data.resolutionBits = 32
        data.channelCount = 4
        data.channelMask = 0b1111
        data.packageIndexLength = 1
        data.minPackageSampleCount = 1
        data.packageSampleCount = 1
        data.K = 1.0
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_GFORCE_QUAT] = data
        return data.channelCount

    async def initPPG(self, packageCount: int) -> int:
        config = await self.gForce.get_ppg_raw_data_config()
        config.mode = int(self.ppgModel)
        config.period = 1
        config.fs = 50
        await self.gForce.set_ppg_raw_data_config(config)

        data = SensorData()
        data.dataType = DataType.NTF_PPG
        data.deviceMac = self.deviceMac
        data.sampleRate = config.fs
        data.channelMask = 255
        data.minPackageSampleCount = packageCount
        data.packageSampleCount = config.batch_len
        data.K = 1.0
        data.resolutionBits = 24
        data.channelCount = 2
        data.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_PPG] = data

        spo2 = SensorData()
        spo2.dataType = DataType.NTF_SPO2
        spo2.deviceMac = self.deviceMac
        spo2.sampleRate = config.period
        spo2.channelMask = 255
        spo2.minPackageSampleCount = 1
        spo2.packageSampleCount = 1
        spo2.K = 1.0
        spo2.resolutionBits = 17
        spo2.channelCount = 2
        spo2.clear()
        self.sensorDatas[SensorDataType.DATA_TYPE_SPO2] = spo2
        return spo2.channelCount

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
        return data.channelCount

    async def initMagAngle(self, _packageCount: int) -> int:
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
        return data.channelCount

    async def initDataTransfer(self, isGetFeature: bool) -> int:
        if isGetFeature:
            self.featureMap = await self.gForce.get_feature_map()
            return self.featureMap
        else:
            await self.gForce.set_subscription(self.notifyDataFlag)
            return self.notifyDataFlag

    async def fetchDeviceInfo(self) -> DeviceInfo:
        info = DeviceInfo()
        client = self.gForce.client
        if platform.system() != "Linux" and client is not None:
            info.MTUSize = client.mtu_size
        else:
            info.MTUSize = 0
        info.DeviceName = await self.gForce.get_device_name()
        info.ModelName = await self.gForce.get_model_number()
        info.HardwareVersion = await self.gForce.get_hardware_revision()
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

            if self.hasEMG() and self._ntf_on(NtfParam.NTF_EMG):
                info.EmgChannelCount = await self.initEMG(packageCount)
                info.EmgSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_EMG].sampleRate)

            if self.hasGEST() and self._ntf_on(NtfParam.NTF_GEST):
                await self.initGesture(packageCount)

            if self.hasEEG() and self._ntf_on(NtfParam.NTF_EEG):
                info.EegChannelCount = await self.initEEG(packageCount)
                info.EegSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_EEG].sampleRate)

            if self.hasECG() and self._ntf_on(NtfParam.NTF_ECG):
                info.EcgChannelCount = await self.initECG(packageCount)
                info.EcgSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_ECG].sampleRate)

            if self.hasBrth() and self._ntf_on(NtfParam.NTF_BRTH):
                info.BrthChannelCount = await self.initBrth(packageCount)
                info.BrthSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_BRTH].sampleRate)

            if self.hasIMU() and self._ntf_on(NtfParam.NTF_IMU):
                await self.initIMU(packageCount)
                info.AccChannelCount = 3
                info.GyroChannelCount = 3
                info.AccSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_ACC].sampleRate)
                info.GyroSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_GYRO].sampleRate)
                if self.isContainQAT6:
                    info.QuatChannelCount = 4
                    info.EulerChannelCount = 3
                    info.QuatSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_QUATERNION].sampleRate)
                    info.EulerSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_EULER].sampleRate)

            if self.hasEuler() and self._ntf_on(NtfParam.NTF_GFORCE_EULER) and not self.isContainQAT6:
                info.EulerChannelCount = await self.initEuler(packageCount)
                info.EulerSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_EULER].sampleRate)

            if self.hasQuat() and self._ntf_on(NtfParam.NTF_GFORCE_QUAT) and not self.isContainQAT6:
                info.QuatChannelCount = await self.initGForceQuat(packageCount)
                info.QuatSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_GFORCE_QUAT].sampleRate)

            if self.hasPPG() and (self._ntf_on(NtfParam.NTF_PPG) or self._ntf_on(NtfParam.NTF_SPO2)):
                await self.initPPG(packageCount)
                info.PpgChannelCount = 2
                info.Spo2ChannelCount = 2
                info.PpgSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_PPG].sampleRate)
                info.Spo2SampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_SPO2].sampleRate)

            if self.hasMagAngle() and self._ntf_on(NtfParam.NTF_MAG_ANGLE):
                magAngleChannelCount = await self.initMagAngle(packageCount)
                info.MagAngleChannelCount = magAngleChannelCount
                info.MagAngleSampleRate = int(self.sensorDatas[SensorDataType.DATA_TYPE_MAG_ANGLE].sampleRate)

            self._device_info = info
            self.build_notify_data_flag()

            if not self.isUniversalStream:
                await self.initDataTransfer(False)
            await self.apply_function_switch()

            self._is_initing = False
            return True
        except Exception as e:
            self._is_initing = False
            raise DataContextInitError(f"Data context init failed: {e}") from e

    async def start_streaming(self) -> bool:
        if self._is_data_transfering:
            raise DataNotificationInProgressError("Data collection is already in progress.")
            return True
        self._is_data_transfering = True
        self._drain_queue(self._rawDataBuffer)
        self._concatDataBuffer.clear()
        self.clear()
        self._last_progress_time = time.monotonic()
        self._watchdog_restart_pending = False

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
                await self.gForce.set_subscription(DataSubscription.OFF)

            while self._is_running and not self._rawDataBuffer.empty():
                await asyncio.sleep(0.1)

        except Exception as e:
            raise DataContextStopStreamingError(f"Failed to stop streaming: {e}") from e

        return True

    async def setFilter(self, filter: FilterParam, value: ParamToggle) -> str:
        self.filter_map[filter] = value
        switch = 0
        for filter_key, toggle in self.filter_map.items():
            if toggle == ParamToggle.ON:
                switch |= filter_key.firmware_switch_bit
        try:
            await self.gForce.set_firmware_filter_switch(switch)
            return "OK"
        except Exception as e:
            return "ERROR: " + str(e)

    async def setDebugCSV(self, debugFilePath: str | None) -> str:
        if self._debug_csv_file is not None:
            self._debug_csv_file.close()
            self._debug_csv_file = None
        self.debugCSVWriter = None
        if debugFilePath is not None:
            self.debugCSVPath = debugFilePath
            try:
                if self.debugCSVPath != "":
                    with open(self.debugCSVPath, "w", newline="", encoding="utf-8") as f:
                        csv.writer(f, delimiter=",")
            except Exception as e:
                return "ERROR: " + str(e)
        return "OK"

    ####################################################################################

    async def process_data(self) -> None:
        """Parser loop for standard (non-CONCAT_BLE) devices.

        Scheduled as ``GForceDriver._process_task`` at connect. Drains
        ``_rawDataBuffer`` on the driver loop; optional CONCAT_BLE reassembly
        runs inline when that subscription flag is set.
        """
        while self._is_running:
            while self._is_running and self._rawDataBuffer.empty():
                await asyncio.sleep(0.01)
                if (
                    self._is_data_transfering
                    and self._last_progress_time > 0
                    and (time.monotonic() - self._last_progress_time) > _WATCHDOG_STALL_S
                ):
                    self._logger.warning("Data parse stall detected; clearing assemble buffers")
                    self._concatDataBuffer.clear()
                    self._drain_queue(self._rawDataBuffer)
                    for sensor_data in self.sensorDatas:
                        sensor_data.clear()
                    self._last_progress_time = time.monotonic()
            if not self._is_running:
                break

            if self._watchdog_restart_pending:
                self._watchdog_restart_pending = False
                self._concatDataBuffer.clear()
                self._drain_queue(self._rawDataBuffer)
                for sensor_data in self.sensorDatas:
                    sensor_data.clear()
                self._last_progress_time = time.monotonic()

            try:
                while self._is_running and not self._rawDataBuffer.empty():
                    data = self._rawDataBuffer.get_nowait()

                    if self.notifyDataFlag & DataSubscription.DNF_CONCAT_BLE != 0:
                        self._concatDataBuffer.extend(data)
                    else:
                        self._processDataPackage(data)

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
                        calc_crc = calc_crc8(self._concatDataBuffer[index + 2 : index + 2 + n])
                        if crc8 != calc_crc:
                            index += 1
                            continue
                        if self._is_data_transfering:
                            data_package = bytes(self._concatDataBuffer[index + 2 : index + 2 + n])
                            self._processDataPackage(data_package)
                        last_cut = index = index + 2 + n
                        index += 1
                    else:
                        index += 1

                if last_cut > 0:
                    self._concatDataBuffer = self._concatDataBuffer[last_cut + 1 :]
                    last_cut = -1
                    index = 0

    def _processDataPackage(self, data: bytes) -> None:
        if not data:
            return
        v = data[0] & 0x7F
        self._last_progress_time = time.monotonic()

        def dispatch(sensor_type: SensorDataType, data_offset: int, data_gap: int) -> None:
            sensor_data = self.sensorDatas[sensor_type]
            if sensor_data.sampleRate <= 0:
                return
            if self.checkReadSamples(data, sensor_data, data_offset, data_gap):
                self.sendSensorData(sensor_data)

        if v == DataType.NTF_IMPEDANCE:
            offset = 1
            # packageIndex = ((data[offset + 1] & 0xff) << 8) | (data[offset] & 0xff)
            offset += 2

            impedanceData = []
            saturationData = []

            dataCount = (len(data) - 3) // 4 // 2

            for _index in range(dataCount):
                impedance = struct.unpack_from("<f", data, offset)[0]
                offset += 4
                impedanceData.append(impedance)

            for _index in range(dataCount):
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

            device_info = self._device_info
            if device_info is None:
                return
            dataCount = device_info.EegChannelCount + device_info.EcgChannelCount

            for _index in range(dataCount):
                impedance = struct.unpack_from("<f", data, offset)[0]
                offset += 4
                impedanceData.append(impedance)

            for _index in range(dataCount):
                saturation = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                saturationData.append(saturation / 10)  # firmware value range 0 - 1000

            self.impedanceData = impedanceData
            self.saturationData = saturationData
        elif v == DataType.NTF_MAG_ANGLE_DATA:
            dispatch(SensorDataType.DATA_TYPE_MAG_ANGLE, 4, 0)
        elif v == DataType.NTF_EMG:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_EMG]
            dispatch(SensorDataType.DATA_TYPE_EMG, sensor_data.packageIndexLength + 1, 0)
        elif v == DataType.NTF_GEST:
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_GEST]
            if self.checkReadSamples(data, sensor_data, 0, -1):
                self.sendSensorData(sensor_data)
        elif v == DataType.NTF_EEG:
            dispatch(SensorDataType.DATA_TYPE_EEG, 3, 0)
        elif v == DataType.NTF_ECG:
            dispatch(SensorDataType.DATA_TYPE_ECG, 3, 0)
        elif v == DataType.NTF_BRTH:
            dispatch(SensorDataType.DATA_TYPE_BRTH, 3, 0)
        elif v == DataType.NTF_IMU and self.hasIMU():
            sensor_data_acc = self.sensorDatas[SensorDataType.DATA_TYPE_ACC]
            if self.checkReadSamples(data, sensor_data_acc, 3, 6):
                self.sendSensorData(sensor_data_acc)

            sensor_data_gyro = self.sensorDatas[SensorDataType.DATA_TYPE_GYRO]
            if self.checkReadSamples(data, sensor_data_gyro, 9, 6):
                self.sendSensorData(sensor_data_gyro)

            if self.isContainQAT6:
                sensor_quat = self.sensorDatas[SensorDataType.DATA_TYPE_QUATERNION]
                if sensor_quat.sampleRate > 0 and self.checkReadSamples(data, sensor_quat, 15, 0):
                    self.sendSensorData(sensor_quat)
        elif v == DataType.NTF_PPG and self.hasPPG() and self._ntf_on(NtfParam.NTF_PPG):
            dispatch(SensorDataType.DATA_TYPE_PPG, 3, 0)
        elif v == DataType.NTF_SPO2 and self.hasPPG() and self._ntf_on(NtfParam.NTF_SPO2):
            dispatch(SensorDataType.DATA_TYPE_SPO2, 3, 0)
        elif v == DataType.NTF_EULER_DATA and self.hasEuler():
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_EULER]
            dispatch(SensorDataType.DATA_TYPE_EULER, sensor_data.packageIndexLength + 1, 0)
        elif v == DataType.NTF_QUATERNION and (self.hasQuat() or self.isContainQAT6):
            # Prefer dedicated GForce quat slot when present; else IMU QAT6 slot.
            gforce_quat = self.sensorDatas[SensorDataType.DATA_TYPE_GFORCE_QUAT]
            if gforce_quat.sampleRate > 0:
                dispatch(SensorDataType.DATA_TYPE_GFORCE_QUAT, gforce_quat.packageIndexLength + 1, 0)
            else:
                dispatch(
                    SensorDataType.DATA_TYPE_QUATERNION,
                    self.sensorDatas[SensorDataType.DATA_TYPE_QUATERNION].packageIndexLength + 1,
                    0,
                )
        elif v == DataType.NTF_ACC and self.hasAcc():
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_ACC]
            dispatch(SensorDataType.DATA_TYPE_ACC, sensor_data.packageIndexLength + 1, 0)
        elif v == DataType.NTF_GYRO and self.hasGyro():
            sensor_data = self.sensorDatas[SensorDataType.DATA_TYPE_GYRO]
            dispatch(SensorDataType.DATA_TYPE_GYRO, sensor_data.packageIndexLength + 1, 0)

    def checkReadSamples(
        self,
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
                    lostPackageCounter = deltaPackageIndex - 1
                    sensorData.lostPackageCount = sensorData.lostPackageCount + lostPackageCounter
                    lostSampleCount = sensorData.packageSampleCount * lostPackageCounter

                    if lostPackageCounter < _MAX_ALLOWED_PACKAGE_INDEX_DELTA:
                        if lostSampleCount < 100:
                            self.readSamples(data, sensorData, 0, dataGap, lostSampleCount)
                    else:
                        # Illegal jump: signal assemble buffers should be re-synced.
                        self._watchdog_restart_pending = True
                        sensorData.clear()
                        if self._on_error is not None:
                            self._on_error(f"Illegal package index jump ({lostPackageCounter}); resetting stream state")
                        return False

                    if newPackageIndex == 0:
                        sensorData.lastPackageIndex = maxPackageIndex
                    else:
                        sensorData.lastPackageIndex = newPackageIndex - 1
                    sensorData.lastPackageCounter += lostPackageCounter

                    lostLog = (
                        "MSG|LOST SAMPLE|MAC|"
                        + str(sensorData.deviceMac)
                        + "|TYPE|"
                        + str(sensorData.dataType)
                        + "|COUNT|"
                        + str(lostSampleCount)
                    )
                    # print(lostLog)
                    if not _terminated and self._on_error is not None:
                        with contextlib.suppress(Exception):
                            self._on_error(lostLog)

                sensorData.lastPackageIndex = newPackageIndex

            if dataGap >= 0:
                self.readSamples(data, sensorData, dataOffset, dataGap, 0)

            sensorData.lastPackageCounter += 1
        except Exception as e:
            raise DataContextReadSamplesError(f"Error in checkReadSamples: {e}") from e
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
            for _channelIndex in range(sensorData.channelCount):
                channelSamples.append([])

        for _sampleIndex in range(sampleCount):
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

    def sendSensorData(self, sensorData: SensorData) -> None:
        """Batch internal samples and invoke the driver's ``publish_data`` callback.

        Called synchronously from the parser task; the driver marshals to the
        outbound buffer via :meth:`~synchroni_sensor_sdk.async_api.driver.base.Driver.schedule_publish_data`.
        """
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
        for _batchIndex in range(batchCount):
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

            if self.debugCSVPath is not None and self.debugCSVPath != "" and self.debugCSVWriter is None:
                try:
                    # File stays open for the lifetime of the debug CSV session.
                    self._debug_csv_file = open(  # noqa: SIM115
                        self.debugCSVPath, "w", newline="", encoding="utf-8"
                    )
                    self.debugCSVWriter = csv.writer(self._debug_csv_file)
                    header_append_keys = ["dataType", "sampleRate"]
                    channel_samples_header = list(vars(sensorDataResult.channelSamples[0][0]).keys())
                    for key_item in header_append_keys:
                        channel_samples_header.append(key_item)
                    self.debugCSVWriter.writerow(channel_samples_header)
                except Exception:
                    self._logger.exception("Failed to initialize debug CSV writer")

            if self.debugCSVWriter is not None:
                try:
                    for _i, channel_sample_list in enumerate(sensorDataResult.channelSamples):
                        for channel_sample in channel_sample_list:
                            row_data = []

                            for key in vars(channel_sample):
                                row_data.append(getattr(channel_sample, key))
                            row_data.append(sensorDataResult.dataType)
                            row_data.append(sensorDataResult.sampleRate)
                            self.debugCSVWriter.writerow(row_data)
                except Exception:
                    self._logger.exception("Failed to write debug CSV row")

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
            self._publish_data(sensor_data_to_public(sensorDataResult))

    async def process_universal_data(self) -> None:
        """Parser loop for universal-stream (RFSTAR) devices.

        Same role as :meth:`process_data` but always reassembles 0x55-framed
        packets from ``_concatDataBuffer`` before calling
        :meth:`_processDataPackage`.
        """
        while self._is_running:
            while self._is_running and self._rawDataBuffer.empty():
                await asyncio.sleep(0.01)
            if not self._is_running:
                break

            try:
                while self._is_running and not self._rawDataBuffer.empty():
                    data = self._rawDataBuffer.get_nowait()
                    self._concatDataBuffer.extend(data)
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
                    calc_crc = crc16_cal(self._concatDataBuffer[index + 2 : index + 2 + n], n)
                    if crc16 != calc_crc:
                        index += 1
                        continue
                    if self._is_data_transfering:
                        data_package = bytes(self._concatDataBuffer[index + 2 : index + 2 + n])
                        self._processDataPackage(data_package)
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
                    calc_crc = crc16_cal(self._concatDataBuffer[index + 2 : index + 2 + n], n)
                    if crc16 != calc_crc:
                        index += 1
                        continue
                    data_package = bytes(self._concatDataBuffer[index + 2 : index + 2 + n])

                    if not _terminated:
                        await self.gForce.async_on_cmd_response(data_package)
                    last_cut = index = index + 2 + n + 1
                    index += 1
                else:
                    index += 1

            if last_cut > 0:
                self._concatDataBuffer = self._concatDataBuffer[last_cut + 1 :]
                last_cut = -1
                index = 0
