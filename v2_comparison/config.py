"""Default capture profile shared by legacy and v2 recorders."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_SCAN_MS = 5000
DEFAULT_RECORD_S = 30
DEFAULT_PACKAGE_SAMPLE_COUNT = 10
DEFAULT_POWER_REFRESH_MS = 5000
DEFAULT_ENABLED_NTF: tuple[str, ...] = ("EEG",)
# Legacy startDataNotification sleeps this long after BLE subscribe; v2 recorder applies it explicitly.
POST_STREAM_SETTLE_S = 0.2

DEVICE_NAME_PREFIXES = ("OB", "Sync", "Orion")
MIN_RSSI = -80

ALL_NTF_KEYS = ("EMG", "EEG", "ECG", "IMU", "BRTH", "IMPEDANCE")
ALL_FILTER_KEYS = ("50HZ", "60HZ", "HPF", "LPF")


@dataclass
class CaptureConfig:
    scan_ms: int = DEFAULT_SCAN_MS
    record_s: int = DEFAULT_RECORD_S
    package_sample_count: int = DEFAULT_PACKAGE_SAMPLE_COUNT
    power_refresh_interval_ms: int = DEFAULT_POWER_REFRESH_MS
    enabled_ntf: tuple[str, ...] = field(default_factory=lambda: DEFAULT_ENABLED_NTF)

    def to_dict(self) -> dict[str, object]:
        return {
            "scan_ms": self.scan_ms,
            "record_s": self.record_s,
            "package_sample_count": self.package_sample_count,
            "power_refresh_interval_ms": self.power_refresh_interval_ms,
            "enabled_ntf": list(self.enabled_ntf),
            "filters_off": list(ALL_FILTER_KEYS),
            "disabled_ntf": [k for k in ALL_NTF_KEYS if k not in self.enabled_ntf],
            "post_stream_settle_s": POST_STREAM_SETTLE_S,
        }


def default_capture_config() -> CaptureConfig:
    return CaptureConfig()
