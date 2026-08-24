"""Pending command-response cleanup and command cool-down pacing."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak import BLEDevice

from synchroni_sensor_sdk.async_api.driver.gforce.protocol import (
    COMMAND_MIN_INTERVAL_S,
    FILTER_CMD_COOLDOWN_S,
    SAME_OPCODE_COOLDOWN_S,
    Command,
    CommandResponseError,
    GForceProtocol,
    Request,
    ResponseCode,
)


def _protocol() -> GForceProtocol:
    device = MagicMock(spec=BLEDevice)
    device.name = "test"
    return GForceProtocol(
        device,
        "cmd",
        "data",
        is_universal_stream=False,
        loop=asyncio.get_running_loop(),
    )


@pytest.mark.asyncio
async def test_clear_pending_responses_unblocks_waiter() -> None:
    protocol = _protocol()
    q = protocol._get_response_channel(Command.GET_BATTERY_LEVEL)
    waiter = asyncio.create_task(q.get())
    await asyncio.sleep(0)

    protocol.clear_pending_responses()
    assert protocol.responses == {}
    result = await asyncio.wait_for(waiter, timeout=1.0)
    assert result is None


@pytest.mark.asyncio
async def test_fragmented_command_response_and_rejection_are_preserved() -> None:
    protocol = _protocol()
    response = protocol._get_response_channel(Command.GET_FW_REVISION)
    await protocol.async_on_cmd_response(b"\xff\x01\x00\x06V1.0-")
    await protocol.async_on_cmd_response(b"\xff\x00test")
    assert await response.get() == b"V1.0-test"

    rejection = protocol._get_response_channel(Command.SET_EMG_RAWDATA_CONFIG)
    await protocol.async_on_cmd_response(bytes((ResponseCode.BAD_PARAM, Command.SET_EMG_RAWDATA_CONFIG)))
    assert isinstance(await rejection.get(), CommandResponseError)


@pytest.mark.asyncio
async def test_clear_response_channel_on_write_timeout() -> None:
    protocol = _protocol()
    protocol.client = SimpleNamespace(
        write_gatt_char=AsyncMock(side_effect=TimeoutError("write timed out")),
    )

    result = await protocol._send_request(
        Request(cmd=Command.GET_BATTERY_LEVEL, has_res=True),
    )
    assert result is None
    assert Command.GET_BATTERY_LEVEL not in protocol.responses


@pytest.mark.asyncio
async def test_driver_init_clears_pending_responses() -> None:
    from synchroni_sensor_sdk.async_api.driver.gforce.driver import GForceDriver

    driver = GForceDriver("AA:BB:CC:DD:EE:FF")
    protocol = _protocol()
    protocol._get_response_channel(Command.GET_FEATURE_MAP)
    assert protocol.responses

    async def fake_init(_package_count: int) -> bool:
        return True

    driver._protocol = protocol
    driver._data_context = SimpleNamespace(init=fake_init)  # type: ignore[assignment]
    driver._loop = asyncio.get_running_loop()

    async def fake_battery() -> int:
        return 50

    protocol.get_battery_level = fake_battery  # type: ignore[method-assign]

    await driver.init(package_sample_count=10, power_refresh_interval=60_000)
    assert protocol.responses == {}
    if driver._power_task is not None:
        driver._power_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await driver._power_task


@pytest.mark.asyncio
async def test_command_min_interval_waits_between_sends() -> None:
    protocol = _protocol()
    writes: list[float] = []

    async def write_gatt(_char: str, _data: bytes, response: bool | None = None) -> None:
        del response
        writes.append(time.monotonic())

    protocol.client = SimpleNamespace(write_gatt_char=write_gatt)
    protocol._record_command_complete(Command.GET_BATTERY_LEVEL)
    protocol._last_any_cmd_end_mono = time.monotonic()

    t0 = time.monotonic()
    await protocol._send_request(Request(cmd=Command.GET_FEATURE_MAP, has_res=False))
    elapsed = time.monotonic() - t0
    assert elapsed >= COMMAND_MIN_INTERVAL_S * 0.9
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_same_opcode_cooldown_waits() -> None:
    protocol = _protocol()
    protocol.client = SimpleNamespace(write_gatt_char=AsyncMock())
    protocol._last_cmd_end_mono[Command.GET_BATTERY_LEVEL] = time.monotonic()
    protocol._last_any_cmd_end_mono = 0.0  # don't add the 75ms gap on top for this check

    # Shorten cool-down for the unit test without changing production default semantics
    # (patch module constant isn't needed if we set last_end to almost-now minus a small epsilon).
    # Force ~50ms remaining of SAME_OPCODE cool-down:
    protocol._last_cmd_end_mono[Command.GET_BATTERY_LEVEL] = time.monotonic() - (SAME_OPCODE_COOLDOWN_S - 0.05)
    t0 = time.monotonic()
    await protocol._send_request(Request(cmd=Command.GET_BATTERY_LEVEL, has_res=False))
    assert time.monotonic() - t0 >= 0.04


@pytest.mark.asyncio
async def test_filter_cmd_cooldown_between_filter_family() -> None:
    protocol = _protocol()
    protocol.client = SimpleNamespace(write_gatt_char=AsyncMock())
    protocol._last_filter_cmd_end_mono = time.monotonic() - (FILTER_CMD_COOLDOWN_S - 0.05)
    protocol._last_any_cmd_end_mono = 0.0
    protocol._last_cmd_end_mono.clear()

    t0 = time.monotonic()
    await protocol._send_request(
        Request(cmd=Command.CMD_GET_FRIMWARE_FILTER_SWITCH, has_res=False),
    )
    assert time.monotonic() - t0 >= 0.04


@pytest.mark.asyncio
async def test_serial_lock_makes_commands_non_overlapping() -> None:
    protocol = _protocol()
    active = 0
    peaks = 0

    async def write_gatt(_char: str, _data: bytes, response: bool | None = None) -> None:
        nonlocal active, peaks
        del response
        active += 1
        peaks = max(peaks, active)
        await asyncio.sleep(0.03)
        active -= 1

    protocol.client = SimpleNamespace(write_gatt_char=write_gatt)
    protocol._reset_command_pacing()

    await asyncio.gather(
        protocol._send_request(Request(cmd=Command.GET_BATTERY_LEVEL, has_res=False)),
        protocol._send_request(Request(cmd=Command.GET_FEATURE_MAP, has_res=False)),
    )
    assert peaks == 1
