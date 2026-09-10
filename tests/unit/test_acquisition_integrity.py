"""Acquisition regressions using real parsers and callback workers; no hardware."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from synchroni_sensor_sdk.async_api.driver.base import Driver
from synchroni_sensor_sdk.async_api.driver.gforce.convert import sensor_data_to_public
from synchroni_sensor_sdk.async_api.driver.gforce.data_context import DataContext, FeatureMaps
from synchroni_sensor_sdk.async_api.driver.gforce.driver import GForceDriver
from synchroni_sensor_sdk.async_api.driver.gforce.parsing_models import DataType, ParseSample, ParseSensorData
from synchroni_sensor_sdk.async_api.driver.gforce.protocol import RawDataPacket
from synchroni_sensor_sdk.async_api.driver.ingress import BoundedThreadIngress
from synchroni_sensor_sdk.async_api.sensor import Sensor
from synchroni_sensor_sdk.core.data import NtfDataType, Sample, SensorData


def _packet(index: int) -> SensorData:
    return SensorData(
        device_mac="AA:BB",
        data_type=NtfDataType.NTF_EEG,
        sample_rate=250,
        channel_count=1,
        package_sample_count=1,
        package_index_length=2,
        channel_samples=[[Sample(index, index / 8, 0.0, 0.0, index, False, index * 4, 0)]],
        last_package_counter=index,
        last_package_index=index,
        resolution_bits=24,
        channel_mask=1,
        min_package_sample_count=1,
        K=0.125,
        received_monotonic_ns=1_000_000_000 + index * 4_000_000,
    )


def _sensor(*, capacity: int = 2) -> tuple[GForceDriver, Sensor]:
    driver = GForceDriver("AA:BB")
    Driver.__init__(driver, "AA:BB", data_buffer_maxsize=capacity)
    driver._bind_loop()
    driver._streaming = True

    async def start() -> None:
        driver.reset_scientific_delivery()
        driver._streaming = True

    async def stop() -> None:
        driver._streaming = False

    driver.start_streaming = AsyncMock(side_effect=start)
    driver.stop_streaming = AsyncMock(side_effect=stop)
    return driver, Sensor("AA:BB", driver)


async def test_overflow_notifies_pending_then_delivers_every_accepted_packet_before_final_fault() -> None:
    driver, sensor = _sensor()
    entered, release, pending, final = (asyncio.Event() for _ in range(4))
    calls: list[tuple[str, object]] = []

    async def on_data(packet: SensorData) -> None:
        entered.set()
        await release.wait()
        calls.append(("data", packet.delivery_sequence))

    async def on_pending(message: str) -> None:
        calls.append(("pending", message))
        pending.set()

    async def on_error(message: str) -> None:
        calls.append(("final", message))
        final.set()

    await sensor.register_data_callback(on_data)
    await sensor.register_error_callback(on_error)
    await sensor.register_scientific_fault_pending_callback(on_pending)
    try:
        driver.publish_data_on_loop(_packet(0))
        await asyncio.wait_for(entered.wait(), 1)
        loop = asyncio.get_running_loop()
        with patch.object(loop, "create_task", wraps=loop.create_task) as create_task:
            for index in range(1, 1_001):
                driver.publish_data_on_loop(_packet(index))
            assert create_task.call_count == 0
        await asyncio.wait_for(pending.wait(), 1)
        assert driver.scientific_fault_boundary == (0, 3)
        assert driver.pending_data_packets == 2
        assert driver.dropped_data_packets == 1
        assert not final.is_set()
        release.set()
        await asyncio.wait_for(final.wait(), 1)
        assert [kind for kind, _value in calls] == ["pending", "data", "data", "data", "final"]
        assert [value for kind, value in calls if kind == "data"] == [1, 2, 3]
        assert "accepted_sequence=3" in str(calls[-1][1])
        assert "boundary_settled=true|settled_sequence=3|callback_failure_count=0" in str(calls[-1][1])
    finally:
        release.set()
        await sensor._cancel_callback_tasks()


async def test_callback_failure_is_explicit_and_does_not_discard_the_remaining_accepted_tail() -> None:
    driver, sensor = _sensor(capacity=4)
    received: list[int] = []
    messages: list[str] = []
    final = asyncio.Event()

    async def on_data(packet: SensorData) -> None:
        if packet.last_package_counter == 1:
            raise ValueError("consumer rejected packet")
        received.append(packet.last_package_counter)

    async def on_error(message: str) -> None:
        messages.append(message)
        final.set()

    await sensor.register_data_callback(on_data)
    await sensor.register_error_callback(on_error)
    try:
        for index in range(4):
            driver.publish_data_on_loop(_packet(index))
        await asyncio.wait_for(final.wait(), 1)
        assert received == [0, 2, 3]
        assert len(messages) == 1
        assert "stage=data_callback|error=ValueError|packet_counter=1" in messages[0]
        assert "accepted_sequence=4" in messages[0]
        assert "boundary_settled=true|settled_sequence=4|callback_failure_count=1" in messages[0]
        driver.publish_data_on_loop(_packet(4))
        assert driver.pending_data_packets == 0
    finally:
        await sensor._cancel_callback_tasks()


def _imu_context(*, callback_batch: int = 1):
    driver = GForceDriver("test")
    driver._bind_loop()
    protocol = SimpleNamespace(_is_universal_stream=False, stop_streaming=AsyncMock(), drain_raw_ingress=AsyncMock())
    raw = asyncio.Queue(maxsize=4096)
    context = DataContext(
        protocol, "test", raw, publish_data=driver._publish_parsed_data, on_error=driver._publish_error
    )
    context._is_data_transfering = True
    context.featureMap = FeatureMaps.GFD_FEAT_IMU.value
    for slot, kind in [(2, DataType.NTF_ACC), (3, DataType.NTF_GYRO)]:
        parsed = ParseSensorData()
        parsed.dataType = kind
        parsed.sampleRate = 50
        parsed.channelCount = 3
        parsed.channelMask = 7
        parsed.resolutionBits = 16
        parsed.packageSampleCount = 1
        parsed.minPackageSampleCount = callback_batch
        parsed.K = 1
        parsed.clear()
        context.sensorDatas[slot] = parsed
    driver._data_context = context
    driver._streaming = True
    return driver, Sensor("test", driver), context, raw


def _imu_packet(index: int) -> RawDataPacket:
    return RawDataPacket(bytes((0x13, index & 255, index >> 8)) + bytes(12), time.monotonic_ns())


async def test_stop_drains_pending_raw_packets_partial_batches_and_inflight_callback() -> None:
    _driver, sensor, context, raw = _imu_context(callback_batch=2)
    entered, release = asyncio.Event(), asyncio.Event()
    received = []

    async def callback(packet):
        entered.set()
        await release.wait()
        received.append(packet)

    await sensor.register_data_callback(callback)
    parser = asyncio.create_task(context.process_data())
    for index in range(3):
        raw.put_nowait(_imu_packet(index))
    stopping = asyncio.create_task(sensor.stop_streaming())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, timeout=2)
        for kind in (DataType.NTF_ACC, DataType.NTF_GYRO):
            indices = [
                sample.sample_index
                for packet in received
                if packet.data_type.value == kind.value
                for sample in packet.channel_samples[0]
            ]
            assert indices == [0, 1, 2]
    finally:
        release.set()
        parser.cancel()
        await asyncio.gather(parser, stopping, return_exceptions=True)
        await sensor._cancel_callback_tasks()


async def test_notification_mailbox_has_finite_staging_and_one_fault() -> None:
    target = asyncio.Queue(maxsize=2)
    faults = []
    ingress = BoundedThreadIngress(asyncio.get_running_loop(), target, faults.append, capacity=4)
    # No parser/loop turn occurs during this finite callback burst.
    for index in range(1000):
        ingress.publish(index)
    assert ingress.pending_count == 4
    await asyncio.sleep(0)
    assert target.qsize() == 2
    assert [target.get_nowait(), target.get_nowait()] == [0, 1]
    assert ingress.rejected == 998
    assert len(faults) == 1


def test_public_conversion_preserves_fractional_values_and_receipt_time() -> None:
    sample = ParseSample()
    sample.rawData = 7
    sample.data = 0.875
    sample.impedance = 12.625
    sample.saturation = 0.125
    sample.sampleIndex = 42
    sample.timeStampInMs = 168
    packet = ParseSensorData()
    packet.channelSamples = [[sample]]
    packet.receivedMonotonicNs = 1_234_567_890

    result = sensor_data_to_public(packet)

    assert result.channel_samples[0][0] == Sample(7, 0.875, 12.625, 0.125, 42, False, 168, 0)
    assert result.received_monotonic_ns == 1_234_567_890
    assert result.delivery_sequence is None
    assert result.delivery_generation is None
