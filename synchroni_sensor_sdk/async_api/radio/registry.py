"""Resolve and cache RadioAdapter instances for a hub."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from synchroni_sensor_sdk.async_api.radio.base import RadioAdapter
from synchroni_sensor_sdk.async_api.radio.managed_usb import ManagedUsbRadioAdapter
from synchroni_sensor_sdk.async_api.radio.system_bleak import SystemBleakRadioAdapter
from synchroni_sensor_sdk.core.bluetooth import SYSTEM_DEFAULT_ADAPTER_ID
from synchroni_sensor_sdk.core.exceptions import (
    BluetoothAdapterNotFoundError,
    MultiAdapterDisabledError,
)

if TYPE_CHECKING:
    from synchroni_sensor_sdk.async_api.multi_adapter.controller import MultiAdapterController

logger = logging.getLogger(__name__)


class RadioRegistry:
    """Hub-owned map of live radio adapters.

    The system Bleak radio is always present. Managed USB radios are created
    lazily when multi-adapter mode is enabled and inventory resolves them.
    """

    def __init__(
        self,
        multi: MultiAdapterController | None = None,
        *,
        firmware_resource_dir: Path | str | None = None,
    ) -> None:
        self._multi = multi
        self._firmware_resource_dir = firmware_resource_dir
        self._system = SystemBleakRadioAdapter()
        self._managed: dict[str, ManagedUsbRadioAdapter] = {}

    def system(self) -> SystemBleakRadioAdapter:
        return self._system

    def list_known(self) -> list[RadioAdapter]:
        """System + any already-created managed radios."""
        return [self._system, *self._managed.values()]

    async def get(self, adapter_id: str | None) -> RadioAdapter:
        """Return (and create if needed) the radio for **adapter_id**."""
        if adapter_id is None or adapter_id == SYSTEM_DEFAULT_ADAPTER_ID:
            return self._system
        if self._multi is None:
            raise MultiAdapterDisabledError("Managed USB radios require SensorHub(enable_multi_adapter=True).")
        multi = self._multi
        if adapter_id not in multi._adapters:
            await multi.refresh_adapters()
        adapter = multi.resolve_adapter(adapter_id)
        existing = self._managed.get(adapter.id)
        if existing is not None:
            return existing
        radio = ManagedUsbRadioAdapter(
            adapter_id=adapter.id,
            multi=multi,
            firmware_resource_dir=self._firmware_resource_dir or multi.firmware_resource_dir,
        )
        self._managed[adapter.id] = radio
        return radio

    async def list_managed_for_scan(
        self,
        adapter_ids: Sequence[str] | None = None,
    ) -> list[RadioAdapter]:
        """Select radios eligible for a scan pass (after inventory refresh).

        When **adapter_ids** is ``None``, returns only free managed USB radios
        (``scan_managed_usb``). When a list is given, each id may be
        ``system:default`` or a managed ``usb:…`` adapter.
        """
        if self._multi is None:
            raise MultiAdapterDisabledError("Managed USB scan requires SensorHub(enable_multi_adapter=True).")
        multi = self._multi
        await multi.refresh_adapters()

        if adapter_ids is None:
            ids = multi.free_managed_adapter_ids()
        else:
            ids = []
            seen: set[str] = set()
            for aid in adapter_ids:
                if multi.is_system_adapter(aid):
                    if SYSTEM_DEFAULT_ADAPTER_ID not in seen:
                        ids.append(SYSTEM_DEFAULT_ADAPTER_ID)
                        seen.add(SYSTEM_DEFAULT_ADAPTER_ID)
                    continue
                try:
                    adapter = multi.resolve_adapter(aid)
                except BluetoothAdapterNotFoundError:
                    logger.warning("Adapter %s not in inventory after refresh; skipping", aid)
                    continue
                # Temporary reserves block concurrent use; hub-session claims do not —
                # explicit scans on a claimed adapter are required for reconnect.
                if multi.is_reserved(adapter.id):
                    logger.info("Skipping reserved adapter %s during managed scan", adapter.id)
                    continue
                if adapter.claim_required:
                    logger.info("Skipping claim_required adapter %s during managed scan", adapter.id)
                    continue
                if adapter.source != "managed_usb" or not adapter.usb_transport:
                    logger.warning(
                        "Adapter %s is not a managed USB radio; skipping",
                        adapter.id,
                    )
                    continue
                if adapter.id in seen:
                    continue
                ids.append(adapter.id)
                seen.add(adapter.id)

        radios: list[RadioAdapter] = []
        for aid in ids:
            radios.append(await self.get(aid))
        return radios

    async def close_all(self) -> None:
        """Stop continuous scans and clear local caches on every known radio."""
        await self._system.close()
        for radio in list(self._managed.values()):
            await radio.close()
        self._managed.clear()
