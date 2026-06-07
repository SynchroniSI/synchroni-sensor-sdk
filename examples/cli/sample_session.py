"""Connect + short stream session helpers for the CLI."""

from __future__ import annotations

import csv
import statistics
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from synchroni_sensor_sdk import SensorHub
from synchroni_sensor_sdk.async_api.sensor_hub import ScanResult
from synchroni_sensor_sdk.core.data import NtfDataType, SensorData
from synchroni_sensor_sdk.core.device import DeviceState, SetParamCommand
from synchroni_sensor_sdk.sync_api.sensor import Sensor

from cli import display as cli_display

console = Console()

PACKAGE_SAMPLE_COUNT = 10
POWER_REFRESH_INTERVAL_MS = 5000
# Cap raw values retained for summary stats (avoid large RAM on long streams).
_MAX_VALUES_PER_TYPE = 20_000

DEFAULT_STREAM_PARAMS = SetParamCommand(
    enable_ntf_ecg=False,
    enable_ntf_imu=False,
    enable_filter_50hz=False,
    enable_filter_60hz=False,
    enable_filter_hpf=False,
    enable_filter_lpf=False,
)

CSV_HEADER = (
    "received_at_s",
    "device_mac",
    "data_type",
    "sample_rate",
    "channel_index",
    "sample_index",
    "timestamp_ms",
    "data",
    "raw_data",
    "impedance",
    "saturation",
    "is_lost",
    "lost_package_count",
)


@dataclass
class TypeStats:
    packets: int = 0
    samples: int = 0
    lost_packages: int = 0
    values: list[float] = field(default_factory=list)
    channel_count: int = 0
    sample_rate: int = 0


class SampleCollector:
    """Thread-safe accumulator for streaming sample packets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.packets = 0
        self.by_type: dict[NtfDataType, TypeStats] = defaultdict(TypeStats)
        self.first_packet_at: float | None = None
        self.last_packet_at: float | None = None
        self.errors: list[str] = []

    def on_data(self, data: SensorData) -> None:
        now = time.monotonic()
        sample_count = sum(len(ch) for ch in data.channel_samples)
        values: list[float] = []
        for ch in data.channel_samples:
            for sample in ch:
                if not sample.is_lost:
                    values.append(float(sample.data))

        with self._lock:
            if self.first_packet_at is None:
                self.first_packet_at = now
            self.last_packet_at = now
            self.packets += 1
            stats = self.by_type[data.data_type]
            stats.packets += 1
            stats.samples += sample_count
            stats.lost_packages += data.lost_package_count
            stats.channel_count = max(stats.channel_count, data.channel_count)
            stats.sample_rate = data.sample_rate or stats.sample_rate
            remaining = max(0, _MAX_VALUES_PER_TYPE - len(stats.values))
            if remaining:
                stats.values.extend(values[:remaining])

    def on_error(self, reason: str) -> None:
        with self._lock:
            self.errors.append(reason)

    def snapshot(self) -> tuple[int, dict[NtfDataType, TypeStats], float | None, list[str]]:
        with self._lock:
            by_type = {
                dtype: TypeStats(
                    packets=s.packets,
                    samples=s.samples,
                    lost_packages=s.lost_packages,
                    values=list(s.values),
                    channel_count=s.channel_count,
                    sample_rate=s.sample_rate,
                )
                for dtype, s in self.by_type.items()
            }
            duration = None
            if self.first_packet_at is not None and self.last_packet_at is not None:
                duration = max(self.last_packet_at - self.first_packet_at, 0.0)
            return self.packets, by_type, duration, list(self.errors)


class CsvSampleWriter:
    """Thread-safe CSV sink for samples (write path suited for sync callbacks)."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._file = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_HEADER)
        self.rows = 0

    def on_data(self, data: SensorData) -> None:
        received_at = time.monotonic() - self._t0
        data_type = data.data_type.name
        rows: list[tuple[object, ...]] = []
        for channel_samples in data.channel_samples:
            for sample in channel_samples:
                rows.append(
                    (
                        f"{received_at:.6f}",
                        data.device_mac,
                        data_type,
                        data.sample_rate,
                        sample.channel_index,
                        sample.sample_index,
                        sample.timestamp_ms,
                        sample.data,
                        sample.raw_data,
                        sample.impedance,
                        sample.saturation,
                        int(sample.is_lost),
                        data.lost_package_count,
                    )
                )
        if not rows:
            return
        with self._lock:
            self._writer.writerows(rows)
            self.rows += len(rows)

    def close(self) -> None:
        with self._lock:
            self._file.flush()
            self._file.close()


