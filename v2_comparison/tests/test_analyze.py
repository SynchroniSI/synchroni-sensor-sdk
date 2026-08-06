from synchroni_sensor_sdk.core.data import NtfDataType
from v2_comparison.analyze import compare_captures
from v2_comparison.schema import SampleRow

_ECG_DATA_TYPE = int(NtfDataType.NTF_ECG)


def _row(
    impl: str,
    sample_index: int,
    data: int,
    *,
    is_lost: bool = False,
    recv_mono_ns: int | None = None,
) -> SampleRow:
    return SampleRow(
        recv_mono_ns=recv_mono_ns if recv_mono_ns is not None else sample_index * 2_000_000,
        impl=impl,
        device_mac="AA:BB:CC:DD:EE:FF",
        data_type=_ECG_DATA_TYPE,
        channel_index=0,
        sample_index=sample_index,
        is_lost=is_lost,
        raw_data=data,
        data=data,
        impedance=0,
        saturation=0.0,
        timestamp_ms=sample_index * 2,
        package_index=sample_index // 8,
        package_counter=sample_index // 8,
        sample_rate=500,
        channel_count=1,
        package_sample_count=8,
    )


def _manifest(impl: str, samples: int, *, duration_s: float = 0.2) -> dict:
    return {
        "impl": impl,
        "duration_s": duration_s,
        "config": {
            "scan_ms": 5000,
            "record_s": 30,
            "package_sample_count": 10,
            "power_refresh_interval_ms": 5000,
            "enabled_ntf": ["ECG"],
        },
        "device": {
            "mac": "AA:BB:CC:DD:EE:FF",
            "name": "SyncTest",
            "model": "M1",
            "hardware_version": "1",
            "firmware_version": "1",
        },
        "stats": {
            "packets": samples // 8,
            "samples": samples,
            "samples_by_data_type": {str(_ECG_DATA_TYPE): samples},
            "dropped_packets": 0,
            "recv_window_s": duration_s,
        },
    }


def test_compare_identical_captures_pass() -> None:
    rows = [_row("legacy", i, i * 10) for i in range(100)]
    manifest = _manifest("legacy", 100)
    result = compare_captures(rows, manifest, rows, manifest, label_a="legacy", label_b="v2")
    assert not any(c.status == "FAIL" for c in result.checks)
    assert any(c.name == "sample_count" and c.status == "PASS" for c in result.checks)
    assert any(c.name == "sample_rate_NTF_ECG_ch0" and c.status == "PASS" for c in result.checks)


def test_compare_missing_stream_fails() -> None:
    rows_a = [_row("legacy", i, i) for i in range(50)]
    manifest_a = _manifest("legacy", 50)
    manifest_b = _manifest("v2", 0)
    result = compare_captures(rows_a, manifest_a, [], manifest_b)
    assert any(c.name == "sample_count" and c.status == "FAIL" for c in result.checks)


def test_compare_count_mismatch_warns() -> None:
    rows_a = [_row("legacy", i, i) for i in range(100)]
    rows_b = [_row("v2", i, i) for i in range(80)]
    manifest_a = _manifest("legacy", 100)
    manifest_b = _manifest("v2", 80)
    result = compare_captures(rows_a, manifest_a, rows_b, manifest_b, tolerance_pct=5.0)
    assert any(c.name == "sample_count" and c.status == "WARN" for c in result.checks)
    assert any(c.name == "packet_count" and c.status == "WARN" for c in result.checks)


def test_callback_arrival_is_informational() -> None:
    # Batched rows share recv_mono_ns → low callback spacing vs high index rate.
    rows = [_row("legacy", i, i, recv_mono_ns=1_000_000_000 if i < 50 else 2_000_000_000) for i in range(100)]
    manifest = _manifest("legacy", 100)
    result = compare_captures(rows, manifest, rows, manifest)
    callback = next(c for c in result.checks if c.name == "callback_arrival_NTF_ECG_ch0")
    assert callback.status == "INFO"
    sample_rate = next(c for c in result.checks if c.name == "sample_rate_NTF_ECG_ch0")
    assert sample_rate.status == "PASS"


def test_align_overlap_report() -> None:
    rows_a = [_row("legacy", i, i) for i in range(20)]
    rows_b = [_row("v2", i, i + 1) for i in range(20)]
    manifest = _manifest("legacy", 20, duration_s=20 / 500)
    result = compare_captures(rows_a, manifest, rows_b, manifest, align=True)
    assert any(c.name.startswith("align_") for c in result.checks)
