"""Hub-owned multi-adapter controller: inventory, occupancy, claim.

Only constructed when :class:`~synchroni_sensor_sdk.async_api.sensor_hub.SensorHub`
is created with ``enable_multi_adapter=True``.

Scan/connect transport is owned by
:class:`~synchroni_sensor_sdk.async_api.radio.RadioAdapter` instances via the
hub's :class:`~synchroni_sensor_sdk.async_api.radio.RadioRegistry`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

from synchroni_sensor_sdk.core.bluetooth import (
    SYSTEM_DEFAULT_ADAPTER_ID,
    BluetoothAdapter,
    BluetoothCapability,
    ClaimResult,
)
from synchroni_sensor_sdk.core.exceptions import (
    BluetoothAdapterBusyError,
    BluetoothAdapterNotFoundError,
)

logger = logging.getLogger(__name__)


@dataclass
class MultiAdapterController:
    """Session state for dedicated dongle routing on one hub instance.

    Attributes
    ----------
    winusb_installer_path:
        Optional libwdi helper path for :meth:`claim_adapter`.
    firmware_resource_dir:
        Optional directory for pinned dongle firmware blobs.
    """

    winusb_installer_path: Path | str | None = None
    firmware_resource_dir: Path | str | None = None
    _adapters: dict[str, BluetoothAdapter] = field(default_factory=dict)
    # adapter_id -> sensor mac: hub-session claim (kept until hub close, not until disconnect).
    _occupied: dict[str, str] = field(default_factory=dict)
    _reserved: dict[str, str] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_system_adapter(self, adapter_id: str | None) -> bool:
        return adapter_id is None or adapter_id == SYSTEM_DEFAULT_ADAPTER_ID

    def capability(self) -> BluetoothCapability:
        from synchroni_sensor_sdk.async_api.driver.managed_usb.inventory import (
            get_bluetooth_capability,
        )

        return get_bluetooth_capability()

    async def refresh_adapters(self) -> list[BluetoothAdapter]:
        """Refresh dongle inventory and annotate occupancy."""
        from synchroni_sensor_sdk.async_api.driver.managed_usb.inventory import list_all_adapters

        raw = await list_all_adapters()
        annotated: list[BluetoothAdapter] = []
        by_id: dict[str, BluetoothAdapter] = {}
        for adapter in raw:
            in_use = adapter.id in self._occupied or adapter.id in self._reserved
            marked = replace(adapter, is_in_use=in_use)
            by_id[marked.id] = marked
            annotated.append(marked)
        self._adapters = by_id
        return annotated

    def list_cached_adapters(self) -> list[BluetoothAdapter]:
        return list(self._adapters.values())

    def get_adapter(self, adapter_id: str) -> BluetoothAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is not None:
            return adapter
        alias = self._find_adapter_alias(adapter_id)
        if alias is not None:
            return alias
        raise BluetoothAdapterNotFoundError(f"Adapter {adapter_id!r} is not in inventory.")

    def free_managed_adapter_ids(self) -> list[str]:
        """Managed USB adapters that are ready for scan/connect (not reserved/occupied)."""
        free: list[str] = []
        for adapter in self._adapters.values():
            if adapter.source != "managed_usb":
                continue
            if adapter.claim_required:
                continue
            if adapter.id in self._occupied or adapter.id in self._reserved:
                continue
            if not adapter.usb_transport:
                continue
            free.append(adapter.id)
        return free

    def is_reserved(self, adapter_id: str) -> bool:
        return adapter_id in self._reserved

    async def claim_adapter(self, adapter_id: str) -> ClaimResult:
        """Run Windows WinUSB claim for a claim_required adapter."""
        if adapter_id not in self._adapters:
            await self.refresh_adapters()
        adapter = self.get_adapter(adapter_id)
        from synchroni_sensor_sdk.async_api.driver.managed_usb.windows_claim import (
            claim_windows_adapter,
        )

        result = await claim_windows_adapter(adapter, installer_path=self.winusb_installer_path)
        await self.refresh_adapters()
        return result

    async def reserve(self, adapter_id: str, sensor_mac: str) -> None:
        """Temporarily lock a managed dongle for one connect attempt.

        An adapter already claimed by this hub for the same MAC may be reserved
        again (reconnect). A claim for a different MAC remains until hub close.
        """
        if self.is_system_adapter(adapter_id):
            return
        async with self._lock:
            owner = self._occupied.get(adapter_id)
            if owner is not None and owner != sensor_mac:
                raise BluetoothAdapterBusyError(f"Adapter {adapter_id} is already claimed by this hub for {owner}")
            if adapter_id in self._reserved and self._reserved[adapter_id] != sensor_mac:
                raise BluetoothAdapterBusyError(
                    f"Adapter {adapter_id} is already reserved for {self._reserved[adapter_id]}"
                )
            self._reserved[adapter_id] = sensor_mac

    async def release_reserve(self, adapter_id: str) -> None:
        if self.is_system_adapter(adapter_id):
            return
        async with self._lock:
            self._reserved.pop(adapter_id, None)

    async def occupy(self, adapter_id: str, sensor_mac: str) -> None:
        """Record a hub-session claim of the adapter for this sensor MAC.

        The claim persists across disconnects until :meth:`close` so the dongle
        stays bound to that routing decision for the rest of the hub lifetime.
        """
        if self.is_system_adapter(adapter_id):
            return
        async with self._lock:
            owner = self._occupied.get(adapter_id)
            if owner is not None and owner != sensor_mac:
                raise BluetoothAdapterBusyError(f"Adapter {adapter_id} is already claimed by this hub for {owner}")
            self._reserved.pop(adapter_id, None)
            self._occupied[adapter_id] = sensor_mac

    async def release_occupancy(self, sensor_mac: str) -> None:
        """Clear temporary reserves for a MAC after a failed or finished connect attempt.

        Does **not** revoke hub-session claims (``_occupied``); those are released
        only when the multi-adapter controller / hub is closed.
        """
        async with self._lock:
            for aid, mac in list(self._reserved.items()):
                if mac == sensor_mac:
                    self._reserved.pop(aid, None)

    def resolve_adapter(self, adapter_id: str) -> BluetoothAdapter:
        """Return an inventory row, matching by id or VID/PID(+serial) alias."""
        if adapter_id in self._adapters:
            return self._adapters[adapter_id]
        alias = self._find_adapter_alias(adapter_id)
        if alias is not None:
            return alias
        raise BluetoothAdapterNotFoundError(f"Adapter {adapter_id!r} is not in inventory.")

    def _find_adapter_alias(self, adapter_id: str) -> BluetoothAdapter | None:
        from synchroni_sensor_sdk.async_api.driver.managed_usb.windows import (
            normalize_windows_adapter_id,
        )

        needle = adapter_id.strip().lower()
        if not needle:
            return None
        needle_norm = normalize_windows_adapter_id(needle)
        for adapter in self._adapters.values():
            if adapter.id.lower() == needle:
                return adapter
            if normalize_windows_adapter_id(adapter.id) == needle_norm:
                return adapter
            if adapter.usb_transport and adapter.usb_transport.lower() in needle:
                return adapter
            if adapter.vendor_id and adapter.product_id:
                token = f"{adapter.vendor_id.lower()}:{adapter.product_id.lower()}"
                if token in needle:
                    if adapter.serial_number:
                        serial = adapter.serial_number.strip().lower()
                        if serial and serial in needle:
                            return adapter
                    else:
                        return adapter
        return None

    async def close(self) -> None:
        """Drop inventory, hub-session dongle claims, and powered radio stacks."""
        from synchroni_sensor_sdk.async_api.driver.managed_usb.backend import (
            close_all_radio_sessions,
        )

        await close_all_radio_sessions()
        async with self._lock:
            self._occupied.clear()
            self._reserved.clear()
            self._adapters.clear()