def pick_first_device(devices: list[ScanResult], *, mac: str | None = None) -> ScanResult | None:
    """Pick strongest-RSSI device, or a specific MAC when requested."""
    if not devices:
        return None
    if mac is not None:
        needle = mac.strip().lower()
        matches = [d for d in devices if d.mac_address.lower() == needle]
        if not matches:
            return None
        return max(matches, key=lambda d: d.rssi)
    return max(devices, key=lambda d: d.rssi)


def print_stream_summary(
    *,
    device: ScanResult,
    adapter_id: str,
    stream_seconds: float,
    wall_s: float,
    collector: SampleCollector,
    dropped_packets: int,
    battery: int | None,
    model: str | None,
    csv_path: Path | None = None,
    csv_rows: int | None = None,
) -> None:
    packets, by_type, data_span_s, errors = collector.snapshot()

    summary = Table(title="Stream summary", show_header=True, header_style="bold")
    summary.add_column("Field", style="cyan")
    summary.add_column("Value")
    summary.add_row("device", f"{device.name or '-'} ({device.mac_address})")
    summary.add_row("adapter", adapter_id)
    summary.add_row("scan rssi", str(device.rssi))
    if model:
        summary.add_row("model", model)
    if battery is not None:
        summary.add_row("battery", f"{battery}%")
    summary.add_row("requested duration", f"{stream_seconds:.1f}s")
    summary.add_row("wall time", f"{wall_s:.2f}s")
    summary.add_row("data span", f"{data_span_s:.2f}s" if data_span_s is not None else "-")
    summary.add_row("packets", str(packets))
    summary.add_row("dropped (SDK buffer)", str(dropped_packets))
    if csv_path is not None:
        summary.add_row("csv", str(csv_path))
        summary.add_row("csv rows", str(csv_rows if csv_rows is not None else 0))
    console.print(summary)

    if by_type:
        types = Table(title="Per data type", show_header=True, header_style="bold")
        types.add_column("Type", style="cyan")
        types.add_column("Packets", justify="right")
        types.add_column("Samples", justify="right")
        types.add_column("Channels", justify="right")
        types.add_column("Rate (device)", justify="right")
        types.add_column("Hz (est.)", justify="right")
        types.add_column("Lost pkgs", justify="right")
        types.add_column("min", justify="right")
        types.add_column("max", justify="right")
        types.add_column("mean", justify="right")

        for dtype, stats in sorted(by_type.items(), key=lambda item: item[0].name):
            est_hz = "-"
            channels = max(stats.channel_count, 1)
            if data_span_s and data_span_s > 0 and stats.samples:
                # Per-channel rate (device rate is also per-channel).
                est_hz = f"{stats.samples / channels / data_span_s:.1f}"
            vals = stats.values
            min_v = f"{min(vals):.3f}" if vals else "-"
            max_v = f"{max(vals):.3f}" if vals else "-"
            mean_v = f"{statistics.fmean(vals):.3f}" if vals else "-"
            types.add_row(
                dtype.name,
                str(stats.packets),
                str(stats.samples),
                str(stats.channel_count),
                str(stats.sample_rate or "-"),
                est_hz,
                str(stats.lost_packages),
                min_v,
                max_v,
                mean_v,
            )
        console.print(types)
    else:
        console.print("[yellow]No sample packets received during the stream window.[/yellow]")

    if errors:
        console.print("[red]Errors during session:[/red]")
        for err in errors:
            console.print(f"  • {err}")


