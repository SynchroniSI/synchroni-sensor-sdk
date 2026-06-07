import asyncio
import threading
from types import SimpleNamespace

import pytest

from synchroni_sensor_sdk.async_api.driver.gforce.driver import GForceDriver
from synchroni_sensor_sdk.core.device import DeviceState


@pytest.mark.asyncio
async def test_unexpected_disconnect_runs_teardown_on_driver_loop() -> None:
    driver = GForceDriver("AA:BB:CC:DD:EE:FF")
    loop = asyncio.get_running_loop()
    driver._loop = loop
    driver._state = DeviceState.READY
    driver._inited = True
    driver._streaming = True

    process_ran_on_loop = asyncio.Event()
    process_cancelled = asyncio.Event()

    async def fake_process() -> None:
        process_ran_on_loop.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            process_cancelled.set()
            raise

    driver._process_task = asyncio.create_task(fake_process())
    await process_ran_on_loop.wait()

    done = threading.Event()

    def trigger_disconnect_from_thread() -> None:
        driver._on_disconnect(object())
        loop.call_soon_threadsafe(done.set)

    thread = threading.Thread(target=trigger_disconnect_from_thread)
    thread.start()
    await asyncio.to_thread(done.wait, 5)
    thread.join(timeout=5)

    for _ in range(100):
        if driver.device_state() == DeviceState.DISCONNECTED:
            break
        await asyncio.sleep(0.01)

    assert driver.device_state() == DeviceState.DISCONNECTED
    assert driver._process_task is None
    assert process_cancelled.is_set()
    assert not driver.is_streaming()
    assert not driver.is_inited()


@pytest.mark.asyncio
async def test_disconnect_awaits_background_tasks() -> None:
    driver = GForceDriver("AA:BB:CC:DD:EE:FF")
    driver._loop = asyncio.get_running_loop()
    driver._state = DeviceState.READY

    cancelled = asyncio.Event()

    async def fake_process() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    process_task = asyncio.create_task(fake_process())
    driver._process_task = process_task
    await asyncio.sleep(0)

    async def fake_protocol_disconnect() -> None:
        return None

    driver._protocol = SimpleNamespace(
        client=SimpleNamespace(is_connected=True),
        disconnect=fake_protocol_disconnect,
    )

    await driver.disconnect()

    assert process_task.cancelled()
    assert driver._process_task is None
    assert driver.device_state() == DeviceState.DISCONNECTED
