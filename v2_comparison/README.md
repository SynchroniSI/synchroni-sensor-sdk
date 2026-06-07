# v2 Comparison Tooling

Record sensor data from the **legacy** (`sensor`) and **v2** (`synchroni_sensor_sdk`) APIs under identical configuration, then compare captures with automated heuristics.

## Prerequisites

- Poetry environment with project dependencies installed
- Bluetooth enabled
- A supported Synchroni device in range

Only one BLE connection is possible at a time. Run legacy and v2 captures **sequentially** on the same device for meaningful comparison.

## Default capture profile

| Setting | Value |
|---------|-------|
| Scan | 5 seconds |
| Record | 30 seconds (after streaming starts) |
| Package sample count | 10 |
| Power refresh | 5000 ms |
| Streams | EEG only (matches `examples/console.py`; use `--enable-ntf` to override) |
| Filters | All OFF |

Device filter: RSSI > -80 and name starts with `OB`, `Sync`, or `Orion`.

## Record a capture

```bash
poetry run python -m v2_comparison.record_capture --impl legacy
poetry run python -m v2_comparison.record_capture --impl v2
```

Optional flags:

- `--output-dir PATH` — output directory (default: `v2_comparison/captures/<impl>_<mac>_<utc>/`)
- `--scan-ms 5000`
- `--duration-s 30`
- `--package-count 10`
- `--power-refresh-ms 5000`
- `--enable-ntf EMG,EEG` — override enabled notification streams

Each run writes:

- `samples.csv` — one row per sample (canonical schema)
- `manifest.json` — device info, config, and summary stats

## Compare two captures

```bash
poetry run python -m v2_comparison.compare_captures \
  v2_comparison/captures/legacy_AABBCCDDEEFF_... \
  v2_comparison/captures/v2_AABBCCDDEEFF_...
```

Options:

- `--json report.json` — machine-readable results
- `--align` — Tier C sample-index alignment (informational; separate sessions are not ground truth)
- `--tolerance-pct 5` — sample-count pass threshold

## Interpreting the report

- **PASS** — structural checks within tolerance
- **WARN** — review sample/packet counts, index gaps, declared vs estimated rate, config, or amplitude stats
- **FAIL** — missing data or major structural mismatch
- **INFO** — host callback spacing (`callback_arrival_*`), alignment metrics, or v2 `dropped_packets`

### Key checks

| Check | Meaning |
|-------|---------|
| `sample_count` / `packet_count` | Total rows and BLE notification batches |
| `samples_per_packet` | Should match channels × package sample count (e.g. 40 for 4×10 EEG) |
| `sample_rate_*` | Device rate from `sample_index` span ÷ capture duration |
| `declared_vs_index_*` | Manifest `sample_rate` vs index-based estimate |
| `callback_arrival_*` | Host callback spacing only — **not** device sample rate (batched rows share `recv_mono_ns`) |
| `recv_window` | Wall time from first to last received row |
| `sample_index_*` | Monotonic indices and gap detection |
| `stats_*` | Per-channel mean/std comparison |

Both recorders wait `POST_STREAM_SETTLE_S` (0.2s) after streaming starts before the configured record window, so packet counts should align more closely than ad-hoc timing.

If v2 shows dropped packets, the data callback may be too slow — the recorder enqueues rows in O(1) time to minimize this.

## Tests

```bash
poetry run pytest v2_comparison/tests -q
```

Hardware is not required for unit tests.
