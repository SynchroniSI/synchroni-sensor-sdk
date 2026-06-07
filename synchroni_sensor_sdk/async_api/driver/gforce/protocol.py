import asyncio
import logging
import platform
import struct
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Any, Protocol, Self, cast, runtime_checkable

import numpy as np
import numpy.typing as npt
from bleak import (
    BleakClient,
    BleakGATTCharacteristic,
    BLEDevice,
)

from synchroni_sensor_sdk.async_api.driver.gforce.crc_utils import BLE_TIMEOUT


@runtime_checkable
class BleGattClient(Protocol):
    """Minimal GATT client surface shared by Bleak and ManagedUsbBleClient."""

    is_connected: bool

    @property
    def mtu_size(self) -> int: ...

    async def connect(self) -> Any: ...

    async def disconnect(self) -> Any: ...

    async def start_notify(self, char_specifier: str, callback: Callable[..., Any]) -> None: ...

    async def stop_notify(self, char_specifier: str) -> None: ...

    async def write_gatt_char(self, char_specifier: str, data: bytes | bytearray, response: bool = False) -> None: ...


def _response_bytes(payload: bytes | None) -> bytes:
    if payload is None:
        raise RuntimeError("Empty protocol response")
    return payload


@dataclass
class Characteristic:
    uuid: str
    service_uuid: str
    descriptor_uuids: list[str]


class Command(IntEnum):
    GET_PROTOCOL_VERSION = (0x00,)
    GET_FEATURE_MAP = (0x01,)
    GET_DEVICE_NAME = (0x02,)
    GET_MODEL_NUMBER = (0x03,)
    GET_SERIAL_NUMBER = (0x04,)
    GET_HW_REVISION = (0x05,)
    GET_FW_REVISION = (0x06,)
    GET_MANUFACTURER_NAME = (0x07,)
    GET_BOOTLOADER_VERSION = (0x0A,)

    GET_BATTERY_LEVEL = (0x08,)
    GET_TEMPERATURE = (0x09,)

    POWEROFF = (0x1D,)
    SWITCH_TO_OAD = (0x1E,)
    SYSTEM_RESET = (0x1F,)
    SWITCH_SERVICE = (0x20,)

    SET_LOG_LEVEL = (0x21,)
    SET_LOG_MODULE = (0x22,)
    PRINT_KERNEL_MSG = (0x23,)
    MOTOR_CONTROL = (0x24,)
    LED_CONTROL_TEST = (0x25,)
    PACKAGE_ID_CONTROL = (0x26,)

    GET_ACCELERATE_CAP = (0x30,)
    SET_ACCELERATE_CONFIG = (0x31,)

    GET_GYROSCOPE_CAP = (0x32,)
    SET_GYROSCOPE_CONFIG = (0x33,)

    GET_MAGNETOMETER_CAP = (0x34,)
    SET_MAGNETOMETER_CONFIG = (0x35,)

    GET_EULER_ANGLE_CAP = (0x36,)
    SET_EULER_ANGLE_CONFIG = (0x37,)

    QUATERNION_CAP = (0x38,)
    QUATERNION_CONFIG = (0x39,)

    GET_ROTATION_MATRIX_CAP = (0x3A,)
    SET_ROTATION_MATRIX_CONFIG = (0x3B,)

    GET_GESTURE_CAP = (0x3C,)
    SET_GESTURE_CONFIG = (0x3D,)

    GET_EMG_RAWDATA_CAP = (0x3E,)
    SET_EMG_RAWDATA_CONFIG = (0x3F,)

    GET_MOUSE_DATA_CAP = (0x40,)
    SET_MOUSE_DATA_CONFIG = (0x41,)

    GET_JOYSTICK_DATA_CAP = (0x42,)
    SET_JOYSTICK_DATA_CONFIG = (0x43,)

    GET_DEVICE_STATUS_CAP = (0x44,)
    SET_DEVICE_STATUS_CONFIG = (0x45,)

    GET_EMG_RAWDATA_CONFIG = (0x46,)

    SET_DATA_NOTIF_SWITCH = (0x4F,)
    SET_FUNCTION_SWITCH = (0x85,)
    CMD_SET_NEUCIR_STATUS = (0x87,)
    CMD_SET_APP_REMOTE_CMD = (0x89,)

    CMD_GET_EEG_CONFIG = (0xA0,)
    CMD_SET_EEG_CONFIG = (0xA1,)
    CMD_GET_ECG_CONFIG = (0xA2,)
    CMD_SET_ECG_CONFIG = (0xA3,)
    CMD_GET_IMPEDANCE_CONFIG = (0xA4,)
    CMD_SET_IMPEDANCE_CONFIG = (0xA5,)
    CMD_GET_EEG_CAP = (0xA6,)
    CMD_GET_ECG_CAP = (0xA7,)
    CMD_GET_IMPEDANCE_CAP = (0xA8,)
    CMD_GET_IMU_CONFIG = (0xAC,)
    CMD_GET_IMU_CAP = (0xAB,)
    CMD_SET_IMU_CONFIG = (0xAD,)
    CMD_GET_BLE_MTU_INFO = (0xAE,)
    CMD_GET_BRT_CONFIG = (0xB3,)
    CMD_GET_PPG_CAP = (0xB5,)
    CMD_GET_PPG_CONFIG = (0xB6,)
    CMD_SET_PPG_CONFIG = (0xB7,)

    CMD_SET_FRIMWARE_FILTER_SWITCH = (0xAA,)
    CMD_GET_FRIMWARE_FILTER_SWITCH = (0xA9,)
    # Partial command packet, format: [CMD_PARTIAL_DATA, packet number in reverse order, packet content]
    MD_PARTIAL_DATA = 0xFF


