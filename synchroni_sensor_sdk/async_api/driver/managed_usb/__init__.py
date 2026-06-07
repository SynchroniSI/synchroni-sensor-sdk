"""Optional multi-adapter Bluetooth stack (Bumble HCI USB + inventory).

Import this package only when ``enable_multi_adapter=True`` on the hub.
"""

from __future__ import annotations

from synchroni_sensor_sdk.async_api.driver.managed_usb.inventory import (
    get_bluetooth_capability,
    list_all_adapters,
    list_managed_usb_adapters,
    system_default_adapter,
)

__all__ = [
    "get_bluetooth_capability",
    "list_all_adapters",
    "list_managed_usb_adapters",
    "system_default_adapter",
]
