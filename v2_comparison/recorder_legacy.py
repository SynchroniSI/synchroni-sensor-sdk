"""Legacy sensor package capture session."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sensor.sensor_controller import SensorControllerInstance
from sensor.sensor_device import BLEDevice, DeviceStateEx
from v2_comparison.config import CaptureConfig
from v2_comparison.device_picker import PickableDevice, filter_devices, prompt_device_selection
from v2_comparison.normalize import normalize_legacy
from v2_comparison.params import apply_legacy_params
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


def scan_devices(config: CaptureConfig) -> list[PickableDevice]:
    if not SensorControllerInstance.isEnable:
        raise RecordingError("Bluetooth is not enabled (SensorControllerInstance.isEnable is False).")
    devices = SensorControllerInstance.scan(config.scan_ms)
    return filter_devices(
        devices,
        name_getter=lambda d: d.Name,
        address_getter=lambda d: d.Address,
        rssi_getter=lambda d: d.RSSI,
    )


def record_session(
    config: CaptureConfig,
    *,
    output_dir: Path | None = None,
    device: PickableDevice | None = None,
) -> Path:
    started_at = datetime.now(UTC)
    collector = SampleCollector()

    if device is None:
        devices = scan_devices(config)
        device = prompt_device_selection(devices)

    sensor = SensorControllerInstance.requireSensor(cast(BLEDevice, device.payload))
    if sensor is None:
        raise RecordingError(f"Could not create sensor profile for {device.address}")

    def on_data(_profile: Any, data: Any) -> None:
        collector.add_packet(normalize_legacy(data))

    # Must be set before connect(): legacy process_data captures the callback when
    # the data thread starts during connect (see examples/console.py).
    sensor.onDataCallback = on_data

    try:
        print(f"Connecting to {device.name} ({device.address})...")
        if sensor.deviceState != DeviceStateEx.Ready and not sensor.connect():
            raise RecordingError("Legacy connect() returned False")

        apply_legacy_params(sensor, config)
        if not sensor.init(config.package_sample_count, config.power_refresh_interval_ms):
            raise RecordingError("Legacy init() returned False")

        print(f"Streaming for {config.record_s}s...")
        if not sensor.startDataNotification():
            raise RecordingError("Legacy startDataNotification() returned False")
        # startDataNotification already sleeps POST_STREAM_SETTLE_S internally.
        sleep_recording_window(config, post_stream_settle_s=0)
        sensor.stopDataNotification()

        device_info = sensor.getDeviceInfo()
        if device_info is None:
            raise RecordingError("getDeviceInfo() returned None")
    except RecordingError:
        raise
    except Exception as exc:
        raise RecordingError(f"Legacy capture failed: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            sensor.disconnect()
        SensorControllerInstance.terminate()

    ended_at = datetime.now(UTC)
    rows, _packets = collector.snapshot()
    if not rows:
        raise RecordingError("No samples received during capture.")

    out_dir = output_dir or default_output_dir("legacy", device.address)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_samples_csv(out_dir / SAMPLE_CSV_NAME, rows)
    stats = collector.build_stats(dropped_packets=0)
    manifest = Manifest(
        impl="legacy",
        started_at_utc=started_at.isoformat(),
        ended_at_utc=ended_at.isoformat(),
        duration_s=config.record_s,
        device=DeviceManifest(
            mac=device.address,
            name=device_info.DeviceName or device.name,
            model=device_info.ModelName,
            hardware_version=device_info.HardwareVersion,
            firmware_version=device_info.FirmwareVersion,
        ),
        config=config.to_dict(),
        stats=stats,
    )
    write_manifest(out_dir / MANIFEST_JSON_NAME, manifest)
    print(f"Wrote {len(rows)} samples ({stats.packets} packets) to {out_dir}")
    return out_dir
