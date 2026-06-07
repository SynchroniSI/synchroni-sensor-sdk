"""CLI: scan, pick device, record samples, write CSV + manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from v2_comparison.config import CaptureConfig, default_capture_config
from v2_comparison.recorder_legacy import record_session as record_legacy
from v2_comparison.recorder_v2 import record_session as record_v2
from v2_comparison.schema import RecordingError


def _parse_enabled_ntf(value: str | None) -> tuple[str, ...]:
    if not value:
        return default_capture_config().enabled_ntf
    keys = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not keys:
        raise argparse.ArgumentTypeError("At least one NTF stream is required.")
    return keys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record a sensor capture for legacy vs v2 comparison.")
    parser.add_argument("--impl", choices=["legacy", "v2"], required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Capture directory (default: v2_comparison/captures/<impl>_<mac>_<utc>/)",
    )
    parser.add_argument("--scan-ms", type=int, default=None)
    parser.add_argument("--duration-s", type=int, default=None)
    parser.add_argument("--package-count", type=int, default=None)
    parser.add_argument("--power-refresh-ms", type=int, default=None)
    parser.add_argument("--enable-ntf", type=str, default=None, help="Comma-separated NTF keys, e.g. EMG,EEG")
    return parser


def build_config(args: argparse.Namespace) -> CaptureConfig:
    base = default_capture_config()
    return CaptureConfig(
        scan_ms=args.scan_ms if args.scan_ms is not None else base.scan_ms,
        record_s=args.duration_s if args.duration_s is not None else base.record_s,
        package_sample_count=args.package_count if args.package_count is not None else base.package_sample_count,
        power_refresh_interval_ms=args.power_refresh_ms
        if args.power_refresh_ms is not None
        else base.power_refresh_interval_ms,
        enabled_ntf=_parse_enabled_ntf(args.enable_ntf),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = build_config(args)

    try:
        if args.impl == "legacy":
            out = record_legacy(config, output_dir=args.output_dir)
        else:
            out = record_v2(config, output_dir=args.output_dir)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except RecordingError as exc:
        print(f"Recording failed: {exc}", file=sys.stderr)
        return 1

    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
