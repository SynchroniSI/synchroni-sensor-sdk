"""Canonical capture schema: rows, manifest, and CSV/JSON I/O."""

from __future__ import annotations

import csv
import json
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any, cast

SAMPLE_CSV_NAME = "samples.csv"
MANIFEST_JSON_NAME = "manifest.json"

SAMPLE_FIELDNAMES = [
    "recv_mono_ns",
    "impl",
    "device_mac",
    "data_type",
    "channel_index",
    "sample_index",
    "is_lost",
    "raw_data",
    "data",
    "impedance",
    "saturation",
    "timestamp_ms",
    "package_index",
    "package_counter",
    "sample_rate",
    "channel_count",
    "package_sample_count",
]


@dataclass
class SampleRow:
    recv_mono_ns: int
    impl: str
    device_mac: str
    data_type: int
    channel_index: int
    sample_index: int
    is_lost: bool
    raw_data: int
    data: int
    impedance: int
    saturation: float
    timestamp_ms: int
    package_index: int
    package_counter: int
    sample_rate: int
    channel_count: int
    package_sample_count: int

    def to_csv_row(self) -> dict[str, object]:
        row = asdict(self)
        row["is_lost"] = int(self.is_lost)
        return row

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> SampleRow:
        return cls(
            recv_mono_ns=int(row["recv_mono_ns"]),
            impl=row["impl"],
            device_mac=row["device_mac"],
            data_type=int(row["data_type"]),
            channel_index=int(row["channel_index"]),
            sample_index=int(row["sample_index"]),
            is_lost=bool(int(row["is_lost"])),
            raw_data=int(row["raw_data"]),
            data=int(row["data"]),
            impedance=int(row["impedance"]),
            saturation=float(row["saturation"]),
            timestamp_ms=int(row["timestamp_ms"]),
            package_index=int(row["package_index"]),
            package_counter=int(row["package_counter"]),
            sample_rate=int(row["sample_rate"]),
            channel_count=int(row["channel_count"]),
            package_sample_count=int(row["package_sample_count"]),
        )


@dataclass
class DeviceManifest:
    mac: str
    name: str
    model: str
    hardware_version: str
    firmware_version: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class CaptureStats:
    packets: int = 0
    samples: int = 0
    samples_by_data_type: dict[str, int] = field(default_factory=dict)
    dropped_packets: int = 0
    extras: dict[str, float | int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "packets": self.packets,
            "samples": self.samples,
            "samples_by_data_type": self.samples_by_data_type,
            "dropped_packets": self.dropped_packets,
        }
        result.update(self.extras)
        return result


@dataclass
class Manifest:
    impl: str
    started_at_utc: str
    ended_at_utc: str
    duration_s: int
    device: DeviceManifest
    config: dict[str, object]
    stats: CaptureStats

    def to_dict(self) -> dict[str, object]:
        return {
            "impl": self.impl,
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "duration_s": self.duration_s,
            "device": self.device.to_dict(),
            "config": self.config,
            "stats": self.stats.to_dict(),
        }


class SampleCollector:
    """Thread-safe buffer for normalized sample rows."""

    def __init__(self) -> None:
        self._rows: list[SampleRow] = []
        self._packets = 0
        self._lock = threading.Lock()

    def add_packet(self, rows: list[SampleRow]) -> None:
        if not rows:
            return
        with self._lock:
            self._packets += 1
            self._rows.extend(rows)

    def snapshot(self) -> tuple[list[SampleRow], int]:
        with self._lock:
            return list(self._rows), self._packets

    def build_stats(self, dropped_packets: int = 0) -> CaptureStats:
        rows, packets = self.snapshot()
        by_type: Counter[str] = Counter()
        for row in rows:
            by_type[str(row.data_type)] += 1
        extras: dict[str, float | int] = {}
        if rows:
            by_recv = sorted(rows, key=lambda r: r.recv_mono_ns)
            extras["first_recv_mono_ns"] = by_recv[0].recv_mono_ns
            extras["last_recv_mono_ns"] = by_recv[-1].recv_mono_ns
            extras["recv_window_s"] = (by_recv[-1].recv_mono_ns - by_recv[0].recv_mono_ns) / 1e9
        if packets > 0:
            extras["samples_per_packet"] = len(rows) / packets
        return CaptureStats(
            packets=packets,
            samples=len(rows),
            samples_by_data_type=dict(by_type),
            dropped_packets=dropped_packets,
            extras=extras,
        )


class RecordingError(Exception):
    """Raised when a capture session fails."""


def default_output_dir(impl: str, mac: str) -> Path:
    from datetime import datetime

    safe_mac = mac.replace(":", "").upper()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path(__file__).resolve().parent / "captures"
    return root / f"{impl}_{safe_mac}_{stamp}"


def write_samples_csv(path: Path, rows: list[SampleRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row())


def read_samples_csv(path: Path) -> list[SampleRow]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [SampleRow.from_csv_row(row) for row in reader]


def write_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")


def read_manifest(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def load_capture_dir(path: Path) -> tuple[list[SampleRow], dict[str, Any]]:
    samples_path = path / SAMPLE_CSV_NAME
    manifest_path = path / MANIFEST_JSON_NAME
    if not samples_path.is_file():
        raise FileNotFoundError(f"Missing {SAMPLE_CSV_NAME} in {path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing {MANIFEST_JSON_NAME} in {path}")
    return read_samples_csv(samples_path), read_manifest(manifest_path)


def monotonic_recv_ns() -> int:
    return time.monotonic_ns()
