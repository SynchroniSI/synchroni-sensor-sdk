"""Tests for Realtek HCI firmware auto-fetch."""

from __future__ import annotations

import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from synchroni_sensor_sdk.async_api.driver.managed_usb import rtk_firmware as rtk_fw


@pytest.fixture(autouse=True)
def _reset_ensure_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtk_fw, "_fetched_targets", set())
    monkeypatch.delenv(rtk_fw.RTK_FIRMWARE_AUTO_ENV, raising=False)


def test_clear_rtk_firmware_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "rtk"
    cache.mkdir()
    (cache / "rtl8761bu_fw.bin").write_bytes(b"x")
    monkeypatch.setenv("BUMBLE_RTK_FIRMWARE_DIR", str(cache))
    rtk_fw._fetched_targets.add("marker")
    cleared = rtk_fw.clear_rtk_firmware_cache()
    assert cleared == cache
    assert not cache.exists()
    assert rtk_fw._fetched_targets == set()


def test_parse_usb_vid_pid() -> None:
    assert rtk_fw.parse_usb_vid_pid("usb:2357:0604/B8FBB39D0999") == ("2357", "0604")
    assert rtk_fw.parse_usb_vid_pid("usb:macos:2357:0604:b8fbb39d0999") == ("2357", "0604")
    assert rtk_fw.parse_usb_vid_pid("usb:8-3.1.3") is None


def test_ensure_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(rtk_fw.RTK_FIRMWARE_AUTO_ENV, "0")
    assert rtk_fw.ensure_rtk_firmware_available(transport_name="usb:2357:0604/x") is None


def test_ensure_skips_unknown_or_path_only_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_rtk = SimpleNamespace(rtk_firmware_dir=lambda: tmp_path, Driver=SimpleNamespace())
    bumble_mod = types.ModuleType("bumble")
    drivers_mod = types.ModuleType("bumble.drivers")
    drivers_mod.rtk = fake_rtk  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "bumble", bumble_mod)
    monkeypatch.setitem(__import__("sys").modules, "bumble.drivers", drivers_mod)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rtk_fw, "_http_get_url", lambda url: (_ for _ in ()).throw(AssertionError(url)))
        assert rtk_fw.ensure_rtk_firmware_available(transport_name="usb:8-3.1.3") == tmp_path
        assert rtk_fw.ensure_rtk_firmware_available(transport_name="usb:ffff:ffff") == tmp_path


def test_ensure_downloads_only_mapped_fw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def find_binary_path(name: str) -> Path | None:
        path = tmp_path / name
        return path if path.is_file() else None

    fake_rtk = SimpleNamespace(
        rtk_firmware_dir=lambda: tmp_path,
        Driver=SimpleNamespace(find_binary_path=find_binary_path),
        RTK_FIRMWARE_DIR_ENV="BUMBLE_RTK_FIRMWARE_DIR",
    )
    bumble_mod = types.ModuleType("bumble")
    drivers_mod = types.ModuleType("bumble.drivers")
    drivers_mod.rtk = fake_rtk  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "bumble", bumble_mod)
    monkeypatch.setitem(__import__("sys").modules, "bumble.drivers", drivers_mod)

    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        calls.append(name)
        if name.endswith("_config.bin") or name.endswith("_config"):
            raise RuntimeError("no config")
        return b"FW"

    monkeypatch.setattr(rtk_fw, "_http_get_url", fake_get)

    result = rtk_fw.ensure_rtk_firmware_available(transport_name="usb:2357:0604/B8FBB39D0999")
    assert result == tmp_path
    assert "rtl8761bu_fw.bin" in calls or "rtl8761bu_fw" in calls
    assert not any("rtl8821c" in c for c in calls)
    assert (tmp_path / "rtl8761bu_fw.bin").read_bytes() == b"FW"

    rtk_fw.ensure_rtk_firmware_available(transport_name="usb:2357:0604/B8FBB39D0999")
    # second call should not re-download
    assert calls.count("rtl8761bu_fw.bin") + calls.count("rtl8761bu_fw") == 1


def test_download_tries_mirrors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    def fake_get(url: str) -> bytes:
        attempts.append(url)
        if "git.kernel.org" in url:
            raise RuntimeError("HTTP 503")
        return b"OK"

    monkeypatch.setattr(rtk_fw, "_http_get_url", fake_get)
    data = rtk_fw._http_get_with_mirrors("rtl8761bu_fw.bin")  # noqa: SLF001
    assert data == b"OK"
    assert any("git.kernel.org" in u for u in attempts)
    assert any("linuxfromscratch" in u or "anduin" in u for u in attempts)
