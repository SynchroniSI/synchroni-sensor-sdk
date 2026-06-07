"""Internal mutable models used while assembling BLE packets.

``DataContext`` was ported from legacy ``sensor/sensor_data_context.py``, which
expects mutable objects with camelCase fields (``deviceMac``, ``channelSamples``,
``rawData``, …), empty constructors, in-place mutation, and ``.clear()`` reuse.
The public v2 types in ``core.data`` and ``core.device`` are snake_case
dataclasses meant for callbacks and user code — a poor fit for that parsing style.

These types are therefore an internal implementation detail. They are converted to
the public API at the driver boundary (see ``gforce.convert.sensor_data_to_public`` and
``gforce.device_info.parse_device_info_to_public``) immediately before data is published.

The ``Sample`` / ``SensorData`` / ``DeviceInfo`` aliases exist only so the ported
parser can keep its original import names; they are **not** the public v2 types.

TODO: Refactor ``gforce.data_context`` to build ``core`` dataclasses directly, drop the
aliases, and remove this module together with ``gforce.convert`` / ``gforce.device_info``.
"""

from synchroni_sensor_sdk.core.data import NtfDataType


class ParseDeviceInfo:
    """Mutable device-capability record filled during init (legacy field layout)."""

    def __init__(self) -> None:
        self.DeviceName: str = ""
        self.ModelName: str = ""
        self.HardwareVersion: str = ""
        self.FirmwareVersion: str = ""
        self.EmgChannelCount: int = 0
        self.EmgSampleRate: int = 0
        self.EegChannelCount: int = 0
        self.EegSampleRate: int = 0
        self.EcgChannelCount: int = 0
        self.EcgSampleRate: int = 0
        self.AccChannelCount: int = 0
        self.AccSampleRate: int = 0
        self.GyroChannelCount: int = 0
        self.GyroSampleRate: int = 0
        self.BrthChannelCount: int = 0
        self.BrthSampleRate: int = 0
        self.MagAngleChannelCount: int = 0
        self.MagAngleSampleRate: int = 0
        self.PpgChannelCount: int = 0
        self.PpgSampleRate: int = 0
        self.Spo2ChannelCount: int = 0
        self.Spo2SampleRate: int = 0
        self.EulerChannelCount: int = 0
        self.EulerSampleRate: int = 0
        self.QuatChannelCount: int = 0
        self.QuatSampleRate: int = 0
        self.GestChannelCount: int = 0
        self.MTUSize: int = 0


DeviceInfo = ParseDeviceInfo


class ParseSample:
    """Single channel sample while parsing; converted to ``core.data.Sample`` at publish time."""

    def __init__(self) -> None:
        self.rawData: int = 0
        self.data: int | float = 0
        self.impedance: int | float = 0
        self.saturation: float = 0.0
        self.sampleIndex: int = 0
        self.isLost: bool = False
        self.timeStampInMs: int = 0
        self.channelIndex: int = 0


class ParseSensorData:
    """In-progress packet assembly; converted to ``core.data.SensorData`` at publish time."""

    def __init__(self) -> None:
        self.deviceMac: str = ""
        self.dataType: NtfDataType = NtfDataType.NTF_EEG
        self.sampleRate: int = 0
        self.channelCount: int = 0
        self.packageSampleCount: int = 0
        self.packageIndexLength: int = 2
        self.channelSamples: list[list[ParseSample]] = []
        self.lastPackageCounter: int = 0
        self.lastPackageIndex: int = 0
        self.lostPackageCount: int = 0
        self.resolutionBits: int = 0
        self.channelMask: int = 0
        self.minPackageSampleCount: int = 0
        self.K: float = 0.0

    def clear(self) -> None:
        self.channelSamples.clear()
        self.lastPackageCounter = -1
        self.lastPackageIndex = 0
        self.lostPackageCount = 0


# Legacy import names used by data_context.py (not the public v2 types).
DataType = NtfDataType
Sample = ParseSample
SensorData = ParseSensorData
