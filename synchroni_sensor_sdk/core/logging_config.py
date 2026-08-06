"""Stdlib logging helpers for the v2 SDK."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(
    *,
    enabled: bool = True,
    path: str | Path | None = None,
    level: int = logging.DEBUG,
    logger_name: str = "synchroni_sensor_sdk",
) -> None:
    """Configure the ``synchroni_sensor_sdk`` logger.

    Parameters
    ----------
    enabled:
        When False, clears handlers on the SDK logger.
    path:
        Optional log file path. When set, a FileHandler is added (in addition to
        a StreamHandler if no handlers exist).
    level:
        Logging level for the SDK logger.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level if enabled else logging.WARNING)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    if not enabled:
        logger.addHandler(logging.NullHandler())
        return

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if path is not None:
        file_handler = logging.FileHandler(str(path), encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
