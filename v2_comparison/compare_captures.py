"""CLI: compare two capture directories with tiered heuristics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from v2_comparison.analyze import compare_captures, format_report
from v2_comparison.schema import load_capture_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare legacy and v2 sensor captures.")
    parser.add_argument("capture_a", type=Path, help="First capture directory")
    parser.add_argument("capture_b", type=Path, help="Second capture directory")
    parser.add_argument("--json", type=Path, default=None, help="Write machine-readable report to this path")
    parser.add_argument("--align", action="store_true", help="Enable Tier C sample-index alignment (informational)")
    parser.add_argument("--tolerance-pct", type=float, default=5.0, help="Sample count pass threshold (percent)")
    parser.add_argument("--label-a", default=None)
    parser.add_argument("--label-b", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        rows_a, manifest_a = load_capture_dir(args.capture_a)
        rows_b, manifest_b = load_capture_dir(args.capture_b)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    label_a = args.label_a or manifest_a.get("impl", args.capture_a.name)
    label_b = args.label_b or manifest_b.get("impl", args.capture_b.name)

    result = compare_captures(
        rows_a,
        manifest_a,
        rows_b,
        manifest_b,
        label_a=label_a,
        label_b=label_b,
        tolerance_pct=args.tolerance_pct,
        align=args.align,
    )

    report = format_report(result)
    print(report)

    if args.json is not None:
        args.json.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        print(f"\nWrote JSON report to {args.json}")

    has_fail = any(c.status == "FAIL" for c in result.checks)
    return 1 if has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
