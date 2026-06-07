"""Locate, download, and cache the Windows WinUSB claim helper.

Resolution order:
1. Explicit path override
2. ``SYNCHRONI_WINUSB_INSTALLER`` env var
3. Packaged resources (``resources/windows-driver/…``)
4. On-disk cache under the user cache directory
5. Download via the public SDK assets ``manifest.json`` (hash-verified)

``clear_winusb_installer_cache`` removes the cached helper so the next claim
re-fetches from the manifest.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

from synchroni_sensor_sdk.core.exceptions import WindowsClaimUnavailableError

logger = logging.getLogger(__name__)

WINUSB_INSTALLER_ENV = "SYNCHRONI_WINUSB_INSTALLER"
MANIFEST_URL_ENV = "SYNCHRONI_SDK_ASSETS_MANIFEST_URL"

DEFAULT_SDK_ASSETS_MANIFEST_URL = "https://synchroni-sdk-assets-654654390239.s3.us-east-2.amazonaws.com/manifest.json"
"""Public registry URL for SDK-distributed installers and related assets."""

WINUSB_ARTIFACT_ID = "winusb-installer"
"""Stable ``id`` field for the WinUSB claim helper in ``manifest.json``."""

_PACKAGED_INSTALLER_NAME = "synchroni-winusb-installer.exe"
_CACHE_DIR_NAME = "synchroni-sensor-sdk"
_CACHE_SUBDIR = "winusb"
_DOWNLOAD_TIMEOUT_S = 120
_USER_AGENT = "synchroni-sensor-sdk"

__all__ = [
    "DEFAULT_SDK_ASSETS_MANIFEST_URL",
    "MANIFEST_URL_ENV",
    "WINUSB_ARTIFACT_ID",
    "WINUSB_INSTALLER_ENV",
    "clear_winusb_installer_cache",
    "default_winusb_installer_path",
    "ensure_winusb_installer",
    "winusb_installer_cache_dir",
]


def winusb_installer_cache_dir() -> Path:
    """Return the directory used to cache the downloaded WinUSB installer."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        root = Path(base) if base else Path.home() / ".cache"
    return root / _CACHE_DIR_NAME / _CACHE_SUBDIR


def clear_winusb_installer_cache() -> Path:
    """Delete the on-disk WinUSB installer cache, if present.

    Returns
    -------
    Path
        The cache directory that was cleared (may not exist after this call).
    """
    cache_dir = winusb_installer_cache_dir()
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        logger.info("Cleared WinUSB installer cache at %s", cache_dir)
    return cache_dir


def default_winusb_installer_path() -> Path | None:
    """Locate a local installer: env override, then package resources, then cache."""
    env = os.environ.get(WINUSB_INSTALLER_ENV)
    if env:
        path = Path(env)
        if path.is_file():
            return path
    packaged = Path(__file__).resolve().parents[3] / "resources" / "windows-driver" / _PACKAGED_INSTALLER_NAME
    if packaged.is_file():
        return packaged
    return _find_cached_installer()


def ensure_winusb_installer(override: str | Path | None = None) -> Path:
    """Resolve the WinUSB installer path, downloading into cache if needed.

    Parameters
    ----------
    override:
        Explicit installer path. When set, the file must already exist.

    Raises
    ------
    WindowsClaimUnavailableError
        If the helper cannot be resolved locally or fetched/verified remotely.
    """
    if override is not None:
        path = Path(override)
        if path.is_file():
            return path
        raise WindowsClaimUnavailableError(f"WinUSB installer not found at {path}")

    found = default_winusb_installer_path()
    if found is not None:
        return found

    try:
        return _download_installer_from_manifest()
    except WindowsClaimUnavailableError:
        raise
    except Exception as exc:
        raise WindowsClaimUnavailableError(
            "WinUSB claim helper not available locally and remote download failed. "
            f"Set {WINUSB_INSTALLER_ENV} or pass winusb_installer_path= to SensorHub. "
            f"Underlying error: {exc}"
        ) from exc


