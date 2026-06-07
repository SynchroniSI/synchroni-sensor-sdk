"""Unit tests for RadioAdapter filter, system/managed radios, and registry."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from synchroni_sensor_sdk.async_api.multi_adapter.controller import MultiAdapterController
from synchroni_sensor_sdk.async_api.radio.filter import (
    SERVICE_GUID,
    derive_mac,
    is_synchroni_advertisement,
    product_mac,
)
from synchroni_sensor_sdk.async_api.radio.managed_usb import ManagedUsbRadioAdapter
from synchroni_sensor_sdk.async_api.radio.registry import RadioRegistry
from synchroni_sensor_sdk.async_api.radio.system_bleak import SystemBleakRadioAdapter
from synchroni_sensor_sdk.core.bluetooth import SYSTEM_DEFAULT_ADAPTER_ID, BluetoothAdapter
from synchroni_sensor_sdk.core.exceptions import MultiAdapterDisabledError


def _adv(*, service_uuids: list[str], service_data: dict[str, bytes], rssi: int = -50) -> AdvertisementData:
    return AdvertisementData(
        local_name="sensor",
        manufacturer_data={},
        service_data=service_data,
        service_uuids=service_uuids,
        tx_power=None,
        rssi=rssi,
        platform_data=(),
    )


def test_derive_mac_and_filter() -> None:
    mac_bytes = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    adv = _adv(service_uuids=[SERVICE_GUID], service_data={SERVICE_GUID: mac_bytes})
    assert is_synchroni_advertisement(adv)
    assert derive_mac(adv) == "AA:BB:CC:DD:EE:FF"
    device = MagicMock(spec=BLEDevice)
    device.address = "00:11:22:33:44:55"
    device.name = "s"
    assert product_mac(device, adv) == "AA:BB:CC:DD:EE:FF"


@pytest.mark.asyncio
async def test_system_bleak_scan_and_prepare() -> None:
    mac_bytes = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
    device = MagicMock(spec=BLEDevice)
    device.address = "AA:AA:AA:AA:AA:AA"
    device.name = "EEG-1"
    adv = _adv(service_uuids=[SERVICE_GUID], service_data={SERVICE_GUID: mac_bytes}, rssi=-42)

    radio = SystemBleakRadioAdapter()
    mock_scanner = MagicMock()
    mock_scanner.discover = AsyncMock(return_value={"x": (device, adv)})
    radio._scanner = mock_scanner

    hits = await radio.scan(100)
    assert len(hits) == 1
    assert hits[0].mac_address == "11:22:33:44:55:66"
    assert radio.get_scanned("11:22:33:44:55:66") is not None

    conn = await radio.prepare_connection("11:22:33:44:55:66")
    assert conn.managed_usb is None
    assert conn.device is device

    with pytest.raises(ValueError, match="not found"):
        await radio.prepare_connection("00:00:00:00:00:00")

    await radio.close()
    assert radio.list_scanned() == []


@pytest.mark.asyncio
async def test_managed_usb_scan_updates_cache() -> None:
    multi = MultiAdapterController()
    multi._adapters["usb:test"] = BluetoothAdapter(
        id="usb:test",
        name="t",
        source="managed_usb",
        platform="test",
        usb_transport="usb:10d7:b012",
        last_seen_at=datetime.now(UTC),
    )
    radio = ManagedUsbRadioAdapter(adapter_id="usb:test", multi=multi)

    device = MagicMock(spec=BLEDevice)
    adv = _adv(service_uuids=[SERVICE_GUID], service_data={})
    fake_hit = SimpleNamespace(
        mac_address="AA:BB:CC:DD:EE:FF",
        name="m",
        rssi=-30,
        peer_address="01:02:03:04:05:06",
        device=device,
        advertisement_data=adv,
    )

    with patch(
        "synchroni_sensor_sdk.async_api.driver.managed_usb.backend.scan_managed_usb_devices",
        new=AsyncMock(return_value=[fake_hit]),
    ):
        hits = await radio.scan(200)

    assert len(hits) == 1
    assert hits[0].peer_address == "01:02:03:04:05:06"
    assert radio.get_scanned("AA:BB:CC:DD:EE:FF") is not None

    with (
        patch(
            "synchroni_sensor_sdk.async_api.driver.managed_usb.firmware.ensure_firmware",
            return_value=None,
        ),
        patch(
            "synchroni_sensor_sdk.async_api.driver.managed_usb.windows_claim.require_claimable",
        ),
    ):
        conn = await radio.prepare_connection("AA:BB:CC:DD:EE:FF")

    assert conn.managed_usb is not None
    assert conn.managed_usb.transport_name == "usb:10d7:b012"
    assert conn.managed_usb.peer_address == "01:02:03:04:05:06"
    await radio.close()


@pytest.mark.asyncio
async def test_registry_system_and_managed() -> None:
    reg = RadioRegistry(None)
    assert reg.system().adapter_id == SYSTEM_DEFAULT_ADAPTER_ID
    assert (await reg.get(None)).adapter_id == SYSTEM_DEFAULT_ADAPTER_ID
    with pytest.raises(MultiAdapterDisabledError):
        await reg.get("usb:x")
    with pytest.raises(MultiAdapterDisabledError):
        await reg.list_managed_for_scan(None)

    multi = MultiAdapterController()
    multi._adapters["usb:m"] = BluetoothAdapter(
        id="usb:m",
        name="m",
        source="managed_usb",
        platform="test",
        usb_transport="usb:10d7:b012",
        last_seen_at=datetime.now(UTC),
    )
    reg2 = RadioRegistry(multi)
    with patch.object(multi, "refresh_adapters", new=AsyncMock(return_value=list(multi._adapters.values()))):
        radios = await reg2.list_managed_for_scan(["usb:m"])
    assert len(radios) == 1
    assert radios[0].adapter_id == "usb:m"
    again = await reg2.get("usb:m")
    assert again is radios[0]
    await reg2.close_all()
