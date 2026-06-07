"""Convert internal parsing models to public v2 dataclasses.

``gforce.data_context`` works with mutable camelCase types from ``gforce.parsing_models``;
this module is the single boundary where those become immutable-style ``core.data``
dataclasses for callbacks. See ``gforce.parsing_models`` for why both exist.

TODO: Remove this module once ``gforce.data_context`` emits ``core.data`` types directly.
"""

from __future__ import annotations

from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import ParseSample, ParseSensorData
from synchroni_sensor_sdk.core.data import NtfDataType, Sample, SensorData


def _sample_to_public(sample: ParseSample) -> Sample:
    return Sample(
        raw_data=sample.rawData,
        data=int(sample.data) if isinstance(sample.data, float) else sample.data,
        impedance=int(sample.impedance),
        saturation=float(sample.saturation),
        sample_index=sample.sampleIndex,
        is_lost=sample.isLost,
        timestamp_ms=sample.timeStampInMs,
        channel_index=sample.channelIndex,
    )


def sensor_data_to_public(packet: ParseSensorData) -> SensorData:
    return SensorData(
        device_mac=packet.deviceMac,
        data_type=NtfDataType(packet.dataType),
        sample_rate=packet.sampleRate,
        channel_count=packet.channelCount,
        package_sample_count=packet.packageSampleCount,
        package_index_length=packet.packageIndexLength,
        channel_samples=[[_sample_to_public(s) for s in channel] for channel in packet.channelSamples],
        last_package_counter=packet.lastPackageCounter,
        last_package_index=packet.lastPackageIndex,
        resolution_bits=packet.resolutionBits,
        channel_mask=packet.channelMask,
        min_package_sample_count=packet.minPackageSampleCount,
        K=packet.K,
        lost_package_count=getattr(packet, "lostPackageCount", 0),
    )
