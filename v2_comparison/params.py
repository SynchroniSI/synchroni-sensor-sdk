"""Apply shared capture profile to legacy and v2 sensors."""

from __future__ import annotations

from typing import Any

from synchroni_sensor_sdk.core.device import SetParamCommand
from v2_comparison.config import ALL_FILTER_KEYS, CaptureConfig


def _ntf_enabled(config: CaptureConfig, key: str) -> bool:
    return key in config.enabled_ntf


def build_v2_set_param(config: CaptureConfig) -> SetParamCommand:
    """Mirror examples/console.py: enable requested streams, disable filters, leave other NTFs at defaults."""
    command = SetParamCommand(
        enable_filter_50hz=False,
        enable_filter_60hz=False,
        enable_filter_hpf=False,
        enable_filter_lpf=False,
    )
    if _ntf_enabled(config, "EMG"):
        command.enable_ntf_emg = True
    if _ntf_enabled(config, "EEG"):
        command.enable_ntf_eeg = True
    if _ntf_enabled(config, "ECG"):
        command.enable_ntf_ecg = True
    if _ntf_enabled(config, "IMU"):
        command.enable_ntf_imu = True
    if _ntf_enabled(config, "BRTH"):
        command.enable_ntf_brth = True
    if _ntf_enabled(config, "IMPEDANCE"):
        command.enable_ntf_impedance = True
    if not _ntf_enabled(config, "ECG"):
        command.enable_ntf_ecg = False
    if not _ntf_enabled(config, "IMU"):
        command.enable_ntf_imu = False
    return command


def apply_legacy_params(sensor: Any, config: CaptureConfig) -> None:
    """Match examples/console.py: enable requested streams, disable filters, avoid turning off other NTFs."""
    for key in ALL_FILTER_KEYS:
        sensor.setParam(f"FILTER_{key}", "OFF")
    for key in config.enabled_ntf:
        sensor.setParam(f"NTF_{key}", "ON")
    # console.py explicitly disables these unless the capture profile re-enables them
    if not _ntf_enabled(config, "ECG"):
        sensor.setParam("NTF_ECG", "OFF")
    if not _ntf_enabled(config, "IMU"):
        sensor.setParam("NTF_IMU", "OFF")


def apply_v2_params(sensor: Any, config: CaptureConfig) -> None:
    sensor.set_param(build_v2_set_param(config))