class DataSubscription(IntEnum):
    # Data Notify All Off
    OFF = (0x00000000,)

    # Accelerate On(C.7)
    ACCELERATE = (0x00000001,)

    # Gyroscope On(C.8)
    GYROSCOPE = (0x00000002,)

    # Magnetometer On(C.9)
    MAGNETOMETER = (0x00000004,)

    # Euler Angle On(C.10)
    EULERANGLE = (0x00000008,)

    # Quaternion On(C.11)
    QUATERNION = (0x00000010,)

    # Rotation Matrix On(C.12)
    ROTATIONMATRIX = (0x00000020,)

    # EMG Gesture On(C.13)
    EMG_GESTURE = (0x00000040,)

    # EMG Raw Data On(C.14)
    EMG_RAW = (0x00000080,)

    # HID Mouse On(C.15)
    HID_MOUSE = (0x00000100,)

    # HID Joystick On(C.16)
    HID_JOYSTICK = (0x00000200,)

    # Device Status On(C.17)
    DEVICE_STATUS = (0x00000400,)

    # Device Log On
    LOG = (0x00000800,)

    DNF_MAG_ANGLE_EXT = (0x00002000,)

    DNF_TYPE_GEST_EXT = (0x00001000,)

    DNF_EEG = (0x00010000,)

    DNF_ECG = (0x00020000,)

    DNF_IMPEDANCE = (0x00040000,)

    DNF_IMU = (0x00080000,)

    DNF_ADS = (0x00100000,)

    DNF_BRTH = (0x00200000,)

    DNF_PPG = (0x00400000,)

    DNF_CONCAT_BLE = (0x80000000,)
    # Data Notify All On
    ALL = 0xFFFFFFFF


class DataType(IntEnum):
    ACC = (0x01,)
    GYO = (0x02,)
    MAG = (0x03,)
    EULER = (0x04,)
    QUAT = (0x05,)
    ROTA = (0x06,)
    EMG_GEST = (0x07,)
    EMG_ADC = (0x08,)
    HID_MOUSE = (0x09,)
    HID_JOYSTICK = (0x0A,)
    DEV_STATUS = (0x0B,)
    LOG = (0x0C,)

    PARTIAL = 0xFF


class SampleResolution(IntEnum):
    BITS_8 = (8,)
    BITS_12 = (12,)
    BITS_16 = (16,)
    BITS_24 = 24


class SamplingRate(IntEnum):
    HZ_250 = (250,)
    HZ_500 = (500,)
    HZ_650 = (650,)


