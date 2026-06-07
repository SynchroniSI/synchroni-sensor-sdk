"""Pure comparison heuristics for legacy vs v2 captures."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from v2_comparison.normalize import data_type_name
from v2_comparison.schema import SampleRow

CheckStatus = Literal["PASS", "WARN", "FAIL", "INFO"]


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonResult:
    label_a: str
    label_b: str
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_a": self.label_a,
            "label_b": self.label_b,
            "checks": [asdict(c) for c in self.checks],
        }


def _group_by_type_channel(rows: list[SampleRow]) -> dict[tuple[int, int], list[SampleRow]]:
    groups: dict[tuple[int, int], list[SampleRow]] = defaultdict(list)
    for row in rows:
        groups[(row.data_type, row.channel_index)].append(row)
    for key in groups:
        groups[key].sort(key=lambda r: r.sample_index)
    return groups


def _capture_duration_s(manifest: dict[str, Any]) -> float:
    duration = manifest.get("duration_s")
    if isinstance(duration, (int, float)) and duration > 0:
        return float(duration)
    return 0.0


def _channel_index_rate_hz(rows: list[SampleRow], duration_s: float) -> float | None:
    """Estimate sample rate from device ``sample_index`` span over the capture window."""
    valid = [r for r in rows if not r.is_lost]
    if len(valid) < 2 or duration_s <= 0:
        return None
    indices = [r.sample_index for r in valid]
    span = max(indices) - min(indices) + 1
    if span <= 0:
        return None
    return span / duration_s


def _callback_batch_rate_hz(rows: list[SampleRow]) -> float | None:
    """Host callback spacing; not device sample rate when batches share ``recv_mono_ns``."""
    if len(rows) < 2:
        return None
    deltas = [
        (rows[i].recv_mono_ns - rows[i - 1].recv_mono_ns) / 1e9
        for i in range(1, len(rows))
        if rows[i].recv_mono_ns > rows[i - 1].recv_mono_ns
    ]
    if not deltas:
        return None
    median_delta = statistics.median(deltas)
    if median_delta <= 0:
        return None
    return 1.0 / median_delta


def _declared_sample_rate(rows: list[SampleRow]) -> int | None:
    for row in rows:
        if row.sample_rate > 0:
            return row.sample_rate
    return None


def _index_gaps(rows: list[SampleRow]) -> tuple[bool, int]:
    if not rows:
        return True, 0
    monotonic = True
    max_gap = 0
    prev = rows[0].sample_index
    for row in rows[1:]:
        if row.sample_index < prev:
            monotonic = False
        gap = row.sample_index - prev
        if gap > 1:
            max_gap = max(max_gap, gap - 1)
        prev = row.sample_index
    return monotonic, max_gap


def _package_counter_monotonic(rows: list[SampleRow]) -> bool:
    counters = [r.package_counter for r in rows]
    return all(counters[i] <= counters[i + 1] for i in range(len(counters) - 1))


def _channel_stats(rows: list[SampleRow]) -> dict[str, float]:
    values = [float(r.data) for r in rows if not r.is_lost]
    if not values:
        return {"count": 0.0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "flatline_rate": 0.0}
    flatlines = sum(1 for i in range(1, len(values)) if values[i] == values[i - 1])
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "count": float(len(values)),
        "mean": statistics.mean(values),
        "std": std,
        "min": min(values),
        "max": max(values),
        "flatline_rate": flatlines / max(len(values) - 1, 1),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _pct_diff(a: float, b: float) -> float:
    if a == 0 and b == 0:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b)) * 100.0


def compare_captures(
    rows_a: list[SampleRow],
    manifest_a: dict[str, Any],
    rows_b: list[SampleRow],
    manifest_b: dict[str, Any],
    *,
    label_a: str = "A",
    label_b: str = "B",
    tolerance_pct: float = 5.0,
    align: bool = False,
) -> ComparisonResult:
    result = ComparisonResult(label_a=label_a, label_b=label_b)
    checks = result.checks

    cfg_a = manifest_a.get("config", {})
    cfg_b = manifest_b.get("config", {})
    if cfg_a == cfg_b:
        checks.append(CheckResult("config_parity", "PASS", "Capture configs match."))
    else:
        checks.append(
            CheckResult(
                "config_parity",
                "WARN",
                "Capture configs differ.",
                {"a": cfg_a, "b": cfg_b},
            )
        )

    dev_a = manifest_a.get("device", {})
    dev_b = manifest_b.get("device", {})
    if dev_a.get("model") == dev_b.get("model") and dev_a.get("firmware_version") == dev_b.get("firmware_version"):
        checks.append(CheckResult("device_info", "PASS", "Device model and firmware match between captures."))
    else:
        checks.append(
            CheckResult(
                "device_info",
                "WARN",
                "Device model or firmware differs (expected if not the same device).",
                {"a": dev_a, "b": dev_b},
            )
        )

    stats_a = manifest_a.get("stats", {})
    stats_b = manifest_b.get("stats", {})
    samples_a = int(stats_a.get("samples", len(rows_a)))
    samples_b = int(stats_b.get("samples", len(rows_b)))
    packets_a = int(stats_a.get("packets", 0))
    packets_b = int(stats_b.get("packets", 0))

    duration_a = _capture_duration_s(manifest_a)
    duration_b = _capture_duration_s(manifest_b)
    compare_duration = min(duration_a, duration_b) if duration_a and duration_b else max(duration_a, duration_b)

    if samples_a == 0 or samples_b == 0:
        checks.append(CheckResult("sample_count", "FAIL", "One or both captures contain no samples."))
    else:
        sample_diff = _pct_diff(float(samples_a), float(samples_b))
        checks.append(
            CheckResult(
                "sample_count",
                "PASS" if sample_diff <= tolerance_pct else "WARN",
                f"Total samples {label_a}={samples_a}, {label_b}={samples_b} ({sample_diff:.1f}% diff).",
                {"samples_a": samples_a, "samples_b": samples_b, "diff_pct": sample_diff},
            )
        )

    if packets_a > 0 and packets_b > 0:
        packet_diff = _pct_diff(float(packets_a), float(packets_b))
        checks.append(
            CheckResult(
                "packet_count",
                "PASS" if packet_diff <= tolerance_pct else "WARN",
                f"Total packets {label_a}={packets_a}, {label_b}={packets_b} ({packet_diff:.1f}% diff).",
                {"packets_a": packets_a, "packets_b": packets_b, "diff_pct": packet_diff},
            )
        )
        spp_a = samples_a / packets_a
        spp_b = samples_b / packets_b
        spp_diff = _pct_diff(spp_a, spp_b)
        checks.append(
            CheckResult(
                "samples_per_packet",
                "PASS" if spp_diff <= 1.0 else "WARN",
                f"Samples/packet {label_a}={spp_a:.1f}, {label_b}={spp_b:.1f}.",
                {"samples_per_packet_a": spp_a, "samples_per_packet_b": spp_b, "diff_pct": spp_diff},
            )
        )

    types_a = set(manifest_a.get("stats", {}).get("samples_by_data_type", {}))
    types_b = set(manifest_b.get("stats", {}).get("samples_by_data_type", {}))
    if types_a == types_b and types_a:
        checks.append(CheckResult("data_types", "PASS", f"Data types present: {sorted(types_a)}"))
    else:
        checks.append(
            CheckResult(
                "data_types",
                "WARN" if types_a & types_b else "FAIL",
                "Data type sets differ between captures.",
                {"a": sorted(types_a), "b": sorted(types_b)},
            )
        )

    lost_a = sum(1 for r in rows_a if r.is_lost)
    lost_b = sum(1 for r in rows_b if r.is_lost)
    if lost_a == lost_b == 0:
        checks.append(CheckResult("is_lost", "PASS", "No lost samples flagged in either capture."))
    else:
        checks.append(
            CheckResult(
                "is_lost",
                "WARN" if lost_a == lost_b else "FAIL",
                f"Lost samples {label_a}={lost_a}, {label_b}={lost_b}.",
                {"lost_a": lost_a, "lost_b": lost_b},
            )
        )

    dropped_a = int(stats_a.get("dropped_packets", 0))
    dropped_b = int(stats_b.get("dropped_packets", 0))
    checks.append(
        CheckResult(
            "dropped_packets",
            "WARN" if dropped_a or dropped_b else "INFO",
            f"Dropped packets (driver buffer): {label_a}={dropped_a}, {label_b}={dropped_b}.",
            {"dropped_a": dropped_a, "dropped_b": dropped_b},
        )
    )

    recv_a = float(stats_a.get("recv_window_s", 0))
    recv_b = float(stats_b.get("recv_window_s", 0))
    if recv_a > 0 and recv_b > 0:
        recv_diff = _pct_diff(recv_a, recv_b)
        checks.append(
            CheckResult(
                "recv_window",
                "PASS" if recv_diff <= tolerance_pct else "WARN",
                f"Host receive window {label_a}={recv_a:.2f}s, {label_b}={recv_b:.2f}s ({recv_diff:.1f}% diff).",
                {"recv_window_a": recv_a, "recv_window_b": recv_b, "diff_pct": recv_diff},
            )
        )

    groups_a = _group_by_type_channel(rows_a)
    groups_b = _group_by_type_channel(rows_b)
    all_keys = sorted(set(groups_a) | set(groups_b))

    for key in all_keys:
        dt_name = data_type_name(key[0])
        ch = key[1]
        ga = groups_a.get(key, [])
        gb = groups_b.get(key, [])

        declared_a = _declared_sample_rate(ga)
        declared_b = _declared_sample_rate(gb)
        if declared_a and declared_b:
            checks.append(
                CheckResult(
                    f"declared_rate_{dt_name}_ch{ch}",
                    "PASS" if declared_a == declared_b else "WARN",
                    f"{dt_name} ch{ch}: declared sample rate {label_a}={declared_a} Hz, {label_b}={declared_b} Hz.",
                    {"rate_a": declared_a, "rate_b": declared_b},
                )
            )

        mono_a, gap_a = _index_gaps(ga)
        mono_b, gap_b = _index_gaps(gb)
        status_idx: CheckStatus = "PASS"
        if not mono_a or not mono_b or gap_a > 0 or gap_b > 0:
            status_idx = "WARN"
        checks.append(
            CheckResult(
                f"sample_index_{dt_name}_ch{ch}",
                status_idx,
                f"{dt_name} ch{ch}: monotonic A={mono_a} B={mono_b}, max gap A={gap_a} B={gap_b}.",
                {"monotonic_a": mono_a, "monotonic_b": mono_b, "max_gap_a": gap_a, "max_gap_b": gap_b},
            )
        )

        pc_a = _package_counter_monotonic(ga) if ga else True
        pc_b = _package_counter_monotonic(gb) if gb else True
        checks.append(
            CheckResult(
                f"package_counter_{dt_name}_ch{ch}",
                "PASS" if pc_a and pc_b else "WARN",
                f"{dt_name} ch{ch}: package_counter monotonic A={pc_a} B={pc_b}.",
            )
        )

        if compare_duration > 0:
            rate_a = _channel_index_rate_hz(ga, compare_duration)
            rate_b = _channel_index_rate_hz(gb, compare_duration)
            if rate_a is not None and rate_b is not None:
                rate_diff = _pct_diff(rate_a, rate_b)
                checks.append(
                    CheckResult(
                        f"sample_rate_{dt_name}_ch{ch}",
                        "PASS" if rate_diff <= tolerance_pct else "WARN",
                        f"{dt_name} ch{ch}: index span ~{rate_a:.1f} Hz vs ~{rate_b:.1f} Hz over {compare_duration:.0f}s.",
                        {"rate_a": rate_a, "rate_b": rate_b, "diff_pct": rate_diff},
                    )
                )
                if declared_a:
                    declared_diff = _pct_diff(float(declared_a), rate_a)
                    checks.append(
                        CheckResult(
                            f"declared_vs_index_{dt_name}_ch{ch}",
                            "PASS" if declared_diff <= 10.0 else "WARN",
                            f"{dt_name} ch{ch}: declared {declared_a} Hz vs index-estimated {rate_a:.1f} Hz ({label_a}).",
                            {"declared": declared_a, "estimated": rate_a, "diff_pct": declared_diff},
                        )
                    )

        cb_a = _callback_batch_rate_hz(ga)
        cb_b = _callback_batch_rate_hz(gb)
        if cb_a is not None and cb_b is not None:
            checks.append(
                CheckResult(
                    f"callback_arrival_{dt_name}_ch{ch}",
                    "INFO",
                    f"{dt_name} ch{ch}: host callback spacing ~{cb_a:.1f} Hz vs ~{cb_b:.1f} Hz "
                    "(informational; batched samples share recv_mono_ns).",
                    {"callback_rate_a": cb_a, "callback_rate_b": cb_b},
                )
            )

        sa = _channel_stats(ga)
        sb = _channel_stats(gb)
        if sa["count"] > 0 and sb["count"] > 0:
            mean_diff_pct = _pct_diff(sa["mean"], sb["mean"])
            std_diff_pct = _pct_diff(sa["std"], sb["std"]) if sa["std"] or sb["std"] else 0.0
            stats_status: CheckStatus = "PASS"
            if mean_diff_pct > 15.0 or std_diff_pct > 20.0:
                stats_status = "WARN"
            checks.append(
                CheckResult(
                    f"stats_{dt_name}_ch{ch}",
                    stats_status,
                    f"{dt_name} ch{ch}: mean {label_a}={sa['mean']:.2f} {label_b}={sb['mean']:.2f}, "
                    f"std {label_a}={sa['std']:.2f} {label_b}={sb['std']:.2f}.",
                    {"a": sa, "b": sb, "mean_diff_pct": mean_diff_pct, "std_diff_pct": std_diff_pct},
                )
            )

        if align and ga and gb:
            map_a = {r.sample_index: r.data for r in ga if not r.is_lost}
            map_b = {r.sample_index: r.data for r in gb if not r.is_lost}
            overlap = sorted(set(map_a) & set(map_b))
            if len(overlap) < 2:
                checks.append(
                    CheckResult(
                        f"align_{dt_name}_ch{ch}",
                        "INFO",
                        f"{dt_name} ch{ch}: insufficient overlapping sample indices for alignment.",
                        {"overlap": len(overlap)},
                    )
                )
            else:
                xs = [float(map_a[i]) for i in overlap]
                ys = [float(map_b[i]) for i in overlap]
                diffs = [abs(x - y) for x, y in zip(xs, ys, strict=True)]
                rmse = math.sqrt(sum(d * d for d in diffs) / len(diffs))
                r = _pearson(xs, ys)
                checks.append(
                    CheckResult(
                        f"align_{dt_name}_ch{ch}",
                        "INFO",
                        f"{dt_name} ch{ch}: overlap={len(overlap)}, max|diff|={max(diffs):.2f}, RMSE={rmse:.2f}, r={r}.",
                        {
                            "overlap": len(overlap),
                            "max_abs_diff": max(diffs),
                            "rmse": rmse,
                            "pearson_r": r,
                        },
                    )
                )

    return result


def format_report(result: ComparisonResult) -> str:
    lines = [f"Comparison: {result.label_a} vs {result.label_b}", ""]
    for check in result.checks:
        lines.append(f"[{check.status}] {check.name}: {check.message}")
    fails = sum(1 for c in result.checks if c.status == "FAIL")
    warns = sum(1 for c in result.checks if c.status == "WARN")
    lines.append("")
    lines.append(f"Summary: {fails} FAIL, {warns} WARN, {len(result.checks)} checks total.")
    if fails:
        lines.append(
            "Interpretation: structural mismatch — verify same device, config, stream enabled, and comparable record window."
        )
    elif warns:
        lines.append(
            "Interpretation: largely aligned; review any WARN on sample/packet counts, index gaps, or amplitude stats."
        )
    else:
        lines.append("Interpretation: captures look structurally consistent.")
    lines.append(
        "Note: callback_arrival_* is host scheduling only; use sample_rate_* (from sample_index) for device throughput."
    )
    return "\n".join(lines)
