"""Unit tests for multi-adapter hub gate and pure inventory helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from synchroni_sensor_sdk.async_api.driver.managed_usb.firmware import (
    FirmwarePin,
    clear_firmware_resource_dir,
    ensure_firmware,
    pins_for,
)
from synchroni_sensor_sdk.async_api.driver.managed_usb.macos import merge_macos_adapters
from synchroni_sensor_sdk.async_api.driver.managed_usb.usb_common import (
    is_known_usb_bluetooth_adapter,
    managed_usb_transport_name,
    normalize_identity_token,
)
from synchroni_sensor_sdk.async_api.multi_adapter.controller import MultiAdapterController
from synchroni_sensor_sdk.async_api.radio.base import RadioScanHit
from synchroni_sensor_sdk.async_api.sensor_hub import SensorHub as AsyncSensorHub
from synchroni_sensor_sdk.core.bluetooth import SYSTEM_DEFAULT_ADAPTER_ID, BluetoothAdapter
from synchroni_sensor_sdk.core.exceptions import (
    AdapterFirmwareError,
    BluetoothAdapterBusyError,
    MultiAdapterDisabledError,
)


def test_system_default_id() -> None:
    assert SYSTEM_DEFAULT_ADAPTER_ID == "system:default"


def test_scan_cache_per_radio_lookup() -> None:
    """Each radio keeps its own MAC cache; hub prefers system then strongest RSSI."""
    hub = AsyncSensorHub()
    mac = "AA:BB:CC:DD:EE:FF"
    device = MagicMock()
    adv = MagicMock()

    def hit(name: str, rssi: int) -> RadioScanHit:
        return RadioScanHit(
            mac_address=mac,
            name=name,
            rssi=rssi,
            device=device,
            advertisement_data=adv,
        )

    hub._radios.system()._scanned[mac] = hit("sys", -70)
    weak = ManagedRadioStub("usb:1")
    strong = ManagedRadioStub("usb:2")
    weak._scanned[mac] = hit("d1", -80)
    strong._scanned[mac] = hit("d2", -40)
    hub._radios._managed["usb:1"] = weak  # type: ignore[assignment]
    hub._radios._managed["usb:2"] = strong  # type: ignore[assignment]

    found_sys = hub._lookup_hit(mac)
    assert found_sys is not None
    assert found_sys[0] == SYSTEM_DEFAULT_ADAPTER_ID
    assert found_sys[1].name == "sys"

    found_1 = hub._lookup_hit(mac, adapter_id="usb:1")
    assert found_1 is not None and found_1[1].name == "d1"

    del hub._radios.system()._scanned[mac]
    found_best = hub._lookup_hit(mac)
    assert found_best is not None
    assert found_best[0] == "usb:2"
    assert found_best[1].rssi == -40


class ManagedRadioStub:
    """Minimal radio stand-in for hub lookup tests."""

    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        self._scanned: dict[str, RadioScanHit] = {}

    def get_scanned(self, mac: str) -> RadioScanHit | None:
        return self._scanned.get(mac)

    def list_scanned(self) -> list[RadioScanHit]:
        return list(self._scanned.values())

    def is_scanning(self) -> bool:
        return False

    def stop_scan(self) -> None:
        return None

    def _set_scan_results_callback(self, callback: object) -> None:
        _ = callback


def test_known_vid_pid() -> None:
    assert is_known_usb_bluetooth_adapter("0a12", "0001")
    assert is_known_usb_bluetooth_adapter("10d7", "b012")
    assert is_known_usb_bluetooth_adapter("33fa", "0010")  # UGREEN BT5.4
    assert is_known_usb_bluetooth_adapter("2357", "0604")  # TP-Link UB500
    assert not is_known_usb_bluetooth_adapter("ffff", "ffff")


def test_managed_usb_transport_name() -> None:
    assert managed_usb_transport_name("10d7", "b012", "SN1") == "usb:10d7:b012/SN1"
    assert managed_usb_transport_name("10d7", "b012", None, device_index=1) == "usb:10d7:b012#1"


def test_normalize_identity_token() -> None:
    assert "abc" in normalize_identity_token("Abc 123")


def test_clear_firmware_resource_dir_keeps_gitkeep(tmp_path: Path) -> None:
    (tmp_path / ".gitkeep").write_text("")
    (tmp_path / "blob.bin").write_bytes(b"x")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "other.bin").write_bytes(b"y")
    clear_firmware_resource_dir(resource_dir=tmp_path)
    assert (tmp_path / ".gitkeep").is_file()
    assert not (tmp_path / "blob.bin").exists()
    assert not (nested / "other.bin").exists()


def test_merge_macos_adapters_prefers_libusb_and_keeps_system_only() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    system_a = BluetoothAdapter(
        id="usb:macos:2357:0604:sys",
        name="TP-Link system",
        source="managed_usb",
        platform="macos",
        vendor_id="2357",
        product_id="0604",
        serial_number="B8FBB39D0999",
        usb_transport="usb:2357:0604/B8FBB39D0999",
        is_external=True,
        last_seen_at=now,
    )
    system_b = BluetoothAdapter(
        id="usb:macos:33fa:0010:sys",
        name="UGREEN system",
        source="managed_usb",
        platform="macos",
        vendor_id="33fa",
        product_id="0010",
        is_external=True,
        last_seen_at=now,
    )
    libusb_a = BluetoothAdapter(
        id="usb:macos:2357:0604:lib",
        name="TP-Link libusb",
        source="managed_usb",
        platform="macos",
        transport="libusb",
        vendor_id="2357",
        product_id="0604",
        serial_number="B8FBB39D0999",
        usb_transport="usb:2357:0604/B8FBB39D0999",
        is_external=True,
        last_seen_at=now,
    )
    merged = merge_macos_adapters([libusb_a], [system_a, system_b])
    by_vid = {(a.vendor_id, a.product_id): a for a in merged}
    assert by_vid[("2357", "0604")].id == libusb_a.id
    assert by_vid[("33fa", "0010")].id == system_b.id


@pytest.mark.asyncio
async def test_hub_multi_adapter_gate() -> None:
    hub = AsyncSensorHub(enable_multi_adapter=False)
    assert hub.enable_multi_adapter is False
    with pytest.raises(MultiAdapterDisabledError):
        await hub.list_bluetooth_adapters()
    with pytest.raises(MultiAdapterDisabledError):
        await hub.scan_managed_usb()


@pytest.mark.asyncio
async def test_hub_multi_adapter_enabled_constructs_controller() -> None:
    hub = AsyncSensorHub(enable_multi_adapter=True)
    assert hub.enable_multi_adapter is True
    assert hub._multi is not None
    await hub.close()


@pytest.mark.asyncio
async def test_reserve_occupancy() -> None:
    ctl = MultiAdapterController()
    ctl._adapters["usb:test"] = BluetoothAdapter(
        id="usb:test",
        name="t",
        source="managed_usb",
        platform="test",
        usb_transport="usb:10d7:b012",
    )
    await ctl.reserve("usb:test", "AA:BB")
    with pytest.raises(BluetoothAdapterBusyError):
        await ctl.reserve("usb:test", "CC:DD")
    await ctl.occupy("usb:test", "AA:BB")
    with pytest.raises(BluetoothAdapterBusyError):
        await ctl.reserve("usb:test", "CC:DD")
    # Disconnect-style release must not revoke the hub-session claim.
    await ctl.release_occupancy("AA:BB")
    with pytest.raises(BluetoothAdapterBusyError):
        await ctl.reserve("usb:test", "CC:DD")
    # Same MAC may reconnect while the session claim is held.
    await ctl.reserve("usb:test", "AA:BB")
    await ctl.occupy("usb:test", "AA:BB")
    assert ctl._occupied.get("usb:test") == "AA:BB"
    await ctl.close()
    await ctl.reserve("usb:test", "CC:DD")


def test_firmware_ensure_ok(tmp_path: Path) -> None:
    blob = b"\x00" * 16
    path = tmp_path / "dongle.bin"
    path.write_bytes(blob)
    pin = FirmwarePin(
        vendor_id="10d7",
        product_id="b012",
        filename="dongle.bin",
        size_bytes=16,
        sha256=hashlib.sha256(blob).hexdigest(),
    )
    import synchroni_sensor_sdk.async_api.driver.managed_usb.firmware as fw

    original = fw.KNOWN_FIRMWARE_PINS
    fw.KNOWN_FIRMWARE_PINS = (pin,)
    try:
        assert pins_for("10d7", "b012") is pin
        adapter = BluetoothAdapter(
            id="usb:x",
            name="x",
            source="managed_usb",
            platform="test",
            vendor_id="10d7",
            product_id="b012",
        )
        resolved = ensure_firmware(adapter, resource_dir=tmp_path)
        assert resolved is not None
        assert resolved.name == "dongle.bin"
    finally:
        fw.KNOWN_FIRMWARE_PINS = original


def test_firmware_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bad.bin"
    path.write_bytes(b"x")
    pin = FirmwarePin(
        vendor_id="aa",
        product_id="bb",
        filename="bad.bin",
        size_bytes=1,
        sha256="0" * 64,
    )
    import synchroni_sensor_sdk.async_api.driver.managed_usb.firmware as fw

    original = fw.KNOWN_FIRMWARE_PINS
    fw.KNOWN_FIRMWARE_PINS = (pin,)
    try:
        adapter = BluetoothAdapter(
            id="usb:x",
            name="x",
            source="managed_usb",
            platform="test",
            vendor_id="aa",
            product_id="bb",
        )
        with pytest.raises(AdapterFirmwareError):
            ensure_firmware(adapter, resource_dir=tmp_path)
    finally:
        fw.KNOWN_FIRMWARE_PINS = original


def test_driver_factory_managed_metadata() -> None:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    from synchroni_sensor_sdk.core.bluetooth import ManagedUsbBackend
    from synchroni_sensor_sdk.core.driver import driver_factory

    device = MagicMock(spec=BLEDevice)
    adv = AdvertisementData(
        local_name="t",
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=-40,
        platform_data=(),
    )
    backend = ManagedUsbBackend(
        transport_name="usb:10d7:b012",
        peer_address="AA:BB:CC:DD:EE:FF",
        adapter_id="usb:test",
    )
    driver = driver_factory("11:22:33:44:55:66", device=device, advertisement_data=adv, managed_usb=backend)
    assert driver._managed_usb_transport == "usb:10d7:b012"  # type: ignore[attr-defined]
    assert driver._managed_usb_peer_address == "AA:BB:CC:DD:EE:FF"  # type: ignore[attr-defined]
