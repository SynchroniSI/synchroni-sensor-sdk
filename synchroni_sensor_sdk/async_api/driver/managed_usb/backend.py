"""Bumble + libusb HCI backend for dedicated USB Bluetooth dongles.

Requires the optional ``managed-usb`` dependency (Bumble). The default
:class:`~synchroni_sensor_sdk.async_api.sensor_hub.SensorHub` path never
imports this module.

:class:`ManagedUsbBleClient` exposes a BleakClient-compatible surface so
:class:`~synchroni_sensor_sdk.async_api.driver.gforce.protocol.GForceProtocol`
can share the GForce command/stream stack across OS Bleak and USB HCI.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import platform
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from bleak.backends.device import BLEDevice as BleakBLEDevice
from bleak.backends.scanner import AdvertisementData

logger = logging.getLogger(__name__)

SERVICE_GUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
RFSTAR_SERVICE_GUID = "00001812-0000-1000-8000-00805f9b34fb"


try:
    from bumble import hci
    from bumble.core import UUID
    from bumble.device import Advertisement, AdvertisingData, Device, Peer
    from bumble.hci import Address
    from bumble.transport import open_transport
except ImportError:  # pragma: no cover - exercised via _require_bumble
    hci = None  # type: ignore[assignment]
    UUID = None  # type: ignore[misc,assignment]
    Advertisement = None  # type: ignore[misc,assignment]
    AdvertisingData = None  # type: ignore[misc,assignment]
    Device = None  # type: ignore[misc,assignment]
    Peer = None  # type: ignore[misc,assignment]
    Address = None  # type: ignore[misc,assignment]
    open_transport = None  # type: ignore[assignment]

MANAGED_USB_DEFAULT_SCAN_TIMEOUT_S = 1.0
MANAGED_USB_PEER_DISCONNECT_TIMEOUT_S = 3.0
MANAGED_USB_PAIRING_CLEANUP_TIMEOUT_S = 2.0
MANAGED_USB_TRANSPORT_CLOSE_TIMEOUT_S = 3.0
MANAGED_USB_TRANSPORT_OPEN_TIMEOUT_S = 8.0
MANAGED_USB_POWER_ON_TIMEOUT_S = 15.0
MANAGED_USB_POWER_OFF_TIMEOUT_S = 3.0
MANAGED_USB_SCAN_COMMAND_TIMEOUT_S = 5.0
MANAGED_USB_POST_CLOSE_SETTLE_S = 0.5
MANAGED_USB_POST_CLOSE_STUCK_SETTLE_S = 1.25
MANAGED_USB_ACL_IN_TRANSFER_COUNT = 4
MANAGED_USB_USB_READ_SIZE = 4096
_MANAGED_USB_DEFAULT_ATT_MTU = 23
_MANAGED_USB_REQUESTED_ATT_MTU = 247

# Retain in-flight transport closes so wait_for timeouts do not cancel and leave USB claimed.
# Keyed by transport_name so the next open can wait for the same dongle to go idle.
_background_transport_closes: dict[str, set[asyncio.Task[Any]]] = {}
# Hub-scoped powered HCI stacks (one per Bumble transport string) reused across scan/connect.
_radio_sessions: dict[str, ManagedUsbRadioSession] = {}
_radio_sessions_lock = asyncio.Lock()

# Op-codes disabled on quirky USB HCI dongles (Bumble HCI command values).
_MANAGED_USB_DISABLED_COMMANDS: frozenset[int] = frozenset()

if hci is not None:
    _MANAGED_USB_DISABLED_COMMANDS = frozenset(
        (
            hci.HCI_LE_EXTENDED_CREATE_CONNECTION_COMMAND,
            hci.HCI_LE_READ_MAXIMUM_ADVERTISING_DATA_LENGTH_COMMAND,
            hci.HCI_LE_READ_NUMBER_OF_SUPPORTED_ADVERTISING_SETS_COMMAND,
        )
    )


class ManagedUsbRadioSession:
    """Powered Bumble HCI stack for one USB dongle, owned for a hub lifetime.

    Scan and GATT connect share open/power_on so we avoid the common
    scan-close → sticky-libusb → connect-fail cycle on Realtek consumer dongles.
    """

    def __init__(self, transport_name: str) -> None:
        self.transport_name = transport_name
        self._lock = asyncio.Lock()
        self._transport: Any | None = None
        self._device: Any | None = None

    @property
    def device(self) -> Any | None:
        return self._device

    async def ensure_ready(self) -> Any:
        """Open USB + power the controller if needed; return the Bumble Device."""
        _require_bumble()
        async with self._lock:
            if self._device is not None and getattr(self._device, "powered_on", False):
                return self._device

            await _await_usb_idle(self.transport_name)
            if self._transport is not None:
                await _close_managed_usb_transport(self._transport, transport_name=self.transport_name)
                self._transport = None
                self._device = None

            await _prepare_managed_usb_session(self.transport_name)
            self._transport = await _await_managed_usb_stage(
                open_transport(self.transport_name),
                timeout_s=MANAGED_USB_TRANSPORT_OPEN_TIMEOUT_S,
                stage="transport open",
                transport_name=self.transport_name,
            )
            hci_source, hci_sink = self._transport
            _enable_windows_acl_read_pipeline(hci_source)
            self._device = Device.with_hci(
                "Synchroni USB BLE",
                Address("F0:F1:F2:F3:F4:F5"),
                hci_source,
                hci_sink,
            )
            _disable_problematic_managed_usb_commands(self._device)
            await _await_managed_usb_stage(
                self._device.power_on(),
                timeout_s=MANAGED_USB_POWER_ON_TIMEOUT_S,
                stage="controller initialization",
                transport_name=self.transport_name,
            )
            _disable_problematic_managed_usb_commands(self._device)
            logger.info("Managed USB radio session ready: %s", self.transport_name)
            return self._device

    async def scan(self, timeout_s: float) -> list[ManagedUsbScanHit]:
        """LE scan on the shared controller; leaves the stack powered for connect."""
        device = await self.ensure_ready()
        found: dict[str, ManagedUsbScanHit] = {}
        scanning = False
        async with self._lock:
            try:

                def on_advertisement(advertisement: Advertisement) -> None:
                    hit = _hit_from_advertisement(advertisement, transport_name=self.transport_name)
                    if hit is None:
                        return
                    key = _normalize_mac(hit.mac_address)
                    existing = found.get(key)
                    if existing is None or hit.rssi > existing.rssi:
                        found[key] = hit

                device.on("advertisement", on_advertisement)
                await _await_managed_usb_stage(
                    device.start_scanning(legacy=True, active=True, filter_duplicates=False),
                    timeout_s=MANAGED_USB_SCAN_COMMAND_TIMEOUT_S,
                    stage="legacy scan start",
                    transport_name=self.transport_name,
                )
                scanning = True
                await asyncio.sleep(max(timeout_s, 0.1))
            finally:
                if scanning:
                    await _soft_await(
                        device.stop_scanning(legacy=True),
                        timeout_s=MANAGED_USB_SCAN_COMMAND_TIMEOUT_S,
                        stage="legacy scan stop",
                        transport_name=self.transport_name,
                        retain_on_timeout=False,
                    )
                with suppress(Exception):
                    device.remove_listener("advertisement", on_advertisement)
        return list(found.values())

    async def close(self) -> None:
        """Power off and release USB (hub teardown)."""
        async with self._lock:
            device = self._device
            transport = self._transport
            self._device = None
            self._transport = None
            if device is not None and getattr(device, "powered_on", False):
                await _soft_await(
                    device.power_off(),
                    timeout_s=MANAGED_USB_POWER_OFF_TIMEOUT_S,
                    stage="controller power off",
                    transport_name=self.transport_name,
                    retain_on_timeout=False,
                )
            if transport is not None:
                await _close_managed_usb_transport(transport, transport_name=self.transport_name)
                await asyncio.sleep(MANAGED_USB_POST_CLOSE_SETTLE_S)
            logger.info("Managed USB radio session closed: %s", self.transport_name)


async def acquire_radio_session(transport_name: str) -> ManagedUsbRadioSession:
    """Return (or create) the hub-scoped radio session for ``transport_name``."""
    async with _radio_sessions_lock:
        session = _radio_sessions.get(transport_name)
        if session is None:
            session = ManagedUsbRadioSession(transport_name)
            _radio_sessions[transport_name] = session
        return session


async def close_radio_session(transport_name: str) -> None:
    async with _radio_sessions_lock:
        session = _radio_sessions.pop(transport_name, None)
    if session is not None:
        await session.close()


async def close_all_radio_sessions() -> None:
    async with _radio_sessions_lock:
        sessions = list(_radio_sessions.values())
        _radio_sessions.clear()
    for session in sessions:
        with suppress(Exception):
            await session.close()


def _enable_windows_acl_read_pipeline(source: Any) -> None:
    """Keep several WinUSB bulk-IN reads queued to avoid gaps between completions.

    Bumble currently submits one ACL read and only re-submits it from the completion callback.
    WinUSB can leave a scheduling gap in that handoff, which is enough for small Bluetooth dongle
    buffers to overflow. Bumble's source already supports multiple in-flight transfers for SCO;
    registering extra ACL transfers in the same lifecycle collection ensures they are also
    cancelled and closed during normal transport teardown.
    """
    if platform.system() != "Windows":
        return

    required_attributes = (
        "bulk_in",
        "device",
        "done",
        "isochronous_in_transfers",
        "transfer_callback",
    )
    if any(not hasattr(source, attribute) for attribute in required_attributes):
        logger.warning("Bumble USB source does not support pipelined ACL reads; using its default transfer")
        return

    additional_transfer_count = MANAGED_USB_ACL_IN_TRANSFER_COUNT - 1
    if additional_transfer_count <= 0:
        return

    added = 0
    for _ in range(additional_transfer_count):
        try:
            transfer = source.device.getTransfer()
            transfer.setBulk(
                source.bulk_in.getAddress(),
                MANAGED_USB_USB_READ_SIZE,
                callback=source.transfer_callback,
                user_data=hci.HCI_ACL_DATA_PACKET,
            )
            source.done[transfer] = asyncio.Event()
            # UsbPacketSource.terminate() already cancels every transfer in this collection.
            source.isochronous_in_transfers.append(transfer)
            transfer.submit()
            added += 1
        except Exception:
            logger.warning("Failed to queue an additional WinUSB ACL read", exc_info=True)
            break

    logger.info("Managed USB WinUSB ACL read pipeline active with %s transfer(s)", added + 1)


class ManagedUsbBleClient:
    """Small BleakClient-compatible shim backed by Bumble over an HCI USB transport."""

    def __init__(
        self,
        *,
        transport_name: str,
        peer_address: str,
        disconnected_callback: Callable[[Any], None] | None = None,
    ) -> None:
        self._transport_name = transport_name
        self._peer_address = peer_address
        self._disconnected_callback = disconnected_callback
        self._radio: ManagedUsbRadioSession | None = None
        self._device: Device | None = None
        self._peer: Peer | None = None
        self._connection: Any = None
        self._characteristics_discovered = False
        self._notify_subscribers: dict[str, Callable[[bytes], Any]] = {}
        self._transport_loss_cleanup_task: asyncio.Task[None] | None = None
        self._release_lock = asyncio.Lock()
        self._transport_release_error: Exception | None = None
        self._disconnecting = False
        self._mtu_size = _MANAGED_USB_DEFAULT_ATT_MTU
        self.is_connected = False

    @property
    def mtu_size(self) -> int:
        if self._peer is not None:
            gatt_client = getattr(self._peer, "gatt_client", None)
            if gatt_client is not None:
                return int(gatt_client.mtu)
        if self._connection is not None:
            return int(getattr(self._connection, "att_mtu", self._mtu_size))
        return self._mtu_size

    @property
    def transport_released(self) -> bool:
        task = self._transport_loss_cleanup_task
        return self._connection is None and self._transport_release_error is None and (task is None or task.done())

    async def connect(self) -> bool:
        _require_bumble()
        logger.info("Connecting managed USB BLE peer %s via %s", self._peer_address, self._transport_name)
        try:
            self._radio = await acquire_radio_session(self._transport_name)
            self._device = await self._radio.ensure_ready()
            assert self._device is not None
            self._connection = await _await_managed_usb_stage(
                self._device.connect(self._peer_address),
                timeout_s=MANAGED_USB_POWER_ON_TIMEOUT_S,
                stage="peer connect",
                transport_name=self._transport_name,
            )
            self._connection.on("disconnection", self._on_disconnection)
            self._peer = Peer(self._connection)
            await self._request_larger_mtu()
            self._mtu_size = self.mtu_size
            logger.info("Managed USB BLE peer connected with ATT MTU %s", self._mtu_size)
            self.is_connected = True
            return True
        except (Exception, asyncio.CancelledError):
            try:
                await self._release_resources(disconnect_peer=True)
            except (Exception, asyncio.CancelledError):
                logger.warning(
                    "Failed to release managed USB peer after connection failure: %s",
                    self._transport_name,
                    exc_info=True,
                )
            raise

    async def disconnect(self) -> bool:
        self._disconnecting = True
        cleanup_task = self._transport_loss_cleanup_task
        try:
            if cleanup_task is not None and not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except Exception as exc:
                    logger.warning("Managed USB BLE transport-loss cleanup failed before explicit release: %s", exc)

            release_task = asyncio.create_task(self._release_resources(disconnect_peer=True))
            try:
                await asyncio.shield(release_task)
            except asyncio.CancelledError:
                logger.warning("Managed USB BLE disconnect was cancelled; waiting for peer release")
                await release_task
                raise
        finally:
            self._disconnecting = False
        return True

    async def start_notify(self, char_specifier: str, callback: Callable[[Any, bytearray], None]) -> None:
        peer = self._require_peer()
        characteristic = await self._get_characteristic(char_specifier)

        def subscriber(data: bytes) -> None:
            callback(characteristic.handle, bytearray(data))

        self._notify_subscribers[_normalize_uuid(char_specifier)] = subscriber
        await peer.subscribe(characteristic, subscriber)
        logger.info("Managed USB BLE notifications enabled for %s", _normalize_uuid(char_specifier))

    async def stop_notify(self, char_specifier: str) -> None:
        peer = self._require_peer()
        characteristic = await self._get_characteristic(char_specifier)
        subscriber = self._notify_subscribers.pop(_normalize_uuid(char_specifier), None)
        await peer.unsubscribe(characteristic, subscriber)

    async def write_gatt_char(
        self,
        char_specifier: str,
        data: bytes | bytearray,
        response: bool | None = None,
    ) -> None:
        peer = self._require_peer()
        characteristic = await self._get_characteristic(char_specifier)
        if response is None:
            from bumble.gatt import Characteristic

            response = bool(characteristic.properties & Characteristic.Properties.WRITE)
        await peer.write_value(characteristic, bytes(data), with_response=response)

    def _on_disconnection(self, *_args: object) -> None:
        self.is_connected = False
        if not self._disconnecting:
            self._schedule_transport_loss_cleanup()
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)

    def _require_peer(self) -> Peer:
        if self._peer is None:
            raise RuntimeError("Managed USB BLE client is not connected.")
        return self._peer

    async def _request_larger_mtu(self) -> None:
        peer = self._require_peer()
        try:
            negotiated_mtu = await peer.request_mtu(_MANAGED_USB_REQUESTED_ATT_MTU)
        except Exception as exc:
            logger.info(
                "Managed USB BLE peer kept default ATT MTU %s; MTU request failed: %s",
                self._mtu_size,
                exc,
            )
            return
        self._mtu_size = int(negotiated_mtu)

    async def _get_characteristic(self, uuid: str) -> Any:
        peer = self._require_peer()
        if not self._characteristics_discovered:
            await peer.discover_services()
            for service in peer.services:
                await service.discover_characteristics()
            self._characteristics_discovered = True

        matches = peer.get_characteristics_by_uuid(UUID(uuid))
        if not matches:
            raise RuntimeError(f"GATT characteristic not found: {uuid}")
        return matches[0]

    async def _release_resources(self, *, disconnect_peer: bool) -> None:
        """Disconnect the BLE peer and clear pairing; keep the shared radio session alive."""
        async with self._release_lock:
            try:
                connection = self._connection
                if disconnect_peer and connection is not None:
                    try:
                        await _soft_await(
                            connection.disconnect(),
                            timeout_s=MANAGED_USB_PEER_DISCONNECT_TIMEOUT_S,
                            stage="peer disconnect",
                            transport_name=self._transport_name,
                            retain_on_timeout=False,
                        )
                    except Exception as exc:
                        logger.warning("Managed USB BLE peer disconnect failed: %s", exc)

                try:
                    await _soft_await(
                        self._delete_pairing_state(),
                        timeout_s=MANAGED_USB_PAIRING_CLEANUP_TIMEOUT_S,
                        stage="pairing cleanup",
                        transport_name=self._transport_name,
                        retain_on_timeout=False,
                    )
                except Exception as exc:
                    logger.warning("Managed USB pairing cleanup failed: %s", exc)
            except Exception as exc:
                self._transport_release_error = exc
                logger.error("Managed USB BLE peer release failed for %s: %s", self._transport_name, exc)
                raise
            else:
                self._transport_release_error = None
                logger.info("Managed USB BLE peer released: %s", self._transport_name)
            finally:
                self._clear_connection_state()

    def _schedule_transport_loss_cleanup(self) -> None:
        task = self._transport_loss_cleanup_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Managed USB BLE peer disconnected unexpectedly; no running loop to release peer")
            return
        self._transport_loss_cleanup_task = loop.create_task(self._cleanup_after_transport_loss())
        self._transport_loss_cleanup_task.add_done_callback(self._log_transport_loss_cleanup_result)

    async def _cleanup_after_transport_loss(self) -> None:
        logger.warning(
            "Managed USB BLE peer disconnected unexpectedly; releasing peer on %s",
            self._transport_name,
        )
        await self._release_resources(disconnect_peer=False)

    def _log_transport_loss_cleanup_result(self, task: asyncio.Task[None]) -> None:
        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.warning("Managed USB BLE peer-loss cleanup failed: %s", exc)

    def _clear_connection_state(self) -> None:
        # Keep _device / _radio: they belong to the hub-scoped ManagedUsbRadioSession.
        self._peer = None
        self._connection = None
        self._characteristics_discovered = False
        self._notify_subscribers.clear()
        self._mtu_size = _MANAGED_USB_DEFAULT_ATT_MTU
        self.is_connected = False

    async def _delete_pairing_state(self) -> None:
        device = self._device
        if device is None:
            return
        keystore = getattr(device, "keystore", None)
        if keystore is None:
            return

        addresses = {self._peer_address}
        connection = self._connection
        peer_address = getattr(connection, "peer_address", None)
        if peer_address is not None:
            addresses.add(str(peer_address))

        delete = getattr(keystore, "delete", None)
        if delete is None:
            return

        for address in addresses:
            try:
                await delete(address)
            except KeyError:
                continue
            except Exception as exc:
                logger.warning("Failed to delete managed USB pairing state for %s: %s", address, exc)

        refresh_resolving_list = getattr(device, "refresh_resolving_list", None)
        if refresh_resolving_list is not None:
            with suppress(Exception):
                await refresh_resolving_list()


@dataclass(frozen=True)
class ManagedUsbScanHit:
    """One Synchroni advertisement seen on a managed USB transport."""

    mac_address: str
    name: str
    rssi: int
    peer_address: str
    transport_name: str
    device: BleakBLEDevice
    advertisement_data: AdvertisementData


async def scan_managed_usb_devices(
    *,
    transport_name: str,
    timeout_s: float = MANAGED_USB_DEFAULT_SCAN_TIMEOUT_S,
) -> list[ManagedUsbScanHit]:
    """Scan Synchroni advertisements through one Bumble-backed managed USB transport.

    Parameters
    ----------
    transport_name:
        Bumble ``open_transport`` string (from adapter inventory).
    timeout_s:
        Active scan duration after controller power-on.

    Returns
    -------
    list[ManagedUsbScanHit]
        Best RSSI hit per device MAC (Synchroni service data only).

    Raises
    ------
    ManagedUsbUnavailableError
        When Bumble is not installed.
    TimeoutError
        Controllers that hang during open/power-on/scan command.
    """
    radio = await acquire_radio_session(transport_name)
    return await radio.scan(timeout_s)


async def rediscover_peer_address(
    *,
    transport_name: str,
    target_mac: str,
    timeout_s: float = MANAGED_USB_DEFAULT_SCAN_TIMEOUT_S,
) -> ManagedUsbScanHit | None:
    """Find a peer address for ``target_mac`` by scanning one managed transport.

    Used when the app selects an eligible dongle without a prior route on that
    adapter for the sensor MAC.
    """
    target = _normalize_mac(target_mac)
    hits = await scan_managed_usb_devices(transport_name=transport_name, timeout_s=timeout_s)
    for hit in hits:
        if _normalize_mac(hit.mac_address) == target:
            return hit
    return None


def _hit_from_advertisement(advertisement: Any, *, transport_name: str) -> ManagedUsbScanHit | None:
    service_data = _service_data_from_advertisement(advertisement)
    if SERVICE_GUID not in service_data and RFSTAR_SERVICE_GUID not in service_data:
        return None

    if SERVICE_GUID in service_data:
        mac = _mac_from_bytes(service_data[SERVICE_GUID])
        service_uuids = [SERVICE_GUID]
    else:
        mac = _mac_from_bytes(bytes(reversed(service_data[RFSTAR_SERVICE_GUID])))
        service_uuids = [RFSTAR_SERVICE_GUID]

    name = _advertisement_name(advertisement) or str(advertisement.address)
    peer_address = str(advertisement.address)
    bleak_device = BleakBLEDevice(peer_address, name, {"bumble_advertisement": advertisement})
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=service_uuids,
        tx_power=None,
        rssi=advertisement.rssi,
        platform_data=(advertisement,),
    )
    return ManagedUsbScanHit(
        mac_address=mac,
        name=name,
        rssi=int(advertisement.rssi),
        peer_address=peer_address,
        transport_name=transport_name,
        device=bleak_device,
        advertisement_data=adv,
    )


def _require_bumble() -> None:
    try:
        import bumble  # noqa: F401
    except ImportError as exc:
        from synchroni_sensor_sdk.core.exceptions import ManagedUsbUnavailableError

        raise ManagedUsbUnavailableError(
            "Managed USB requires the optional dependency 'bumble'. "
            "Install with: pip install synchroni-sensor-sdk[managed-usb]"
        ) from exc


async def _prepare_managed_usb_session(transport_name: str) -> None:
    """Fetch host-side RTK firmware for this dongle (if mapped) before Bumble power-on."""
    from synchroni_sensor_sdk.async_api.driver.managed_usb.rtk_firmware import (
        ensure_rtk_firmware_available,
    )

    try:
        cache = await asyncio.to_thread(
            ensure_rtk_firmware_available,
            transport_name=transport_name,
        )
        if cache is not None:
            logger.debug("Managed USB RTK firmware ready for %s (cache=%s)", transport_name, cache)
    except Exception as exc:
        # Non-fatal: Bumble may still work if firmware is already on-device or cached elsewhere.
        logger.warning(
            "Could not auto-fetch Realtek HCI firmware for %s (%s); continuing without it",
            transport_name,
            exc,
        )


def _disable_problematic_managed_usb_commands(device: Any) -> None:
    """Install controller quirks before power-on and update an initialized mask."""
    host: Any = device.host
    if not getattr(host, "_synchroni_command_quirks_installed", False):
        supports_command = host.supports_command

        def supports_managed_usb_command(op_code: int) -> bool:
            return op_code not in _MANAGED_USB_DISABLED_COMMANDS and bool(supports_command(op_code))

        host.supports_command = supports_managed_usb_command
        host._synchroni_command_quirks_installed = True

    for op_code in _MANAGED_USB_DISABLED_COMMANDS:
        command_mask = hci.HCI_SUPPORTED_COMMANDS_MASKS.get(op_code, 0)
        if command_mask == 0:
            continue
        host.local_supported_commands &= ~command_mask


async def _await_managed_usb_stage(
    operation: Awaitable[Any],
    *,
    timeout_s: float,
    stage: str,
    transport_name: str,
) -> Any:
    """Hard-timeout helper for open/power_on — failure means the stage did not succeed.

    Do not use this for transport close; see `_close_managed_usb_transport`.
    """
    try:
        return await asyncio.wait_for(operation, timeout=max(timeout_s, 0.1))
    except TimeoutError as exc:
        raise TimeoutError(
            f"Managed USB {stage} timed out after {timeout_s:.1f}s on {transport_name}. "
            "Unplug and reconnect the Bluetooth dongle, then retry."
        ) from exc


async def _soft_await(
    operation: Awaitable[Any],
    *,
    timeout_s: float,
    stage: str,
    transport_name: str,
    retain_on_timeout: bool = True,
) -> bool:
    """Wait up to timeout; only keep the task alive past timeout when retain is set.

    Transport *close* must never be cancelled (``retain_on_timeout=True``): a cancelled
    libusb release leaves the interface claimed and the next open/inventory broken.
    Pre-close HCI stages (stop scan / power off) should set ``retain_on_timeout=False``
    so a hung command is cancelled before USB close runs on the same transport.
    """

    async def _run() -> Any:
        return await operation

    task: asyncio.Task[Any] = asyncio.create_task(_run())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(timeout_s, 0.1))
        return True
    except TimeoutError:
        logger.warning(
            "Managed USB %s still running after %.1fs on %s; %s",
            stage,
            timeout_s,
            transport_name,
            "not cancelling" if retain_on_timeout else "cancelling so close can reclaim USB",
        )
        if retain_on_timeout:
            _track_background_task(transport_name, task)
        else:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        return False
    except Exception:
        logger.warning(
            "Managed USB %s failed on %s",
            stage,
            transport_name,
            exc_info=True,
        )
        if not task.done():
            if retain_on_timeout:
                _track_background_task(transport_name, task)
            else:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        return False


async def _close_managed_usb_transport(transport: Any, *, transport_name: str) -> bool:
    """Close a Bumble USB transport without cancelling mid-shutdown.

    Bumble's close awaits a native USB event loop. Cancelling that await (default
    ``wait_for`` behavior) leaves libusb interfaces open so the next open fails
    and inventory may stop seeing the dongle. Prefer waiting with a shield, then
    keep the close running in the background if it is slow.
    """
    return await _soft_await(
        transport.close(),
        timeout_s=MANAGED_USB_TRANSPORT_CLOSE_TIMEOUT_S,
        stage="transport close",
        transport_name=transport_name,
        retain_on_timeout=True,
    )


def _track_background_task(transport_name: str, task: asyncio.Task[Any]) -> None:
    """Retain an in-flight USB task so the next open can wait for the same dongle."""
    bucket = _background_transport_closes.setdefault(transport_name, set())
    bucket.add(task)
    task.add_done_callback(functools.partial(_on_background_transport_close_done, transport_name))


def _on_background_transport_close_done(transport_name: str, task: asyncio.Task[Any]) -> None:
    bucket = _background_transport_closes.get(transport_name)
    if bucket is not None:
        bucket.discard(task)
        if not bucket:
            _background_transport_closes.pop(transport_name, None)
    with suppress(asyncio.CancelledError, Exception):
        exc = task.exception()
        if exc is not None:
            logger.debug("Background managed USB task finished with error: %s", exc)


async def _await_usb_idle(transport_name: str, timeout_s: float = 15.0) -> None:
    """Wait for any background close/release tasks for this transport to finish."""
    pending = list(_background_transport_closes.get(transport_name, ()))
    if not pending:
        return
    logger.info(
        "Waiting for %s background managed USB task(s) on %s (timeout=%.1fs)",
        len(pending),
        transport_name,
        timeout_s,
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=max(timeout_s, 0.1),
        )
    except TimeoutError:
        still_running = sum(1 for t in pending if not t.done())
        logger.warning(
            "Managed USB still busy on %s after %.1fs (%s task(s) running); continuing",
            transport_name,
            timeout_s,
            still_running,
        )


async def _finish_managed_usb_scan(
    *,
    device: Any | None,
    scanning: bool,
    transport: Any,
    transport_name: str,
) -> None:
    """Stop traffic and power the controller down before releasing USB.

    Each scan open/close cycle re-runs Bumble ``power_on`` (HCI reset / driver probe).
    Closing the USB transport without ``power_off`` leaves the dongle mid-stack and
    the next open often hangs while trying to set the controller up again.

    All teardown stages use soft timeouts so a hung power_off/stop never cancels
    the USB release mid-flight.
    """
    if device is not None:
        if scanning:
            await _soft_await(
                device.stop_scanning(legacy=True),
                timeout_s=MANAGED_USB_SCAN_COMMAND_TIMEOUT_S,
                stage="legacy scan stop",
                transport_name=transport_name,
                retain_on_timeout=False,
            )

        if getattr(device, "powered_on", False):
            await _soft_await(
                device.power_off(),
                timeout_s=MANAGED_USB_POWER_OFF_TIMEOUT_S,
                stage="controller power off",
                transport_name=transport_name,
                retain_on_timeout=False,
            )

    closed_cleanly = await _close_managed_usb_transport(transport, transport_name=transport_name)
    settle = MANAGED_USB_POST_CLOSE_SETTLE_S if closed_cleanly else MANAGED_USB_POST_CLOSE_STUCK_SETTLE_S
    await asyncio.sleep(settle)


def _service_data_from_advertisement(advertisement: Any) -> dict[str, bytes]:
    service_data: dict[str, bytes] = {}
    for ad_type in (
        AdvertisingData.Type.SERVICE_DATA_16_BIT_UUID,
        AdvertisingData.Type.SERVICE_DATA_32_BIT_UUID,
        AdvertisingData.Type.SERVICE_DATA_128_BIT_UUID,
    ):
        for uuid, payload in advertisement.data.get_all(ad_type):
            normalized_uuid = _normalize_uuid(str(uuid))
            if normalized_uuid in {SERVICE_GUID, RFSTAR_SERVICE_GUID}:
                service_data[normalized_uuid] = bytes(payload)
    return service_data


def _advertisement_name(advertisement: Any) -> str | None:
    complete_name = advertisement.data.get(AdvertisingData.Type.COMPLETE_LOCAL_NAME)
    if isinstance(complete_name, str) and complete_name.strip() != "":
        return complete_name.strip()
    short_name = advertisement.data.get(AdvertisingData.Type.SHORTENED_LOCAL_NAME)
    if isinstance(short_name, str) and short_name.strip() != "":
        return short_name.strip()
    return None


def _mac_from_bytes(value: bytes) -> str:
    return ":".join(f"{byte:02X}" for byte in value)


def _normalize_mac(value: str) -> str:
    return value.strip().replace("-", ":").upper()


def _normalize_uuid(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("uuid-16:"):
        return f"0000{normalized.removeprefix('uuid-16:')[:4]}-0000-1000-8000-00805f9b34fb"
    if len(normalized) == 4:
        return f"0000{normalized}-0000-1000-8000-00805f9b34fb"
    compact = normalized.replace("-", "")
    if len(compact) == 32:
        reversed_uuid = bytes.fromhex(compact)[::-1].hex()
        for candidate in (SERVICE_GUID, RFSTAR_SERVICE_GUID):
            if reversed_uuid == candidate.replace("-", ""):
                return candidate
    return normalized
