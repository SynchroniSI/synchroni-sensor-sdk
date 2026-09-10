"""Shared advertisement filtering for Synchroni sensors on any host radio."""

from __future__ import annotations

from bleak import AdvertisementData, BLEDevice

SERVICE_GUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"


def derive_mac(advertisement_data: AdvertisementData) -> str | None:
    """Return product MAC from Synchroni/RFSTAR service data if present."""
    if SERVICE_GUID in advertisement_data.service_data:
        raw = advertisement_data.service_data[SERVICE_GUID]
        if len(raw) != 6:
            return None
        return ":".join(f"{byte:02X}" for byte in raw)
    if RFSTAR_SERVICE_GUID in advertisement_data.service_data:
        raw = advertisement_data.service_data[RFSTAR_SERVICE_GUID]
        if len(raw) != 6:
            return None
        return ":".join(f"{byte:02X}" for byte in reversed(raw))
    return None


def rssi_from_adv(advertisement_data: AdvertisementData) -> int:
    rssi = getattr(advertisement_data, "rssi", None)
    return int(rssi) if rssi is not None else 0


def is_synchroni_advertisement(advertisement_data: AdvertisementData) -> bool:
    """Require Synchroni's six-byte product identity payload.

    RFSTAR reuses the standard HID service UUID (0x1812), so UUID presence alone
    also matches keyboards, mice, and cached HID peripherals.  Real Synchroni
    advertisements carry the six-byte product MAC in service data.
    """
    return derive_mac(advertisement_data) is not None


def product_mac(device: BLEDevice, advertisement_data: AdvertisementData) -> str:
    del device
    mac = derive_mac(advertisement_data)
    if mac is None:
        raise ValueError("Synchroni advertisement has no valid six-byte product identity")
    return mac
