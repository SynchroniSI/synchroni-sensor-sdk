"""Tests for NTF map / notify-flag rebuild without BLE."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from synchroni_sensor_sdk.async_api.driver.gforce.data_context import DataContext, FeatureMaps
from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import DataType, SensorData
from synchroni_sensor_sdk.async_api.driver.gforce.protocol import DataSubscription, ImuRawDataConfig
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
