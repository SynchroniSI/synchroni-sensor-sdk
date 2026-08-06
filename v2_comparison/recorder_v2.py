"""v2 sync API capture session."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path

from synchroni_sensor_sdk import SensorHub
from synchroni_sensor_sdk.core.data import SensorData
from v2_comparison.config import POST_STREAM_SETTLE_S, CaptureConfig
from v2_comparison.device_picker import PickableDevice, filter_devices, prompt_device_selection
from v2_comparison.normalize import normalize_v2
from v2_comparison.params import apply_v2_params
from v2_comparison.recording import sleep_recording_window
from v2_comparison.schema import (
    MANIFEST_JSON_NAME,
    SAMPLE_CSV_NAME,
    DeviceManifest,
    Manifest,
    RecordingError,
    SampleCollector,
    default_output_dir,
    write_manifest,
    write_samples_csv,
)


def scan_devices(hub: SensorHub, config: CaptureConfig) -> list[PickableDevice]:
    results = hub.scan(config.scan_ms)
    return filter_devices(
        results,
        name_getter=lambda d: d.name,
        address_getter=lambda d: d.mac_address,
        rssi_getter=lambda d: d.rssi,
    )


def record_session(
    config: CaptureConfig,
    *,
    output_dir: Path | None = None,
    device: PickableDevice | None = None,
) -> Path:
    started_at = datetime.now(UTC)
    collector = SampleCollector()

    with SensorHub() as hub:
        if device is None:
            devices = scan_devices(hub, config)
            device = prompt_device_selection(devices)

        print(f"Connecting to {device.name} ({device.address})...")
        sensor = hub.connect(device.address)

        try:
            apply_v2_params(sensor, config)
            sensor.init(config.package_sample_count, config.power_refresh_interval_ms)

            def on_data(data: SensorData) -> None:
                collector.add_packet(normalize_v2(data))

            sensor.register_data_callback(on_data)
            print(f"Streaming for {config.record_s}s...")
            sensor.start_streaming()
            sleep_recording_window(config, post_stream_settle_s=POST_STREAM_SETTLE_S)
            sensor.stop_streaming()

            info = sensor.device_info()
            dropped = sensor.dropped_data_packets
        except Exception as exc:
            raise RecordingError(f"v2 capture failed: {exc}") from exc
        finally:
            with contextlib.suppress(Exception):
                sensor.disconnect()

    ended_at = datetime.now(UTC)
    rows, _packets = collector.snapshot()
    if not rows:
        raise RecordingError("No samples received during capture.")

    out_dir = output_dir or default_output_dir("v2", device.address)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_samples_csv(out_dir / SAMPLE_CSV_NAME, rows)
    stats = collector.build_stats(dropped_packets=dropped)
    manifest = Manifest(
        impl="v2",
        started_at_utc=started_at.isoformat(),
        ended_at_utc=ended_at.isoformat(),
        duration_s=config.record_s,
        device=DeviceManifest(
            mac=device.address,
            name=device.name,
            model=info.model,
            hardware_version=info.hardware_version,
            firmware_version=info.firmware_version,
        ),
        config=config.to_dict(),
        stats=stats,
    )
    write_manifest(out_dir / MANIFEST_JSON_NAME, manifest)
    print(f"Wrote {len(rows)} samples ({stats.packets} packets) to {out_dir}")
    return out_dir
