"""Map legacy and v2 SensorData payloads to canonical SampleRow."""

from __future__ import annotations

from typing import Any

from synchroni_sensor_sdk.core.data import NtfDataType, Sample
from synchroni_sensor_sdk.core.data import SensorData as V2SensorData
from v2_comparison.schema import SampleRow, monotonic_recv_ns


def _int_data_type(value: Any) -> int:
    if isinstance(value, NtfDataType):
        return int(value)
    return int(value)


def _expand_channel_samples(
    channel_samples: list[list[Any]],
    *,
    impl: str,
    device_mac: str,
    data_type: int,
    sample_rate: int,
    channel_count: int,
    package_sample_count: int,
    package_index: int,
    package_counter: int,
    recv_mono_ns: int | None = None,
) -> list[SampleRow]:
    recv_ns = recv_mono_ns if recv_mono_ns is not None else monotonic_recv_ns()
    rows: list[SampleRow] = []
    for channel_index, samples in enumerate(channel_samples):
        for sample in samples:
            resolved_channel = channel_index
            if hasattr(sample, "channel_index") and sample.channel_index:
                resolved_channel = sample.channel_index
            elif hasattr(sample, "channelIndex") and sample.channelIndex:
                resolved_channel = sample.channelIndex
            rows.append(
                _sample_to_row(
                    sample,
                    impl=impl,
                    device_mac=device_mac,
                    data_type=data_type,
                    channel_index=resolved_channel,
                    sample_rate=sample_rate,
                    channel_count=channel_count,
                    package_sample_count=package_sample_count,
                    package_index=package_index,
                    package_counter=package_counter,
                    recv_mono_ns=recv_ns,
                )
            )
    return rows


def _sample_to_row(
    sample: Any,
    *,
    impl: str,
    device_mac: str,
    data_type: int,
    channel_index: int,
    sample_rate: int,
    channel_count: int,
    package_sample_count: int,
    package_index: int,
    package_counter: int,
    recv_mono_ns: int,
) -> SampleRow:
    if isinstance(sample, Sample):
        return SampleRow(
            recv_mono_ns=recv_mono_ns,
            impl=impl,
            device_mac=device_mac,
            data_type=data_type,
            channel_index=sample.channel_index if sample.channel_index else channel_index,
            sample_index=sample.sample_index,
            is_lost=sample.is_lost,
            raw_data=sample.raw_data,
            data=sample.data,
            impedance=sample.impedance,
            saturation=float(sample.saturation),
            timestamp_ms=sample.timestamp_ms,
            package_index=package_index,
            package_counter=package_counter,
            sample_rate=sample_rate,
            channel_count=channel_count,
            package_sample_count=package_sample_count,
        )

    channel_idx = getattr(sample, "channelIndex", channel_index)
    return SampleRow(
        recv_mono_ns=recv_mono_ns,
        impl=impl,
        device_mac=device_mac,
        data_type=data_type,
        channel_index=channel_idx,
        sample_index=getattr(sample, "sampleIndex", 0),
        is_lost=bool(getattr(sample, "isLost", False)),
        raw_data=int(getattr(sample, "rawData", 0)),
        data=int(getattr(sample, "data", 0)),
        impedance=int(getattr(sample, "impedance", 0)),
        saturation=float(getattr(sample, "saturation", 0.0)),
        timestamp_ms=int(getattr(sample, "timeStampInMs", 0)),
        package_index=package_index,
        package_counter=package_counter,
        sample_rate=sample_rate,
        channel_count=channel_count,
        package_sample_count=package_sample_count,
    )


def normalize_v2(data: V2SensorData, *, impl: str = "v2") -> list[SampleRow]:
    return _expand_channel_samples(
        data.channel_samples,
        impl=impl,
        device_mac=data.device_mac,
        data_type=_int_data_type(data.data_type),
        sample_rate=data.sample_rate,
        channel_count=data.channel_count,
        package_sample_count=data.package_sample_count,
        package_index=data.last_package_index,
        package_counter=data.last_package_counter,
    )


def normalize_legacy(data: Any, *, impl: str = "legacy") -> list[SampleRow]:
    device_mac = getattr(data, "deviceMac", "")
    data_type = _int_data_type(getattr(data, "dataType", 0))
    return _expand_channel_samples(
        getattr(data, "channelSamples", []),
        impl=impl,
        device_mac=device_mac,
        data_type=data_type,
        sample_rate=int(getattr(data, "sampleRate", 0)),
        channel_count=int(getattr(data, "channelCount", 0)),
        package_sample_count=int(getattr(data, "packageSampleCount", 0)),
        package_index=int(getattr(data, "lastPackageIndex", 0)),
        package_counter=int(getattr(data, "lastPackageCounter", 0)),
    )


def data_type_name(value: int) -> str:
    try:
        return NtfDataType(value).name
    except ValueError:
        return f"UNKNOWN_{value:#x}"
