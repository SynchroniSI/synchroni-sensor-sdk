from __future__ import annotations

from typing import TYPE_CHECKING

from synchroni_sensor_sdk.async_api.driver.gforce import GForceDriver

if TYPE_CHECKING:
    from bleak import AdvertisementData, BLEDevice

    from synchroni_sensor_sdk.async_api.driver.base import Driver
    from synchroni_sensor_sdk.core.bluetooth import ManagedUsbBackend

RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"


def driver_factory(
    address: str,
    *,
    device: BLEDevice | None = None,
    advertisement_data: AdvertisementData | None = None,
    managed_usb: ManagedUsbBackend | None = None,
) -> Driver:
    """Select a protocol driver from scan metadata and optional USB transport.

    Parameters
    ----------
    managed_usb:
        When set, the GForce stack talks over Bumble/libusb HCI instead of
        system Bleak. OYM vs RFSTAR is still derived from advertisement service data.
    """

    is_universal_stream = advertisement_data is not None and RFSTAR_SERVICE_GUID in advertisement_data.service_data
    return GForceDriver(
        address,
        device=device,
        advertisement_data=advertisement_data,
        is_universal_stream=is_universal_stream,
        managed_usb_transport=managed_usb.transport_name if managed_usb is not None else None,
        managed_usb_peer_address=managed_usb.peer_address if managed_usb is not None else None,
    )
