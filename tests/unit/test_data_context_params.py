"""Tests for NTF map / notify-flag rebuild without BLE."""

from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from synchroni_sensor_sdk.async_api.driver.gforce.data_context import DataContext, FeatureMaps, SensorDataType
from synchroni_sensor_sdk.async_api.driver.gforce.device_info import parse_device_info_to_public
from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import DataType, DeviceInfo, SensorData
from synchroni_sensor_sdk.async_api.driver.gforce.protocol import (
    DataSubscription,
    EmgRawDataConfig,
    ImuRawDataConfig,
    RawDataPacket,
    SampleResolution,
    SamplingRate,
)
from synchroni_sensor_sdk.core.params import NtfParam, ParamToggle


@pytest.fixture
def ctx() -> DataContext:
    gforce = MagicMock()
    gforce._is_universal_stream = False
    buf: asyncio.Queue[bytes] = asyncio.Queue()
    return DataContext(gforce, "AA:BB:CC:DD:EE:FF", buf, publish_data=lambda _d: None)


def test_default_imu_off_for_non_rfstar(ctx: DataContext) -> None:
    assert ctx.init_map[NtfParam.NTF_IMU] == ParamToggle.OFF
    assert ctx.init_map[NtfParam.NTF_GFORCE_ACC] == ParamToggle.OFF


def test_imu_master_sets_subs(ctx: DataContext) -> None:
    ctx.apply_imu_master(True)
    assert ctx.init_map[NtfParam.NTF_IMU] == ParamToggle.ON
    assert ctx.init_map[NtfParam.NTF_GFORCE_GYRO] == ParamToggle.ON
    ctx.init_map[NtfParam.NTF_GFORCE_ACC] = ParamToggle.OFF
    ctx.sync_imu_master_from_subs()
    assert ctx.init_map[NtfParam.NTF_IMU] == ParamToggle.OFF


def test_build_notify_flag_eeg(ctx: DataContext) -> None:
    ctx.featureMap = FeatureMaps.GFD_FEAT_EEG.value | FeatureMaps.GFD_FEAT_CONCAT_BLE.value
    ctx.init_map[NtfParam.NTF_EEG] = ParamToggle.ON
    flag = ctx.build_notify_data_flag()
    assert flag & DataSubscription.DNF_EEG
    assert flag & DataSubscription.DNF_CONCAT_BLE


def test_build_notify_flag_ppg(ctx: DataContext) -> None:
    ctx.featureMap = FeatureMaps.GFD_FEAT_PPG.value
    ctx.init_map[NtfParam.NTF_PPG] = ParamToggle.ON
    flag = ctx.build_notify_data_flag()
    assert flag & DataSubscription.DNF_PPG


def test_get_params_snapshot(ctx: DataContext) -> None:
    snap = ctx.get_params_snapshot()
    assert "NTF_EEG" in snap.ntf
    assert "FILTER_50HZ" in snap.filters


def test_lossy_packet_index_rollover_is_accepted(ctx: DataContext) -> None:
    data = SensorData()
    data.dataType = DataType.NTF_EEG
    data.packageIndexLength = 2
    data.packageSampleCount = 1
    data.lastPackageIndex = 65_530
    data.lastPackageCounter = 20
    ctx._is_data_transfering = True
    ctx.readSamples = MagicMock()  # type: ignore[method-assign]

    packet = bytes((int(DataType.NTF_EEG), 5, 0, 0))
    assert ctx.checkReadSamples(packet, data, 3, 0) is True
    assert data.lastPackageIndex == 5
    assert data.lastPackageCounter == 31


