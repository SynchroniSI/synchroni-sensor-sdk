"""Map legacy init-time device info to the public v2 ``DeviceInfo`` dataclass.

``ParseDeviceInfo`` keeps the wide legacy field layout used during BLE init;
this module collapses it into the slim public shape. See ``gforce.parsing_models``.
"""

from __future__ import annotations

from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import ParseDeviceInfo
from synchroni_sensor_sdk.core.device import DeviceInfo, published_product_specification


def _put_count(counts: dict[str, int], key: str, value: int) -> None:
    if value:
        counts[key] = value


def _put_rate(rates: dict[str, int], key: str, value: int) -> None:
    if value:
        rates[key] = value


def parse_device_info_to_public(
    info: ParseDeviceInfo,
    *,
    supported_streams: frozenset[str] = frozenset(),
    supported_filters: frozenset[str] = frozenset(),
) -> DeviceInfo:
    channel_counts: dict[str, int] = {}
    sample_rates: dict[str, int] = {}

    _put_count(channel_counts, "emg", info.EmgChannelCount)
    _put_rate(sample_rates, "emg", info.EmgSampleRate)
    _put_count(channel_counts, "eeg", info.EegChannelCount)
    _put_rate(sample_rates, "eeg", info.EegSampleRate)
    _put_count(channel_counts, "ecg", info.EcgChannelCount)
    _put_rate(sample_rates, "ecg", info.EcgSampleRate)
    _put_count(channel_counts, "acc", info.AccChannelCount)
    _put_rate(sample_rates, "acc", info.AccSampleRate)
    _put_count(channel_counts, "gyro", info.GyroChannelCount)
    _put_rate(sample_rates, "gyro", info.GyroSampleRate)
    _put_count(channel_counts, "brth", info.BrthChannelCount)
    _put_rate(sample_rates, "brth", info.BrthSampleRate)
    _put_count(channel_counts, "mag_angle", info.MagAngleChannelCount)
    _put_rate(sample_rates, "mag_angle", info.MagAngleSampleRate)
    _put_count(channel_counts, "ppg", info.PpgChannelCount)
    _put_rate(sample_rates, "ppg", info.PpgSampleRate)
    _put_count(channel_counts, "spo2", info.Spo2ChannelCount)
    _put_rate(sample_rates, "spo2", info.Spo2SampleRate)
    _put_count(channel_counts, "euler", info.EulerChannelCount)
    _put_rate(sample_rates, "euler", info.EulerSampleRate)
    _put_count(channel_counts, "quat", info.QuatChannelCount)
    _put_rate(sample_rates, "quat", info.QuatSampleRate)
    _put_count(channel_counts, "gest", info.GestChannelCount)

    return DeviceInfo(
        name=info.DeviceName,
        model=info.ModelName or info.DeviceName,
        hardware_version=info.HardwareVersion,
        firmware_version=info.FirmwareVersion,
        channel_counts=channel_counts,
        sample_rates=sample_rates,
        mtu_size=info.MTUSize,
        supported_streams=supported_streams,
        supported_filters=supported_filters,
        product_specification=published_product_specification(
            info.DeviceName,
            info.ModelName,
            eeg_channel_count=info.EegChannelCount,
        ),
    )
