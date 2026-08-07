"""Unit tests for v2 parity types and param helpers (no hardware)."""

from __future__ import annotations

from synchroni_sensor_sdk.async_api.driver.gforce.device_info import parse_device_info_to_public
from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import ParseDeviceInfo
from synchroni_sensor_sdk.core.data import NtfDataType
from synchroni_sensor_sdk.core.device import BleChipType, SetParamCommand
from synchroni_sensor_sdk.core.params import IMU_SUB_PARAMS, NtfParam, ParamToggle


def test_ntf_data_types_include_rebased_modalities() -> None:
    assert int(NtfDataType.NTF_EULER_DATA) == 0x4
    assert int(NtfDataType.NTF_QUATERNION) == 0x5
    assert int(NtfDataType.NTF_GEST) == 0x07
    assert int(NtfDataType.NTF_SPO2) == 0x17
    assert int(NtfDataType.NTF_PPG) == 0x18


def test_ntf_param_keys_cover_imu_subs() -> None:
    assert NtfParam.NTF_GFORCE_ACC in IMU_SUB_PARAMS
    assert NtfParam.NTF_GEST in NtfParam
    assert NtfParam.NTF_PPG in NtfParam
    assert ParamToggle.from_bool(True).as_bool() is True


def test_set_param_command_fields() -> None:
    cmd = SetParamCommand(
        enable_ntf_ppg=True,
        enable_ntf_gest=False,
        enable_ntf_mag_angle=True,
        enable_ntf_acc=True,
    )
    assert cmd.enable_ntf_ppg is True
    assert cmd.enable_ntf_gest is False
    assert cmd.enable_ntf_mag_angle is True
    assert cmd.enable_ntf_acc is True


def test_parse_device_info_includes_rates_and_mtu() -> None:
    info = ParseDeviceInfo()
    info.DeviceName = "SyncEEG-1"
    info.ModelName = "Test"
    info.HardwareVersion = "1"
    info.FirmwareVersion = "2"
    info.EegChannelCount = 4
    info.EegSampleRate = 250
    info.PpgChannelCount = 2
    info.PpgSampleRate = 50
    info.MTUSize = 247
    public = parse_device_info_to_public(info)
    assert public.name == "SyncEEG-1"
    assert public.model == "Test"
    assert public.channel_counts["eeg"] == 4
    assert public.sample_rates["eeg"] == 250
    assert public.channel_counts["ppg"] == 2
    assert public.sample_rates["ppg"] == 50
    assert public.mtu_size == 247


def test_response_text_and_hardware_revision() -> None:
    from synchroni_sensor_sdk.async_api.driver.gforce.protocol import (
        _response_hardware_revision,
        _response_text,
    )

    assert _response_text(b"SyncEEG\x00\x00") == "SyncEEG"
    assert _response_hardware_revision(bytes([3, 1])) == "3.1"
    assert _response_hardware_revision(b"1.2.0\x00") == "1.2.0"


def test_ble_chip_type_values() -> None:
    assert BleChipType.OYM == 0
    assert BleChipType.RFSTAR == 1
