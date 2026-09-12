"""Acquisition regressions using real parsers and callback workers; no hardware."""

from __future__ import annotations

import asyncio
import struct
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
from synchroni_sensor_sdk.core.device import SetParamCommand


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


async def test_overflow_notifies_pending_then_delivers_every_accepted_packet_before_final_fault() -> None:
    driver, sensor, _context, _raw = _imu_context()
    Driver.__init__(driver, "test", data_buffer_maxsize=2)
    driver._bind_loop()
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
    driver, sensor, context, _raw = _imu_context()
    driver._inited = True
    context.apply_subscription = AsyncMock()
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

        # Explicitly recover, then change settings while an earlier callback is blocked.
        await sensor.stop_streaming()
        await sensor.start_streaming()
        entered, release, stopped = (asyncio.Event() for _ in range(3))

        async def blocked(packet):
            entered.set()
            await release.wait()
            received.append(packet.last_package_counter)

        async def stop():
            stopped.set()

        context.gForce.stop_streaming = stop
        await sensor.register_data_callback(blocked)
        driver.publish_data_on_loop(_packet(4))
        await asyncio.wait_for(entered.wait(), 1)
        driver.publish_data_on_loop(_packet(5))
        changing = asyncio.create_task(sensor.set_param(SetParamCommand(enable_ntf_eeg=False)))
        try:
            await asyncio.wait_for(stopped.wait(), 1)
            await asyncio.sleep(0)
            assert not changing.done()
            release.set()
            await asyncio.wait_for(changing, 1)
            assert sensor.is_streaming()
            assert received == [0, 2, 3, 4, 5]
        finally:
            release.set()
            await asyncio.gather(changing, return_exceptions=True)
    finally:
        await sensor._cancel_callback_tasks()


def _imu_context(*, callback_batch: int = 1):
    driver = GForceDriver("test")
    driver._bind_loop()
    protocol = SimpleNamespace(
        _is_universal_stream=False,
        start_streaming=AsyncMock(),
        stop_streaming=AsyncMock(),
        drain_raw_ingress=AsyncMock(),
    )
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


def _breathe_context() -> tuple[DataContext, list[SensorData], list[str]]:
    published: list[SensorData] = []
    errors: list[str] = []
    context = DataContext(
        SimpleNamespace(_is_universal_stream=False),
        "test",
        asyncio.Queue(),
        publish_data=published.append,
        on_error=errors.append,
    )
    context._is_data_transfering = True
    descriptor = ParseSensorData()
    descriptor.dataType = DataType.NTF_BRTH
    descriptor.sampleRate = 250
    descriptor.channelCount = 1
    descriptor.channelMask = 1
    descriptor.packageSampleCount = 10
    descriptor.minPackageSampleCount = 10
    descriptor.resolutionBits = 24
    descriptor.K = 0.07947
    descriptor.clear()
    context.sensorDatas[4] = descriptor
    return context, published, errors


async def test_breathe_low_mtu_fragments_publish_the_captured_packet() -> None:
    context, published, errors = _breathe_context()
    head = RawDataPacket(bytes.fromhex("ff011501004df45b4df4cd4df4904df4114df44d"), 1_000)
    tail = RawDataPacket(bytes.fromhex("ff004df4bd4df4d24df50b4df5784df5bf"), 2_000)

    parser = asyncio.create_task(context.process_data())
    context._rawDataBuffer.put_nowait(head)
    context._rawDataBuffer.put_nowait(tail)
    try:
        await asyncio.wait_for(context._rawDataBuffer.join(), 1)
        for _ in range(100):
            if published:
                break
            await asyncio.sleep(0.01)

        assert errors == []
        assert len(published) == 1
        packet = published[0]
        assert packet.data_type == DataType.NTF_BRTH
        assert packet.sample_rate == 250
        assert packet.last_package_index == 1
        assert packet.received_monotonic_ns == 2_000
        assert [sample.raw_data for sample in packet.channel_samples[0]] == [
            -3_279_781,
            -3_279_667,
            -3_279_728,
            -3_279_855,
            -3_279_795,
            -3_279_683,
            -3_279_662,
            -3_279_605,
            -3_279_496,
            -3_279_425,
        ]
    finally:
        parser.cancel()
        await asyncio.gather(parser, return_exceptions=True)