@dataclass
class EmgRawDataConfig:
    fs: SamplingRate = SamplingRate.HZ_500
    channel_mask: int = 0xFF
    batch_len: int = 16
    resolution: SampleResolution = SampleResolution.BITS_8

    def to_bytes(self) -> bytes:
        body = b""
        body += struct.pack("<H", self.fs)
        body += struct.pack("<H", self.channel_mask)
        body += struct.pack("<B", self.batch_len)
        body += struct.pack("<B", self.resolution)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        fs, channel_mask, batch_len, resolution = struct.unpack(
            "<HHBB",
            data,
        )
        return cls(
            SamplingRate(fs),
            channel_mask,
            batch_len,
            SampleResolution(resolution),
        )


@dataclass
class EegRawDataConfig:
    fs: int = 0
    channel_mask: int = 0
    batch_len: int = 0
    resolution: int = 0
    K: float = 0

    def to_bytes(self) -> bytes:
        body = b""
        body += struct.pack("<H", self.fs)
        body += struct.pack("<Q", self.channel_mask)
        body += struct.pack("<B", self.batch_len)
        body += struct.pack("<B", self.resolution)
        body += struct.pack("<d", self.K)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        fs, channel_mask, batch_len, resolution, K = struct.unpack(
            "<HQBBd",
            data,
        )
        return cls(fs, channel_mask, batch_len, resolution, K)


@dataclass
class EegRawDataCap:
    fs: int = 0
    channel_count: int = 0
    batch_len: int = 0
    resolution: int = 0

    def to_bytes(self) -> bytes:
        body = b""
        body += struct.pack("<B", self.fs)
        body += struct.pack("<B", self.channel_count)
        body += struct.pack("<B", self.batch_len)
        body += struct.pack("<B", self.resolution)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        fs, channel_count, batch_len, resolution = struct.unpack(
            "<BBBB",
            data,
        )
        return cls(fs, channel_count, batch_len, resolution)


@dataclass
class EcgRawDataConfig:
    fs: SamplingRate = SamplingRate.HZ_250
    channel_mask: int = 0
    batch_len: int = 16
    resolution: SampleResolution = SampleResolution.BITS_24
    K: float = 0

    def to_bytes(self) -> bytes:
        body = b""
        body += struct.pack("<H", self.fs)
        body += struct.pack("<H", self.channel_mask)
        body += struct.pack("<B", self.batch_len)
        body += struct.pack("<B", self.resolution)
        body += struct.pack("<d", self.K)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        fs, channel_mask, batch_len, resolution, K = struct.unpack(
            "<HHBBd",
            data,
        )
        return cls(
            SamplingRate(fs),
            channel_mask,
            batch_len,
            SampleResolution(resolution),
            K,
        )


@dataclass
class ImuRawDataConfig:
    channel_count: int = 0
    fs: int = 0
    batch_len: int = 0
    accK: float = 0
    gyroK: float = 0

    def to_bytes(self) -> bytes:
        body = b""
        body += struct.pack("<i", self.channel_count)
        body += struct.pack("<H", self.fs)
        body += struct.pack("<B", self.batch_len)
        body += struct.pack("<d", self.accK)
        body += struct.pack("<d", self.gyroK)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        channel_count, fs, batch_len, accK, gyroK = struct.unpack(
            "<iHBdd",
            data,
        )
        return cls(channel_count, fs, batch_len, accK, gyroK)


@dataclass
class PpgRawDataConfig:
    mode: int = 0
    period: int = 0
    fs: int = 0
    batch_len: int = 0
    reserved: list[int] | None = None

    def __post_init__(self) -> None:
        if self.reserved is None:
            self.reserved = [0] * 5

    def to_bytes(self) -> bytes:
        reserved = self.reserved or [0] * 5
        body = b""
        body += struct.pack("<B", self.mode)
        body += struct.pack("<H", self.period)
        body += struct.pack("<H", self.fs)
        body += struct.pack("<B", self.batch_len)
        for i in range(5):
            body += struct.pack("<B", reserved[i] if i < len(reserved) else 0)
        body += struct.pack("<B", 0)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        if len(data) < 12:
            raise ValueError(f"PPG config data too short: {len(data)} bytes, expected 12")
        mode = data[0]
        period = struct.unpack("<H", data[1:3])[0]
        fs = struct.unpack("<H", data[3:5])[0]
        batch_len = data[5]
        reserved = list(data[6:11])
        return cls(mode, period, fs, batch_len, reserved)