def _find_cached_installer() -> Path | None:
    cache_dir = winusb_installer_cache_dir()
    if not cache_dir.is_dir():
        return None
    candidates = sorted(cache_dir.glob("*.exe"))
    for path in candidates:
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        expected = _read_hash_sidecar(path)
        if expected is not None and _sha256_file(path) != expected:
            logger.warning(
                "Cached WinUSB installer %s failed hash check; ignoring corrupt cache entry",
                path,
            )
            continue
        return path
    return None


def _manifest_url() -> str:
    return os.environ.get(MANIFEST_URL_ENV) or DEFAULT_SDK_ASSETS_MANIFEST_URL


def _download_installer_from_manifest() -> Path:
    """Fetch manifest.json, download the WinUSB artifact, verify SHA-256, write cache."""
    url = _manifest_url()
    logger.info("Fetching SDK assets manifest from %s", url)
    try:
        payload = _http_get_bytes(url)
    except urllib.error.URLError as exc:
        raise WindowsClaimUnavailableError(f"Failed to download SDK assets manifest from {url}: {exc}") from exc

    try:
        manifest: dict[str, Any] = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WindowsClaimUnavailableError(f"SDK assets manifest at {url} is not valid JSON: {exc}") from exc

    artifact = _select_winusb_artifact(manifest)
    artifact_url = str(artifact["url"])
    name = str(artifact.get("name") or f"{WINUSB_ARTIFACT_ID}.exe")
    expected_hash = str(artifact["sha256"]).strip().lower()
    if len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
        raise WindowsClaimUnavailableError(
            f"Invalid sha256 in manifest for artifact {WINUSB_ARTIFACT_ID!r}: {artifact.get('sha256')!r}"
        )

    cache_dir = winusb_installer_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / name

    logger.info("Downloading WinUSB installer %s → %s", artifact_url, dest)
    try:
        _download_file_verified(artifact_url, dest, expected_hash)
    except urllib.error.URLError as exc:
        raise WindowsClaimUnavailableError(f"Failed to download WinUSB installer from {artifact_url}: {exc}") from exc

    _write_hash_sidecar(dest, expected_hash)
    logger.info("Cached WinUSB installer at %s (sha256=%s)", dest, expected_hash)
    return dest


def _select_winusb_artifact(manifest: dict[str, Any]) -> dict[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise WindowsClaimUnavailableError("SDK assets manifest is missing an 'artifacts' array.")

    for item in artifacts:
        if not isinstance(item, dict):
            continue
        if item.get("id") != WINUSB_ARTIFACT_ID:
            continue
        platform = str(item.get("platform", "")).lower()
        if platform and platform != "windows":
            continue
        if not item.get("url") or not item.get("sha256"):
            raise WindowsClaimUnavailableError(f"Manifest artifact {WINUSB_ARTIFACT_ID!r} is missing url or sha256.")
        return item

    raise WindowsClaimUnavailableError(
        f"SDK assets manifest does not include artifact id {WINUSB_ARTIFACT_ID!r} for platform 'windows'."
    )


def _http_get_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S) as response:
        return cast(bytes, response.read())


def _download_file_verified(url: str, dest: Path, expected_sha256: str) -> None:
    partial = dest.with_name(f"{dest.name}.partial")
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    digest = hashlib.sha256()
    try:
        with (
            urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S) as response,
            partial.open("wb") as handle,
        ):
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
        actual = digest.hexdigest().lower()
        if actual != expected_sha256.lower():
            raise WindowsClaimUnavailableError(
                f"WinUSB installer hash mismatch after download: expected {expected_sha256}, got {actual}"
            )
        partial.replace(dest)
    except Exception:
        if partial.exists():
            with contextlib.suppress(OSError):
                partial.unlink()
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _hash_sidecar_path(installer_path: Path) -> Path:
    return Path(f"{installer_path}.sha256")


def _read_hash_sidecar(installer_path: Path) -> str | None:
    sidecar = _hash_sidecar_path(installer_path)
    if not sidecar.is_file():
        return None
    text = sidecar.read_text(encoding="utf-8").strip().lower()
    return text or None


def _write_hash_sidecar(installer_path: Path, sha256: str) -> None:
    _hash_sidecar_path(installer_path).write_text(f"{sha256.lower()}\n", encoding="utf-8")
