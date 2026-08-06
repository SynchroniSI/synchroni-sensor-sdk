"""Automatically fetch Bumble Realtek (RTK) HCI firmware for managed USB dongles.

Bumble's RTK driver needs host-side firmware such as ``rtl8761bu_fw.bin`` (TP-Link
UB500). This module downloads **only** the image set for the USB device in use
(from VID/PID), not the full Bumble RTK catalog.

Mirror sources (in order): GitLab linux-firmware mirror, Linux kernel firmware
git, Linux from Scratch mirror, Realtek Android open-source tree. Retries 5xx
failures.

Disable with env ``SYNCHRONI_RTK_FIRMWARE_AUTO=0``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

RTK_FIRMWARE_AUTO_ENV = "SYNCHRONI_RTK_FIRMWARE_AUTO"
_USER_AGENT = "synchroni-sensor-sdk"
_DOWNLOAD_TIMEOUT_S = 60
_HTTP_RETRIES = 3
_HTTP_RETRY_DELAY_S = 1.5

# USB (VID, PID) → RTK firmware base name (suffixes ``_fw.bin`` / ``_config.bin``).
# Aligned with Bumble's RTK product list for chips we care about in multi-adapter use.
_VID_PID_FW_BASES: dict[tuple[str, str], tuple[str, ...]] = {
    # Realtek 8761BU (incl. TP-Link UB500)
    ("0bda", "8771"): ("rtl8761bu",),
    ("0bda", "877b"): ("rtl8761bu",),
    ("0bda", "a728"): ("rtl8761bu",),
    ("0bda", "a729"): ("rtl8761bu",),
    ("0b05", "190e"): ("rtl8761bu",),
    ("2357", "0604"): ("rtl8761bu",),  # TP-Link UB500
    ("2550", "8761"): ("rtl8761bu",),
    ("2b89", "8761"): ("rtl8761bu",),
    ("2c0a", "8761"): ("rtl8761bu",),
    ("7392", "c611"): ("rtl8761bu",),
    ("2230", "0016"): ("rtl8761bu",),
    # Realtek 8761CU
    ("0b05", "1bf6"): ("rtl8761cu",),
    ("0bda", "c761"): ("rtl8761cu",),
    ("7392", "f611"): ("rtl8761cu",),
}

_USB_VID_PID_RE = re.compile(
    r"(?i)(?:usb:)?([0-9a-f]{4}):([0-9a-f]{4})(?:[:/#]|$)",
)

_ensure_lock = threading.Lock()
_fetched_targets: set[str] = set()

__all__ = [
    "RTK_FIRMWARE_AUTO_ENV",
    "clear_rtk_firmware_cache",
    "ensure_rtk_firmware_available",
    "parse_usb_vid_pid",
    "rtk_firmware_auto_enabled",
    "rtk_firmware_cache_dir",
]


@dataclass(frozen=True)
class _Source:
    base_url: str
    strip_bin_suffix: bool


_SOURCES: tuple[_Source, ...] = (
    # Prefer mirrors that validate with common Python CA bundles (Scoop/embeddable
    # OpenSSL on Windows often rejects git.kernel.org's chain as "certificate expired").
    _Source(
        "https://gitlab.com/kernel-firmware/linux-firmware/-/raw/main/rtl_bt",
        False,
    ),
    _Source(
        "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt",
        False,
    ),
    _Source(
        "https://anduin.linuxfromscratch.org/sources/linux-firmware/rtl_bt",
        False,
    ),
    _Source(
        "https://raw.githubusercontent.com/Realtek-OpenSource/android_hardware_realtek/rtk1395/bt/rtkbt/Firmware/BT",
        True,
    ),
)


def rtk_firmware_auto_enabled() -> bool:
    """Return whether network auto-fetch of RTK firmware is allowed."""
    raw = os.environ.get(RTK_FIRMWARE_AUTO_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def rtk_firmware_cache_dir() -> Path | None:
    """Return the directory where auto-fetched Realtek host firmware is stored.

    Prefer ``BUMBLE_RTK_FIRMWARE_DIR`` / Bumble's default path. Returns ``None``
    when Bumble is not installed and no explicit env dir is set.
    """
    env_dir = os.environ.get("BUMBLE_RTK_FIRMWARE_DIR")
    if env_dir:
        return Path(env_dir)
    try:
        from bumble.drivers import rtk
    except ImportError:
        return None
    return _rtk_download_dir(rtk)


def clear_rtk_firmware_cache() -> Path | None:
    """Delete the Realtek host-firmware cache directory, if present.

    Also clears the in-process “already fetched” memo used by
    :func:`ensure_rtk_firmware_available`.

    Returns
    -------
    Path | None
        Directory that was cleared (may no longer exist), or ``None`` when no
        cache location could be resolved.
    """
    _fetched_targets.clear()
    cache_dir = rtk_firmware_cache_dir()
    if cache_dir is None:
        logger.debug("No RTK firmware cache directory to clear")
        return None
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        logger.info("Cleared Realtek firmware cache at %s", cache_dir)
    return cache_dir


def parse_usb_vid_pid(transport_name: str | None) -> tuple[str, str] | None:
    """Extract lowercase VID/PID from a Bumble transport string, if present."""
    if not transport_name:
        return None
    match = _USB_VID_PID_RE.search(transport_name)
    if match is None:
        return None
    return match.group(1).lower(), match.group(2).lower()


def ensure_rtk_firmware_available(
    *,
    transport_name: str | None = None,
    force: bool = False,
) -> Path | None:
    """Ensure RTK firmware for *this* USB dongle is available for Bumble.

    Parameters
    ----------
    transport_name:
        Bumble ``open_transport`` string (e.g. ``usb:2357:0604/SERIAL``). Used
        to pick which ``rtl*_fw.bin`` image to download.
    force:
        Re-download even when auto-fetch is disabled or cached.

    Returns
    -------
    Path | None
        Firmware cache directory, or ``None`` when skipped (no matching VID/PID,
        Bumble missing, or auto-fetch disabled).
    """
    if not rtk_firmware_auto_enabled() and not force:
        logger.debug("RTK firmware auto-fetch disabled via %s", RTK_FIRMWARE_AUTO_ENV)
        return None

    try:
        from bumble.drivers import rtk
    except ImportError:
        logger.debug("Bumble not installed; skipping RTK firmware ensure")
        return None

    targets = _targets_for_transport(transport_name)
    if not targets:
        logger.debug(
            "No RTK firmware mapping for transport %r; skipping auto-fetch",
            transport_name,
        )
        return _rtk_download_dir(rtk)

    dest_dir = _rtk_download_dir(rtk)
    cache_key = f"{transport_name or ''}::{','.join(t.filename for t in targets)}"

    with _ensure_lock:
        if not force and cache_key in _fetched_targets:
            return dest_dir

        missing = [target for target in targets if force or not _firmware_resolvable(rtk, target.filename)]
        if not missing:
            logger.debug("RTK firmware already cached for %s", transport_name)
            _fetched_targets.add(cache_key)
            return dest_dir

        dest_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for target in missing:
            try:
                _download_rtk_file(dest_dir, target.filename, required=target.required)
            except Exception as exc:
                if target.required:
                    errors.append(str(exc))
                else:
                    logger.warning("Optional RTK file %s not downloaded: %s", target.filename, exc)

        if errors:
            # Still record partial success so we do not thrash on missing-unrelated files.
            failed = "; ".join(errors)
            raise RuntimeError(
                f"Failed to prepare Realtek firmware for {transport_name}: {failed}. "
                f"See https://google.github.io/bumble/drivers/realtek.html"
            )

        _fetched_targets.add(cache_key)
        return dest_dir


@dataclass(frozen=True)
class _Target:
    filename: str
    required: bool


def _targets_for_transport(transport_name: str | None) -> list[_Target]:
    vid_pid = parse_usb_vid_pid(transport_name)
    if vid_pid is None:
        return []
    bases = _VID_PID_FW_BASES.get(vid_pid)
    if not bases:
        return []
    targets: list[_Target] = []
    for base in bases:
        targets.append(_Target(f"{base}_fw.bin", required=True))
        targets.append(_Target(f"{base}_config.bin", required=False))
    return targets


def _rtk_download_dir(rtk: object) -> Path:
    """Directory Bumble will search: ``BUMBLE_RTK_FIRMWARE_DIR`` or default project data."""
    env_name = getattr(rtk, "RTK_FIRMWARE_DIR_ENV", "BUMBLE_RTK_FIRMWARE_DIR")
    env_dir = os.environ.get(str(env_name))
    if env_dir:
        return Path(env_dir)
    dest = getattr(rtk, "rtk_firmware_dir", None)
    if callable(dest):
        return Path(dest())
    return Path.cwd()


def _firmware_resolvable(rtk: object, file_name: str) -> bool:
    driver = getattr(rtk, "Driver", None)
    find = getattr(driver, "find_binary_path", None) if driver is not None else None
    if callable(find):
        try:
            return find(file_name) is not None
        except Exception:
            return False
    return False


def _download_rtk_file(dest_dir: Path, name: str, *, required: bool) -> None:
    dest = dest_dir / name
    try:
        data = _http_get_with_mirrors(name)
    except Exception as exc:
        if required:
            raise RuntimeError(
                f"Failed to download required Realtek firmware {name!r}: {exc}. "
                f"Install with: bumble-rtk-fw-download --single {name.removesuffix('_fw.bin')}"
            ) from exc
        logger.warning("Optional Realtek file %s unavailable: %s", name, exc)
        return

    tmp = dest.with_name(f"{dest.name}.partial")
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()
    logger.info("Cached Realtek firmware %s (%s bytes)", dest, len(data))


def _http_get_with_mirrors(name: str) -> bytes:
    errors: list[str] = []
    for source in _SOURCES:
        url_name = name.replace(".bin", "") if source.strip_bin_suffix else name
        url = f"{source.base_url}/{url_name}"
        try:
            return _http_get_url(url)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            logger.debug("RTK firmware fetch failed for %s: %s", url, exc)
            continue
    raise RuntimeError("; ".join(errors) if errors else f"no sources for {name}")


def _http_get_url(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, _HTTP_RETRIES + 1):
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S) as response:
                data = cast(bytes, response.read())
                if not data:
                    raise RuntimeError("empty response")
                return data
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(f"HTTP {exc.code}")
            # Retry rate limits / temporary outages
            if exc.code in {408, 425, 429, 500, 502, 503, 504} and attempt < _HTTP_RETRIES:
                time.sleep(_HTTP_RETRY_DELAY_S * attempt)
                continue
            raise last_error from exc
        except Exception as exc:
            last_error = exc
            if attempt < _HTTP_RETRIES:
                time.sleep(_HTTP_RETRY_DELAY_S * attempt)
                continue
            raise
    assert last_error is not None
    raise last_error