@dataclass
class BrthRawDataConfig:
    fs: int = 0
    channel_mask: int = 0
    batch_len: int = 0
    resolution: int = 0
    K: float = 0

    def to_bytes(self) -> bytes:
        body = b""
        body += struct.pack("<H", self.fs)
        body += struct.pack("<H", self.channel_mask)
        body += struct.pack("<B", self.batch_len)
        body += struct.pack("<B", self.resolution)
        body += struct.pack("<d", self.K)
        return body

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        fs, channel_mask, batch_len, resolution, K = struct.unpack(
            "<HHBBd",
            data,
        )
        return cls(fs, channel_mask, batch_len, resolution, K)


@dataclass
class Request:
    cmd: Command
    has_res: bool
    body: bytes | None = None


class ResponseCode(IntEnum):
    SUCCESS = (0x00,)
    NOT_SUPPORT = (0x01,)
    BAD_PARAM = (0x02,)
    FAILED = (0x03,)
    TIMEOUT = (0x04,)
    PARTIAL_PACKET = 0xFF


@dataclass
class Response:
    code: ResponseCode
    cmd: Command
    data: bytes


class GForceProtocol:
    def __init__(
        self,
        device: BLEDevice,
        cmd_char: str,
        data_char: str,
        is_universal_stream: bool,
        loop: asyncio.AbstractEventLoop,
        *,
        managed_usb_transport: str | None = None,
        managed_usb_peer_address: str | None = None,
    ) -> None:
        """Build a GForce ATT protocol over OS Bleak or managed USB HCI.

        When both ``managed_usb_transport`` and ``managed_usb_peer_address`` are
        set, connect uses Bumble/libusb instead of :class:`bleak.BleakClient`.
        """
        self._logger = logging.getLogger(__name__)
        self.device_name: str = ""
        self.client: BleGattClient | None = None
        self._loop: asyncio.AbstractEventLoop = loop
        self.cmd_char: str = cmd_char
        self.data_char: str = data_char
        self.responses: dict[Command, asyncio.Queue[bytes | float] | None] = {}
        self.resolution: SampleResolution = SampleResolution.BITS_8
        self._num_channels: int = 8
        self._device: BLEDevice = device
        self._is_universal_stream: bool = is_universal_stream
        self._raw_data_buf: asyncio.Queue[bytes] | None = None
        self.packet_id: int = 0
        self.data_packet: list = []
        self._managed_usb_transport = managed_usb_transport
        self._managed_usb_peer_address = managed_usb_peer_address

    def _schedule_cmd_response(self, bs: bytes) -> None:
        asyncio.create_task(self.async_on_cmd_response(bs))

    def _require_client(self) -> BleGattClient:
        if self.client is None:
            raise RuntimeError("BLE client is not connected")
        return self.client

    def _require_raw_data_buf(self) -> asyncio.Queue[bytes]:
        if self._raw_data_buf is None:
            raise RuntimeError("Raw data buffer is not configured")
        return self._raw_data_buf

    async def connect(
        self,
        disconnect_cb: Callable[..., None],
        buf: asyncio.Queue[bytes],
    ) -> None:
        if platform.system() == "Darwin":
            loop = asyncio.get_running_loop()
            asyncio.set_event_loop(loop)

        self.device_name = self._device.name or ""
        self._raw_data_buf = buf

        if self._managed_usb_transport and self._managed_usb_peer_address:
            await self._connect_managed_usb(disconnect_cb)
        else:
            await self._connect_bleak(disconnect_cb)

    async def _connect_bleak(self, disconnect_cb: Callable[..., None]) -> None:
        client = cast(BleGattClient, BleakClient(self._device, disconnected_callback=disconnect_cb))
        self.client = client

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await asyncio.wait_for(client.connect(), timeout=BLE_TIMEOUT)
                await asyncio.sleep(0.5)

                if client.is_connected:
                    break
            except Exception as e:
                self._logger.error("Connection attempt %s failed: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0)
                else:
                    self._logger.error("All connection attempts failed")
                    return

        if not client.is_connected:
            return

        try:
            if not self._is_universal_stream:
                await asyncio.wait_for(client.start_notify(self.cmd_char, self._on_cmd_response), timeout=BLE_TIMEOUT)
            else:
                await asyncio.wait_for(
                    client.start_notify(self.data_char, self._on_universal_response), timeout=BLE_TIMEOUT
                )

        except Exception as e:
            self._logger.error("Failed to start notifications: %s", e)
            await client.disconnect()
            return

    async def _connect_managed_usb(self, disconnect_cb: Callable[..., None]) -> None:
        from synchroni_sensor_sdk.async_api.driver.managed_usb.backend import ManagedUsbBleClient

        assert self._managed_usb_transport is not None
        assert self._managed_usb_peer_address is not None
        max_retries = 3
        last_error: Exception | None = None
        # Peer connect + shared radio power-on can exceed BLE_TIMEOUT; do not cancel mid-open
        # (cancelling strands libusb). Use a long wait_for instead of silent BLE_TIMEOUT returns.
        managed_usb_connect_timeout_s = 45.0
        client: ManagedUsbBleClient | None = None
        for attempt in range(max_retries):
            client = ManagedUsbBleClient(
                transport_name=self._managed_usb_transport,
                peer_address=self._managed_usb_peer_address,
                disconnected_callback=disconnect_cb,
            )
            self.client = client
            try:
                await asyncio.wait_for(client.connect(), timeout=managed_usb_connect_timeout_s)
                if client.is_connected:
                    break
                last_error = RuntimeError("Managed USB client did not connect")
                await client.disconnect()
            except Exception as e:
                last_error = e
                self._logger.error("Managed USB connection attempt %s failed: %s", attempt + 1, e)
                with suppress(Exception):
                    await client.disconnect()
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0)
        if client is None or not client.is_connected:
            raise RuntimeError(f"All managed USB connection attempts failed: {last_error}")
        try:
            if not self._is_universal_stream:
                await asyncio.wait_for(client.start_notify(self.cmd_char, self._on_cmd_response), timeout=BLE_TIMEOUT)
            else:
                await asyncio.wait_for(
                    client.start_notify(self.data_char, self._on_universal_response), timeout=BLE_TIMEOUT
                )
        except Exception as e:
            self._logger.error("Failed to start managed USB notifications: %s", e)
            with suppress(Exception):
                await client.disconnect()
            raise

    def _on_data_response(self, q: asyncio.Queue[bytes], bs: bytearray) -> None:
        # bs = bytes(bs)

        # full_packet = []

        # is_partial_data = bs[0] == ResponseCode.PARTIAL_PACKET
        # if is_partial_data:
        #     packet_id = bs[1]
        #     if self.packet_id != 0 and self.packet_id != packet_id + 1:
        #         raise Exception(
        #             "Unexpected packet id: expected {} got {}".format(
        #                 self.packet_id + 1,
        #                 packet_id,
        #             )
        #         )
        #     elif self.packet_id == 0 or self.packet_id > packet_id:
        #         self.packet_id = packet_id
        #         self.data_packet += bs[2:]

        #         if self.packet_id == 0:
        #             full_packet = self.data_packet
        #             self.data_packet = []
        # else:
        #     full_packet = bs

        full_packet = bs
        if len(full_packet) == 0:
            return

        self._loop.call_soon_threadsafe(q.put_nowait, bytes(full_packet))

    @staticmethod
    def _convert_acceleration_to_g(data: bytes) -> npt.NDArray[np.floating[Any]]:
        normalizing_factor = 65536.0

        acceleration_data = np.frombuffer(data, dtype=np.int32).astype(np.float32) / normalizing_factor
        num_channels = 3

        return acceleration_data.reshape(-1, num_channels)

    @staticmethod
    def _convert_gyro_to_dps(data: bytes) -> npt.NDArray[np.floating[Any]]:
        normalizing_factor = 65536.0

        gyro_data = np.frombuffer(data, dtype=np.int32).astype(np.float32) / normalizing_factor
        num_channels = 3

        return gyro_data.reshape(-1, num_channels)

    @staticmethod
    def _convert_magnetometer_to_ut(data: bytes) -> npt.NDArray[np.floating[Any]]:
        normalizing_factor = 65536.0

        magnetometer_data = np.frombuffer(data, dtype=np.int32).astype(np.float32) / normalizing_factor
        num_channels = 3

        return magnetometer_data.reshape(-1, num_channels)

    @staticmethod
    def _convert_euler(data: bytes) -> npt.NDArray[np.floating[Any]]:
        euler_data = np.frombuffer(data, dtype=np.float32).astype(np.float32)
        num_channels = 3

        return euler_data.reshape(-1, num_channels)

    @staticmethod
    def _convert_quaternion(data: bytes) -> npt.NDArray[np.floating[Any]]:
        quaternion_data = np.frombuffer(data, dtype=np.float32).astype(np.float32)
        num_channels = 4

        return quaternion_data.reshape(-1, num_channels)

    @staticmethod
    def _convert_rotation_matrix(data: bytes) -> npt.NDArray[np.floating[Any]]:
        rotation_matrix_data = np.frombuffer(data, dtype=np.int32).astype(np.float32)
        num_channels = 9

        return rotation_matrix_data.reshape(-1, num_channels)

    @staticmethod
    def _convert_emg_gesture(data: bytes) -> npt.NDArray[np.floating[Any]]:
        emg_gesture_data = np.frombuffer(data, dtype=np.int16).astype(np.float16)
        num_channels = 6

        return emg_gesture_data.reshape(-1, num_channels)

    def _on_universal_response(self, _: BleakGATTCharacteristic, bs: bytearray) -> None:
        buf = self._require_raw_data_buf()
        self._loop.call_soon_threadsafe(buf.put_nowait, bytes(bs))

    def _on_cmd_response(self, _: BleakGATTCharacteristic, bs: bytearray) -> None:
        self._loop.call_soon_threadsafe(self._schedule_cmd_response, bytes(bs))

    async def async_on_cmd_response(self, bs: bytes | bytearray) -> None:
        try:
            # print(bytes(bs))
            response = self._parse_response(bytes(bs))
            queue = self.responses.get(response.cmd)
            if queue is not None:
                queue.put_nowait(
                    response.data,
                )
            else:
                self._logger.warning("Invalid response: %s", bytes(bs))
        except Exception as e:
            raise Exception(f"Failed to parse response: {e}") from e

    @staticmethod
    def _parse_response(res: bytes) -> Response:
        code = int.from_bytes(res[:1], byteorder="big")
        code = ResponseCode(code)

        cmd = int.from_bytes(res[1:2], byteorder="big")
        cmd = Command(cmd)

        data = res[2:]

        return Response(
            code=code,
            cmd=cmd,
            data=data,
        )

    async def get_protocol_version(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_PROTOCOL_VERSION,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_feature_map(self) -> int:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_FEATURE_MAP,
                    has_res=True,
                )
            )
        )
        return int.from_bytes(buf, byteorder="little")  # TODO: check if this is correct

    async def get_device_name(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_DEVICE_NAME,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_firmware_revision(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_FW_REVISION,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_hardware_revision(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_HW_REVISION,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_model_number(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_MODEL_NUMBER,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_serial_number(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_SERIAL_NUMBER,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_manufacturer_name(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_MANUFACTURER_NAME,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_bootloader_version(self) -> str:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_BOOTLOADER_VERSION,
                    has_res=True,
                )
            )
        )
        return buf.decode("utf-8")

    async def get_battery_level(self) -> int:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_BATTERY_LEVEL,
                    has_res=True,
                )
            )
        )
        return int.from_bytes(buf, byteorder="big")

    async def get_temperature(self) -> int:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_TEMPERATURE,
                    has_res=True,
                )
            )
        )
        return int.from_bytes(buf, byteorder="big")

    async def power_off(self) -> None:
        await self._send_request(
            Request(
                cmd=Command.POWEROFF,
                has_res=False,
            )
        )

    async def system_reset(self) -> None:
        await self._send_request(
            Request(
                cmd=Command.SYSTEM_RESET,
                has_res=False,
            )
        )

    async def set_motor(self, switchStatus: int) -> None:
        await self._send_request(
            Request(
                cmd=Command.MOTOR_CONTROL,
                body=bytes([switchStatus]),
                has_res=True,
            )
        )

    async def set_led(self, switchStatus: int) -> None:
        await self._send_request(
            Request(
                cmd=Command.LED_CONTROL_TEST,
                body=bytes([switchStatus]),
                has_res=True,
            )
        )

    async def set_package_id(self, switchStatus: int) -> None:
        await self._send_request(
            Request(
                cmd=Command.PACKAGE_ID_CONTROL,
                body=bytes([switchStatus]),
                has_res=True,
            )
        )

    async def set_log_level(self, logLevel: int) -> None:
        await self._send_request(
            Request(
                cmd=Command.SET_LOG_LEVEL,
                body=bytes([0xFF & logLevel]),
                has_res=True,
            )
        )

    async def set_function_switch(self, funcSwitch: int) -> bool:
        ret = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.SET_FUNCTION_SWITCH,
                    body=bytes([0xFF & funcSwitch]),
                    has_res=True,
                )
            )
        )
        return bool(len(ret) > 0 and ret[0] == 0)

    async def set_neucir_app_control(self, open: bool, close: bool, stop: bool) -> bool:
        if stop:
            body = bytes([4])
        elif open:
            body = bytes([6])
        elif close:
            body = bytes([5])
        else:
            body = bytes([0])

        ret = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_SET_APP_REMOTE_CMD,
                    body=body,
                    has_res=True,
                )
            )
        )
        return bool(len(ret) > 0 and ret[0] == 0)

    async def set_neucir_mode(self, _mode: int) -> bool:
        ret = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_SET_NEUCIR_STATUS,
                    body=bytes([0x90]),
                    has_res=True,
                )
            )
        )
        return bool(len(ret) > 0 and ret[0] == 0)

    async def set_firmware_filter_switch(self, switchStatus: int) -> None:
        await self._send_request(
            Request(
                cmd=Command.CMD_SET_FRIMWARE_FILTER_SWITCH,
                body=bytes([0xFF & switchStatus]),
                has_res=True,
            )
        )

    async def get_firmware_filter_switch(self) -> int:
        buf = _response_bytes(
            await self._send_request(Request(cmd=Command.CMD_GET_FRIMWARE_FILTER_SWITCH, has_res=True))
        )
        return buf[0]

    async def set_emg_raw_data_config(self, cfg: EmgRawDataConfig = EmgRawDataConfig()) -> None:
        body = cfg.to_bytes()
        await self._send_request(
            Request(
                cmd=Command.SET_EMG_RAWDATA_CONFIG,
                body=body,
                has_res=True,
            )
        )

        # print('set_emg_raw_data_config returned:', ret)

        self.resolution = cfg.resolution

        num_channels = 0
        ch_mask = cfg.channel_mask

        while ch_mask != 0:
            if ch_mask & 0x01 != 0:
                num_channels += 1
            ch_mask >>= 1

        self._num_channels = num_channels

    async def get_emg_raw_data_config(self) -> EmgRawDataConfig:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.GET_EMG_RAWDATA_CONFIG,
                    has_res=True,
                )
            )
        )
        return EmgRawDataConfig.from_bytes(buf)

    async def get_eeg_raw_data_config(self) -> EegRawDataConfig:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_EEG_CONFIG,
                    has_res=True,
                )
            )
        )
        return EegRawDataConfig.from_bytes(buf)

    async def get_eeg_raw_data_cap(self) -> EegRawDataCap:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_EEG_CAP,
                    has_res=True,
                )
            )
        )
        return EegRawDataCap.from_bytes(buf)

    async def get_ecg_raw_data_config(self) -> EcgRawDataConfig:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_ECG_CONFIG,
                    has_res=True,
                )
            )
        )
        return EcgRawDataConfig.from_bytes(buf)

    async def get_imu_raw_data_config(self) -> ImuRawDataConfig:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_IMU_CONFIG,
                    has_res=True,
                )
            )
        )
        return ImuRawDataConfig.from_bytes(buf)

    async def set_imu_raw_data_config(self, cfg: ImuRawDataConfig) -> bool:
        ret = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_SET_IMU_CONFIG,
                    body=cfg.to_bytes(),
                    has_res=True,
                )
            )
        )
        return bool(len(ret) > 0 and ret[0] == 0)

    async def get_imu_cap_data_config(self) -> tuple[int, int, int] | None:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_IMU_CAP,
                    has_res=True,
                )
            )
        )
        if buf is None or len(buf) < 7:
            return None
        channel_mask = struct.unpack("<I", buf[0:4])[0]
        samp_rate = struct.unpack("<H", buf[4:6])[0]
        sample_count = struct.unpack("<B", buf[6:7])[0]
        return (channel_mask, samp_rate, sample_count)

    async def get_ppg_raw_data_config(self) -> PpgRawDataConfig:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_PPG_CONFIG,
                    has_res=True,
                )
            )
        )
        return PpgRawDataConfig.from_bytes(buf)

    async def set_ppg_raw_data_config(self, cfg: PpgRawDataConfig) -> bool:
        ret = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_SET_PPG_CONFIG,
                    body=cfg.to_bytes(),
                    has_res=True,
                )
            )
        )
        return bool(len(ret) > 0 and ret[0] == 0)

    async def get_brth_raw_data_config(self) -> BrthRawDataConfig:
        buf = _response_bytes(
            await self._send_request(
                Request(
                    cmd=Command.CMD_GET_BRT_CONFIG,
                    has_res=True,
                )
            )
        )
        return BrthRawDataConfig.from_bytes(buf)

    async def set_subscription(self, subscription: int | DataSubscription) -> None:
        value = int(subscription)
        await self._send_request(
            Request(
                cmd=Command.SET_DATA_NOTIF_SWITCH,
                body=bytes(
                    [
                        0xFF & value,
                        0xFF & (value >> 8),
                        0xFF & (value >> 16),
                        0xFF & (value >> 24),
                    ]
                ),
                has_res=True,
            )
        )

    async def start_streaming(self, q: asyncio.Queue[bytes]) -> None:
        client = self._require_client()
        await asyncio.wait_for(
            client.start_notify(
                self.data_char,
                lambda _, data: self._on_data_response(q, data),
            ),
            timeout=BLE_TIMEOUT,
        )

    async def stop_streaming(self) -> None:
        try:
            await asyncio.wait_for(self._require_client().stop_notify(self.data_char), timeout=BLE_TIMEOUT)
        except Exception as e:
            self._logger.warning("Failed to stop streaming: %s", e)

    async def disconnect(self) -> None:
        with suppress(asyncio.CancelledError):
            try:
                if self.client:
                    await asyncio.wait_for(self.client.disconnect(), timeout=BLE_TIMEOUT)
            except Exception as e:
                self._logger.warning("Disconnect error: %s", e)

    def _get_response_channel(self, cmd: Command) -> asyncio.Queue[bytes | float]:
        existing = self.responses.get(cmd)
        if existing is not None:
            return existing
        q: asyncio.Queue[bytes | float] = asyncio.Queue()
        self.responses[cmd] = q
        return q

    def _clear_response_channel(self, cmd: Command) -> None:
        self.responses.pop(cmd, None)

    async def _send_request(self, req: Request) -> bytes | None:
        return await self._send_request_internal(req=req)

    async def _send_request_internal(self, req: Request) -> bytes | None:
        q: asyncio.Queue[bytes | float] | None = None
        if req.has_res:
            q = self._get_response_channel(req.cmd)

        time_stamp_old = -1.0
        if q is not None:
            while not q.empty():
                item = q.get_nowait()
                if isinstance(item, float):
                    time_stamp_old = item

        now = datetime.now()
        timestamp_now = now.timestamp()
        if (time_stamp_old > -1) and ((timestamp_now - time_stamp_old) < 3):
            self._logger.warning("Send request too fast")
            if q is not None:
                q.put_nowait(time_stamp_old)
            return None

        bs = bytes([req.cmd])
        if req.body is not None:
            bs += req.body

        # print(str(req.cmd) + str(req.body))
        try:
            await asyncio.wait_for(self._require_client().write_gatt_char(self.cmd_char, bs), timeout=1)
        except Exception:
            self._clear_response_channel(req.cmd)
            return None

        if not req.has_res:
            self._clear_response_channel(req.cmd)
            return None

        assert q is not None
        try:
            ret = await asyncio.wait_for(q.get(), 2)
            now = datetime.now()
            timestamp_now = now.timestamp()
            q.put_nowait(timestamp_now)
            if isinstance(ret, bytes):
                return ret
            return None
        except Exception:
            self._clear_response_channel(req.cmd)
            return None
