import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from synchroni_sensor_sdk.async_api.driver.gforce import crc_utils


def test_crc_wire_values() -> None:
    for payload, crc8, crc16 in [(b"", 0, 0xFFFF), (b"123456789", 0xF4, 0x374B)]:
        assert crc_utils.calc_crc8(payload) == crc8
        assert crc_utils.crc16_cal(payload, len(payload)) == crc16


async def test_usb_peer_update_retains_interval_and_peer_timeout(monkeypatch) -> None:
    pytest.importorskip("bumble")
    from unittest.mock import AsyncMock

    from synchroni_sensor_sdk.async_api.driver.managed_usb import backend

    monkeypatch.setattr(backend, "_MANAGED_USB_PEER_UPDATE_DELAY_S", 0)
    send = AsyncMock()
    manager = SimpleNamespace(
        on_l2cap_connection_parameter_update_request=Mock(),
        send_control_frame=Mock(),
        host=SimpleNamespace(send_async_command=send),
    )
    connection = SimpleNamespace(
        role=backend.hci.Role.CENTRAL, handle=1, cancel_on_disconnection=lambda operation: operation
    )
    device = SimpleNamespace(l2cap_channel_manager=manager, lookup_connection=lambda _handle: connection)
    tasks = []
    monkeypatch.setattr(
        backend.utils.AsyncRunner, "spawn", lambda operation: tasks.append(asyncio.create_task(operation))
    )
    backend._install_managed_usb_connection_policy(device)
    manager.on_l2cap_connection_parameter_update_request(
        connection, 5, SimpleNamespace(identifier=1, interval_min=12, interval_max=24, latency=0, timeout=400)
    )
    await asyncio.gather(*tasks)
    command = send.call_args.args[0]
    assert (
        command.connection_interval_min,
        command.connection_interval_max,
        command.max_latency,
        command.supervision_timeout,
    ) == (6, 6, 0, 400)
