"""Tests for NTF map / notify-flag rebuild without BLE."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from synchroni_sensor_sdk.async_api.driver.gforce.data_context import DataContext, FeatureMaps
from synchroni_sensor_sdk.async_api.driver.gforce.protocol import DataSubscription
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
