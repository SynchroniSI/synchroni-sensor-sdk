"""Tests for WinUSB installer cache + remote download."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from synchroni_sensor_sdk.async_api.driver.managed_usb import winusb_installer as wi
from synchroni_sensor_sdk.core.exceptions import WindowsClaimUnavailableError


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "winusb"
    monkeypatch.setattr(wi, "winusb_installer_cache_dir", lambda: path)
    return path


def test_clear_winusb_installer_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / "winusb-installer.exe").write_bytes(b"helper")
    wi.clear_winusb_installer_cache()
    assert not cache_dir.exists()


def test_uses_cached_installer_without_network(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(wi.WINUSB_INSTALLER_ENV, raising=False)
    # Skip packaged resource lookup.
    monkeypatch.setattr(
        wi,
        "default_winusb_installer_path",
        lambda: wi._find_cached_installer(),  # noqa: SLF001
    )

    binary = b"MZ-cached-installer"
    digest = _sha256(binary)
    cache_dir.mkdir(parents=True)
    path = cache_dir / "winusb-installer.exe"
    path.write_bytes(binary)
    Path(f"{path}.sha256").write_text(digest + "\n", encoding="utf-8")

    with patch.object(wi, "_download_installer_from_manifest") as download:
        found = wi.ensure_winusb_installer()
    assert found == path
    download.assert_not_called()


def test_ignores_corrupt_cache_and_redownloads(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(wi.WINUSB_INSTALLER_ENV, raising=False)
    monkeypatch.setattr(wi, "default_winusb_installer_path", lambda: wi._find_cached_installer())  # noqa: SLF001

    cache_dir.mkdir(parents=True)
    bad = cache_dir / "winusb-installer.exe"
    bad.write_bytes(b"corrupt")
    Path(f"{bad}.sha256").write_text(_sha256(b"expected-different") + "\n", encoding="utf-8")

    body = b"MZ-fresh"
    digest = _sha256(body)
    manifest = {
        "schemaVersion": 1,
        "artifacts": [
            {
                "id": wi.WINUSB_ARTIFACT_ID,
                "name": "winusb-installer.exe",
                "platform": "windows",
                "url": "https://example.test/installers/winusb-installer.exe",
                "sha256": digest,
            }
        ],
    }

    def fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        del timeout
        url = str(getattr(request, "full_url", request))
        if "manifest" in url:
            return _FakeResponse(json.dumps(manifest).encode("utf-8"))
        return _FakeResponse(body)

    monkeypatch.setenv(wi.MANIFEST_URL_ENV, "https://example.test/manifest.json")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        path = wi.ensure_winusb_installer()

    assert path.read_bytes() == body
    assert Path(f"{path}.sha256").read_text(encoding="utf-8").strip() == digest


def test_download_from_manifest_verifies_hash(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(wi.WINUSB_INSTALLER_ENV, raising=False)
    monkeypatch.setattr(wi, "default_winusb_installer_path", lambda: None)

    body = b"MZ-remote-payload"
    digest = _sha256(body)
    manifest = {
        "schemaVersion": 1,
        "artifacts": [
            {
                "id": wi.WINUSB_ARTIFACT_ID,
                "name": "winusb-installer.exe",
                "platform": "windows",
                "url": "https://example.test/installers/winusb-installer.exe",
                "sha256": digest,
            }
        ],
    }

    def fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        del timeout
        url = str(getattr(request, "full_url", request))
        if "manifest" in url:
            return _FakeResponse(json.dumps(manifest).encode("utf-8"))
        if url.endswith(".exe"):
            return _FakeResponse(body)
        raise AssertionError(url)

    monkeypatch.setenv(wi.MANIFEST_URL_ENV, "https://example.test/manifest.json")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        path = wi.ensure_winusb_installer()

    assert path == cache_dir / "winusb-installer.exe"
    assert path.read_bytes() == body
    assert Path(f"{path}.sha256").read_text(encoding="utf-8").strip() == digest


def test_download_rejects_hash_mismatch(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(wi.WINUSB_INSTALLER_ENV, raising=False)
    monkeypatch.setattr(wi, "default_winusb_installer_path", lambda: None)

    body = b"MZ-bad"
    manifest = {
        "schemaVersion": 1,
        "artifacts": [
            {
                "id": wi.WINUSB_ARTIFACT_ID,
                "name": "winusb-installer.exe",
                "platform": "windows",
                "url": "https://example.test/installers/winusb-installer.exe",
                "sha256": "0" * 64,
            }
        ],
    }

    def fake_urlopen(request: object, timeout: float | None = None) -> _FakeResponse:
        del timeout
        url = str(getattr(request, "full_url", request))
        if "manifest" in url:
            return _FakeResponse(json.dumps(manifest).encode("utf-8"))
        return _FakeResponse(body)

    monkeypatch.setenv(wi.MANIFEST_URL_ENV, "https://example.test/manifest.json")
    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        pytest.raises(WindowsClaimUnavailableError, match="hash mismatch"),
    ):
        wi.ensure_winusb_installer()
    assert not (cache_dir / "winusb-installer.exe").exists()


def test_override_missing_raises() -> None:
    with pytest.raises(WindowsClaimUnavailableError, match="not found"):
        wi.ensure_winusb_installer("/no/such/installer.exe")