async def test_breathe_missing_fragment_faults_without_publishing_corrupt_data() -> None:
    context, published, errors = _breathe_context()
    head = RawDataPacket(bytes.fromhex("ff021501004df45b4df4cd4df4904df4114df44d"), 1_000)
    tail = RawDataPacket(bytes.fromhex("ff004df4bd4df4d24df50b4df5784df5bf"), 2_000)

    assert context._reassemble_standard_data_fragment(head) is None
    assert context._reassemble_standard_data_fragment(tail) is None

    assert published == []
    assert errors == ["SDK_SCIENTIFIC_DELIVERY_FAULT|stage=standard_data_fragment|expected_id=1|received_id=0"]


async def test_breathe_fragment_state_clears_between_streams() -> None:
    context, _published, errors = _breathe_context()
    context.gForce.stop_streaming = AsyncMock()
    context.gForce.start_streaming = AsyncMock()
    head = RawDataPacket(bytes.fromhex("ff011501004df45b4df4cd4df4904df4114df44d"), 1_000)
    tail = RawDataPacket(bytes.fromhex("ff004df4bd4df4d24df50b4df5784df5bf"), 2_000)

    assert context._reassemble_standard_data_fragment(head) is None
    await context.stop_streaming()
    assert errors == ["SDK_SCIENTIFIC_DELIVERY_FAULT|stage=standard_data_fragment|incomplete_on_stop|expected_id=0"]
    await context.start_streaming()
    errors.clear()
    assert context._reassemble_standard_data_fragment(tail) is None
    assert errors == ["SDK_SCIENTIFIC_DELIVERY_FAULT|stage=standard_data_fragment|orphan_tail_id=0"]

    assert context._reassemble_standard_data_fragment(head) is None
    assert context._reassemble_standard_data_fragment(tail) is not None


async def test_stop_drains_pending_raw_packets_partial_batches_and_inflight_callback() -> None:
    driver, sensor, context, raw = _imu_context(callback_batch=2)
    entered, release, stop_started = (asyncio.Event() for _ in range(3))
    received, errors, stopping = [], [], []

    async def callback(packet):
        entered.set()
        await release.wait()
        received.append(packet)

    def publish(packet):
        driver.publish_data_on_loop(packet)
        if not stopping:
            # Stop exactly while the parser's idle reorder flush has yielded.
            stopping.append(asyncio.create_task(sensor.stop_streaming()))
            stop_started.set()

    context._publish_data = publish
    await sensor.register_data_callback(callback)
    await sensor.register_error_callback(errors.append)
    for index in range(5):
        packet = _imu_packet(index)
        raw.put_nowait(RawDataPacket(packet.data, time.monotonic_ns() - 1_000_000_000))
    parser = asyncio.create_task(context.process_data())
    try:
        await asyncio.wait_for(stop_started.wait(), 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert not stopping[0].done()
        release.set()
        await asyncio.wait_for(stopping[0], 2)
        for kind in (DataType.NTF_ACC, DataType.NTF_GYRO):
            assert [
                sample.sample_index
                for packet in received
                if packet.data_type == kind
                for sample in packet.channel_samples[0]
            ] == list(range(5))
        assert errors == []
        assert driver.scientific_fault_boundary is None
        assert context._parse_error_count == 0
    finally:
        release.set()
        parser.cancel()
        await asyncio.gather(parser, *stopping, return_exceptions=True)
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


async def test_public_conversion_preserves_fractional_values_and_receipt_time() -> None:
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

    received = []
    context = DataContext(
        SimpleNamespace(_is_universal_stream=False), "test", asyncio.Queue(), publish_data=received.append
    )
    context.featureMap = FeatureMaps.GFD_FEAT_QUAT.value
    context._is_data_transfering = True
    await context.initGForceQuat(1)
    for index, value in enumerate((1.0, 0.1)):
        packet = RawDataPacket(bytes((5, index)) + struct.pack("<4f", value, 0, 0, 1), 1_000 + index)
        await context._process_ingress_fairly(packet)
        await context._flush_reorder_fairly(force=True)
    assert [packet.last_package_index for packet in received] == [0, 1]
    assert [packet.received_monotonic_ns for packet in received] == [1_000, 1_001]
    assert context._stale_packet_count == 0