@pytest.mark.asyncio
async def test_post_init_imu_enable_builds_descriptors_before_decoding_imu() -> None:
    published = []
    gforce = SimpleNamespace(
        _is_universal_stream=False,
        get_imu_cap_data_config=AsyncMock(return_value=(0, 50, 1)),
        set_imu_raw_data_config=AsyncMock(),
        get_imu_raw_data_config=AsyncMock(
            return_value=ImuRawDataConfig(channel_count=6, fs=50, batch_len=1, accK=1.0, gyroK=1.0)
        ),
        set_subscription=AsyncMock(),
    )
    context = DataContext(gforce, "AA:BB:CC:DD:EE:FF", asyncio.Queue(), publish_data=published.append)
    context.featureMap = FeatureMaps.GFD_FEAT_IMU.value | FeatureMaps.GFD_FEAT_EEG.value
    assert context.sensorDatas[2].sampleRate == context.sensorDatas[3].sampleRate == 0

    async def subscribe(_flag):
        assert context.sensorDatas[2].sampleRate == context.sensorDatas[3].sampleRate == 50

    gforce.set_subscription.side_effect = subscribe
    context.apply_imu_master(True)
    eeg = SensorData()
    eeg.dataType = DataType.NTF_EEG
    eeg.sampleRate = 250
    eeg.channelCount = 2
    eeg.channelMask = 3
    eeg.packageSampleCount = 1
    eeg.minPackageSampleCount = 1
    eeg.resolutionBits = 24
    eeg.K = 1.0
    eeg.clear()
    context.sensorDatas[0] = eeg
    context._is_data_transfering = True

    await context.apply_subscription()
    context._processDataPackage(bytes((0x13, 0, 0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0)))
    context._processDataPackage(bytes((0x10, 1, 0, 0, 0, 1, 0, 0, 2)))

    assert [packet.data_type for packet in published] == [DataType.NTF_ACC, DataType.NTF_GYRO, DataType.NTF_EEG]
    assert [packet.sample_rate for packet in published] == [50, 50, 250]
    assert [sample.raw_data for sample in published[0].channel_samples[0]] == [1]
    assert [sample.raw_data for sample in published[1].channel_samples[0]] == [4]
    assert [sample.raw_data for sample in published[2].channel_samples[0]] == [-8_388_607]
    await context.apply_subscription()
    gforce.get_imu_raw_data_config.assert_awaited_once()


async def test_inactive_imu_packets_are_ignored(ctx):
    ctx.featureMap = FeatureMaps.GFD_FEAT_IMU.value
    ctx.isContainQAT6 = True
    ctx._is_data_transfering = True
    ctx._publish_data = MagicMock()
    for index in range(2):
        await ctx._process_ingress_fairly(RawDataPacket(struct.pack("<BH", DataType.NTF_IMU, index) + bytes(24), 123))
    ctx._publish_data.assert_not_called()
    assert ctx._parse_error_count == 0
    assert all(data.receivedMonotonicNs == 0 for data in ctx.sensorDatas)


async def test_qat6_orientation_metadata_matches_initialized_descriptors(ctx):
    ctx._device_info = DeviceInfo()
    ctx.gForce.get_imu_raw_data_config = AsyncMock(
        return_value=ImuRawDataConfig(channel_count=7, fs=50, batch_len=1, accK=1.0, gyroK=1.0)
    )
    ctx.gForce.get_imu_cap_data_config = AsyncMock(return_value=(7, 50, 1))
    ctx.gForce.set_imu_raw_data_config = AsyncMock()
    await ctx.initIMU(1)
    info = parse_device_info_to_public(ctx._device_info)
    assert info.channel_counts == {"acc": 3, "gyro": 3, "quat": 4, "euler": 3}
    assert info.sample_rates == dict.fromkeys(("acc", "gyro", "quat", "euler"), 50)