def default_csv_path(*, mac: str, adapter_id: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_mac = mac.replace(":", "").lower()
    safe_adapter = adapter_id.replace(":", "_").replace("/", "_")
    return Path(f"collect-{safe_mac}-{safe_adapter}-{stamp}.csv")


def _scan_and_pick(
    hub: SensorHub,
    *,
    adapter_id: str,
    scan_timeout_ms: int,
    mac: str | None,
) -> tuple[ScanResult, str] | None:
    console.print(
        f"Scanning for [bold]{scan_timeout_ms}[/bold] ms (adapter=[cyan]{adapter_id}[/cyan])…"
    )
    devices = hub.scan(scan_timeout_ms, adapter_id=adapter_id)
    if not devices:
        console.print("[yellow]No devices found.[/yellow]")
        return None

    cli_display.print_devices(devices)
    target = pick_first_device(devices, mac=mac)
    if target is None:
        console.print(f"[red]No scanned device matches MAC {mac!r}.[/red]")
        return None
    return target, target.adapter_id or adapter_id


def _open_ready_sensor(
    hub: SensorHub,
    *,
    target: ScanResult,
    connect_adapter: str,
) -> Sensor | None:
    console.print(
        f"Connecting to [bold]{target.name or target.mac_address}[/bold] "
        f"([cyan]{target.mac_address}[/cyan], rssi={target.rssi}) via "
        f"[cyan]{connect_adapter}[/cyan]…"
    )
    try:
        sensor = hub.connect(target.mac_address, adapter_id=connect_adapter)
    except Exception as exc:
        console.print(f"[red]hub.connect failed:[/red] {exc}")
        return None

    if sensor.device_state() != DeviceState.READY:
        console.print(
            f"[red]Device not READY after hub.connect (state={sensor.device_state()!s}).[/red]"
        )
        try:
            hub.disconnect(target.mac_address)
        except Exception:
            pass
        return None

    if not sensor.is_inited():
        sensor.set_param(DEFAULT_STREAM_PARAMS)
        sensor.init(PACKAGE_SAMPLE_COUNT, POWER_REFRESH_INTERVAL_MS)
    return sensor


def _run_stream_session(
    hub: SensorHub,
    *,
    adapter_id: str,
    scan_timeout_ms: int,
    stream_seconds: float,
    mac: str | None,
    data_callbacks: list[Callable[[SensorData], None]],
    error_callbacks: list[Callable[[str], None]],
    csv_path: Path | None = None,
    csv_rows: int | None = None,
) -> int:
    picked = _scan_and_pick(
        hub,
        adapter_id=adapter_id,
        scan_timeout_ms=scan_timeout_ms,
        mac=mac,
    )
    if picked is None:
        return 1
    target, connect_adapter = picked

    sensor = _open_ready_sensor(hub, target=target, connect_adapter=connect_adapter)
    if sensor is None:
        return 1

    collector = SampleCollector()

    def on_data(data: SensorData) -> None:
        collector.on_data(data)
        for cb in data_callbacks:
            cb(data)

    def on_error(reason: str) -> None:
        collector.on_error(reason)
        for cb in error_callbacks:
            cb(reason)

    sensor.register_data_callback(on_data)
    sensor.register_error_callback(on_error)

    model: str | None = None
    battery: int | None = None
    started = False
    t0 = time.monotonic()

    try:
        try:
            info = sensor.device_info()
            model = getattr(info, "model", None) or str(info)
        except Exception as exc:
            console.print(f"[yellow]device_info failed:[/yellow] {exc}")

        try:
            battery = sensor.get_battery_level()
        except Exception:
            battery = sensor.get_cached_battery_level()

        console.print(f"Streaming for [bold]{stream_seconds:.1f}[/bold]s…")
        sensor.start_streaming()
        started = True
        time.sleep(max(stream_seconds, 0.1))
    except Exception as exc:
        console.print(f"[red]Connect/stream failed:[/red] {exc}")
        return 1
    finally:
        wall_s = time.monotonic() - t0
        dropped = sensor.dropped_data_packets
        if started:
            try:
                sensor.stop_streaming()
            except Exception as exc:
                console.print(f"[yellow]stop_streaming failed:[/yellow] {exc}")
        try:
            hub.disconnect(target.mac_address)
        except Exception as exc:
            console.print(f"[yellow]disconnect failed:[/yellow] {exc}")

        print_stream_summary(
            device=target,
            adapter_id=connect_adapter,
            stream_seconds=stream_seconds,
            wall_s=wall_s,
            collector=collector,
            dropped_packets=dropped,
            battery=battery,
            model=model,
            csv_path=csv_path,
            csv_rows=csv_rows,
        )

    packets, _, _, _ = collector.snapshot()
    return 0 if packets > 0 else 1


def run_connect_session(
    hub: SensorHub,
    *,
    adapter_id: str,
    scan_timeout_ms: int,
    stream_seconds: float,
    mac: str | None = None,
) -> int:
    """Scan on adapter, connect to first (or matching) device, stream briefly, print stats."""
    return _run_stream_session(
        hub,
        adapter_id=adapter_id,
        scan_timeout_ms=scan_timeout_ms,
        stream_seconds=stream_seconds,
        mac=mac,
        data_callbacks=[],
        error_callbacks=[],
    )


def run_collect_session(
    hub: SensorHub,
    *,
    adapter_id: str,
    scan_timeout_ms: int,
    stream_seconds: float,
    output: Path | None,
    mac: str | None = None,
) -> int:
    """Like connect, but write every sample row to a CSV file."""
    picked = _scan_and_pick(
        hub,
        adapter_id=adapter_id,
        scan_timeout_ms=scan_timeout_ms,
        mac=mac,
    )
    if picked is None:
        return 1
    target, connect_adapter = picked

    path = output if output is not None else default_csv_path(mac=target.mac_address, adapter_id=adapter_id)
    csv_writer = CsvSampleWriter(path)
    console.print(f"Writing samples to [cyan]{path}[/cyan]")

    sensor = _open_ready_sensor(hub, target=target, connect_adapter=connect_adapter)
    if sensor is None:
        csv_writer.close()
        return 1

    collector = SampleCollector()

    def on_data(data: SensorData) -> None:
        collector.on_data(data)
        csv_writer.on_data(data)

    sensor.register_data_callback(on_data)
    sensor.register_error_callback(collector.on_error)

    model: str | None = None
    battery: int | None = None
    started = False
    t0 = time.monotonic()

    try:
        try:
            info = sensor.device_info()
            model = getattr(info, "model", None) or str(info)
        except Exception as exc:
            console.print(f"[yellow]device_info failed:[/yellow] {exc}")

        try:
            battery = sensor.get_battery_level()
        except Exception:
            battery = sensor.get_cached_battery_level()

        console.print(f"Collecting for [bold]{stream_seconds:.1f}[/bold]s…")
        sensor.start_streaming()
        started = True
        time.sleep(max(stream_seconds, 0.1))
    except Exception as exc:
        console.print(f"[red]Collect stream failed:[/red] {exc}")
        return 1
    finally:
        wall_s = time.monotonic() - t0
        dropped = sensor.dropped_data_packets
        if started:
            try:
                sensor.stop_streaming()
            except Exception as exc:
                console.print(f"[yellow]stop_streaming failed:[/yellow] {exc}")
        try:
            hub.disconnect(target.mac_address)
        except Exception as exc:
            console.print(f"[yellow]disconnect failed:[/yellow] {exc}")
        try:
            csv_writer.close()
        except Exception as exc:
            console.print(f"[yellow]csv close failed:[/yellow] {exc}")

        print_stream_summary(
            device=target,
            adapter_id=connect_adapter,
            stream_seconds=stream_seconds,
            wall_s=wall_s,
            collector=collector,
            dropped_packets=dropped,
            battery=battery,
            model=model,
            csv_path=path,
            csv_rows=csv_writer.rows,
        )

    packets, _, _, _ = collector.snapshot()
    return 0 if packets > 0 and csv_writer.rows > 0 else 1
