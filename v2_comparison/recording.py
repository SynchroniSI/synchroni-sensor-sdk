"""Shared recording helpers for legacy and v2 capture sessions."""

from __future__ import annotations

import time

from v2_comparison.config import CaptureConfig


def sleep_recording_window(config: CaptureConfig, *, post_stream_settle_s: float) -> None:
    """Wait for streaming to stabilize, then record for ``config.record_s`` seconds."""
    if post_stream_settle_s > 0:
        time.sleep(post_stream_settle_s)
    time.sleep(config.record_s)