@pytest.mark.parametrize("rate, emg_enabled", [(500, True), (1000, True), (500, False)])
async def test_ultra_init_applies_function_mode_once(rate, emg_enabled):
    protocol = SimpleNamespace(
        _is_universal_stream=True,
        _managed_usb_transport=None,
        get_feature_map=AsyncMock(return_value=FeatureMaps.GFD_FEAT_EMG.value),
        get_emg_raw_data_config=AsyncMock(
            return_value=EmgRawDataConfig(
                fs=SamplingRate(rate), resolution=SampleResolution.BITS_16, extension=15, batch_len=240
            )
        ),
        set_emg_raw_data_config=AsyncMock(),
        set_function_switch=AsyncMock(return_value=True),
        set_package_id=AsyncMock(),
    )
    context = DataContext(protocol, "TEST-ULTRA", asyncio.Queue(), publish_data=lambda _packet: None)
    info = DeviceInfo()
    info.DeviceName = "gForceUltra"
    context.fetchDeviceInfo = AsyncMock(return_value=info)
    context.init_map[NtfParam.NTF_EMG] = ParamToggle.from_bool(emg_enabled)
    context.init_map[NtfParam.NTF_GEST] = ParamToggle.OFF
    context._emg_sample_rate = SamplingRate(rate)
    if not emg_enabled:
        context.isNewEMG = True  # Reinitializing a previously active native stream must still switch EMG off.

    await context.init(10)

    protocol.set_function_switch.assert_awaited_once_with(2 if emg_enabled else 0)
    if emg_enabled:
        assert protocol.set_emg_raw_data_config.await_args.args[0].fs == rate
    else:
        protocol.set_emg_raw_data_config.assert_not_awaited()


@pytest.mark.parametrize("rate", [SamplingRate.HZ_500, SamplingRate.HZ_1000])
def test_ultra_compressed_samples_and_timing(ctx, rate):
    published = []
    ctx._publish_data = published.append
    ctx._is_data_transfering = True
    data = ctx.sensorDatas[SensorDataType.DATA_TYPE_EMG]
    data.dataType, data.channelCount, data.packageIndexLength = DataType.NTF_EMG, 8, 2
    data.minPackageSampleCount, data.K = 15, 1.0
    config = EmgRawDataConfig(
        fs=rate, channel_mask=255, batch_len=240, resolution=SampleResolution.BITS_16, extension=15
    )
    ctx._apply_emg_packet_layout(data, config, rate, "force_ultra", legacy_emg=False)
    data.clear()
    payload = struct.pack("<8h", 0x0121, 0x0122, -0x10, -0x1F, 0, 0x7F00, -0x8000, 0x001F) * 15
    for index in range(2):
        ctx._processDataPackage(struct.pack("<BH", DataType.NTF_EMG, index) + payload)
    assert len(published) == 2
    assert [channel[0].raw_data for channel in published[0].channel_samples] == [36, 72, -1, -4, 0, 2032, -2048, 32768]
    samples = [sample for packet in published for sample in packet.channel_samples[0]]
    assert [sample.sample_index for sample in samples] == list(range(30))
    assert [sample.timestamp_ms for sample in samples] == [i * (1000 // int(rate)) for i in range(30)]
    assert not any(sample.is_lost for sample in samples)


async def test_pro_motion_decodes_captured_signed_32_bit_packets(ctx):
    published = []
    ctx._publish_data = published.append
    ctx._device_info = DeviceInfo()
    ctx._device_info.DeviceName = "gForcePro+"
    ctx.featureMap = FeatureMaps.GFD_FEAT_ACC.value | FeatureMaps.GFD_FEAT_GYRO.value
    ctx.init_map[NtfParam.NTF_GFORCE_ACC] = ctx.init_map[NtfParam.NTF_GFORCE_GYRO] = ParamToggle.ON
    ctx.gForce.set_subscription = AsyncMock()
    ctx._is_data_transfering = True
    await ctx.apply_subscription()
    ctx._processDataPackage(bytes.fromhex("819d0000feffe014010060f2ffff"))
    ctx._processDataPackage(bytes.fromhex("829d30f8ffff1efbffffca080000"))
    assert [packet.data_type for packet in published] == [DataType.NTF_ACC, DataType.NTF_GYRO]
    assert [packet.sample_rate for packet in published] == [50, 50]
    assert [[channel[0].raw_data for channel in packet.channel_samples] for packet in published] == [
        [-131072, 70880, -3488],
        [-2000, -1250, 2250],
    ]
    assert published[0].channel_samples[0][0].data == pytest.approx(-2.0)
