import asyncio
import threading

import pytest

from synchroni_sensor_sdk.async_api.driver.buffer import DropOldestBuffer
from synchroni_sensor_sdk.async_api.driver.gforce import GForceDriver


@pytest.mark.asyncio
async def test_drops_oldest_when_full() -> None:
    buffer: DropOldestBuffer[int] = DropOldestBuffer(maxsize=2)

    await buffer.publish(1)
    await buffer.publish(2)
    await buffer.publish(3)

    assert buffer.dropped == 1
    assert buffer.qsize == 2

    consumer = buffer.consume()
    assert await anext(consumer) == 2
    assert await anext(consumer) == 3


@pytest.mark.asyncio
async def test_publish_on_loop_drops_oldest() -> None:
    buffer: DropOldestBuffer[int] = DropOldestBuffer(maxsize=2)

    buffer.publish_on_loop(1)
    buffer.publish_on_loop(2)
    buffer.publish_on_loop(3)

    assert buffer.dropped == 1

    consumer = buffer.consume()
    assert await anext(consumer) == 2
    assert await anext(consumer) == 3


@pytest.mark.asyncio
async def test_close_stops_consumer_after_drain() -> None:
    buffer: DropOldestBuffer[int] = DropOldestBuffer(maxsize=4)
    await buffer.publish(1)

    consumer = buffer.consume()
    assert await anext(consumer) == 1

    await buffer.close()
    with pytest.raises(StopAsyncIteration):
        await anext(consumer)


@pytest.mark.asyncio
async def test_call_soon_threadsafe_publish() -> None:
    buffer: DropOldestBuffer[int] = DropOldestBuffer(maxsize=4)
    loop = asyncio.get_running_loop()
    ready = threading.Event()

    def publish_from_thread() -> None:
        ready.wait()
        loop.call_soon_threadsafe(buffer.publish_on_loop, 42)

    thread = threading.Thread(target=publish_from_thread)
    thread.start()
    ready.set()
    thread.join()

    consumer = buffer.consume()
    assert await anext(consumer) == 42


@pytest.mark.asyncio
async def test_driver_schedule_publish_data_from_thread() -> None:
    driver = GForceDriver("AA:BB:CC:DD:EE:FF")
    driver._loop = asyncio.get_running_loop()

    from synchroni_sensor_sdk.core.data import NtfDataType, SensorData

    packet = SensorData(
        device_mac=driver.address,
        data_type=NtfDataType.NTF_EEG,
        sample_rate=250,
        channel_count=1,
        package_sample_count=1,
        package_index_length=1,
        channel_samples=[],
        last_package_counter=0,
        last_package_index=0,
        resolution_bits=24,
        channel_mask=1,
        min_package_sample_count=1,
        K=1.0,
    )
    ready = threading.Event()

    def publish_from_thread() -> None:
        ready.wait()
        driver.schedule_publish_data(packet)

    thread = threading.Thread(target=publish_from_thread)
    thread.start()
    ready.set()
    thread.join()

    async for data in driver.data_notifications():
        assert data is packet
        break


@pytest.mark.asyncio
async def test_publish_after_close_is_ignored() -> None:
    buffer: DropOldestBuffer[int] = DropOldestBuffer(maxsize=4)
    await buffer.close()

    await buffer.publish(1)
    buffer.publish_on_loop(2)

    assert buffer.qsize == 0
