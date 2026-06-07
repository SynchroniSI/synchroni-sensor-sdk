from dataclasses import dataclass
from enum import IntEnum


@dataclass
class Sample:
    """
    Sample represents a single sample in a SensorData packet.
    """

    raw_data: int
    data: int
    impedance: int
    saturation: float
    sample_index: int
    is_lost: bool
    timestamp_ms: int
    channel_index: int


class NtfDataType(IntEnum):
    """
    NtfDataType represents the type of data in a NtfData packet.
    Hex values match the firmware / legacy ``DataType`` enum.
    """

    NTF_ACC = 0x1  # Accelerometer
    NTF_GYRO = 0x2  # Gyroscope
    NTF_EULER_DATA = 0x4  # Euler angles
    NTF_QUATERNION = 0x5  # Quaternion
    NTF_GEST = 0x07  # Gesture
    NTF_EMG = 0x8  # EMG (electromyography)
    NTF_MAG_ANGLE_DATA = 0x0D  # NeuCir angle 0–100%
    NTF_EEG = 0x10  # EEG
    NTF_ECG = 0x11  # ECG
    NTF_IMPEDANCE = 0x12  # Impedance
    NTF_IMU = 0x13  # Combined IMU (ACC + gyro)
    NTF_ADS = 0x14  # Unitless ADS
    NTF_BRTH = 0x15  # Breathing
    NTF_IMPEDANCE_EXT = 0x16  # Extended impedance
    NTF_SPO2 = 0x17  # SpO2 / HR
    NTF_PPG = 0x18  # PPG raw
    NTF_DATA_TYPE_MAX = 0x19


@dataclass
class SensorData:
    """
    SensorData represents a single data packet delivered to a callback.
    """

    device_mac: str
    data_type: NtfDataType
    sample_rate: int
    channel_count: int
    package_sample_count: int
    package_index_length: int
    channel_samples: list[list[Sample]]
    last_package_counter: int
    last_package_index: int
    resolution_bits: int
    channel_mask: int
    min_package_sample_count: int
    K: float
    lost_package_count: int = 0

    def clear(self) -> None:
        self.channel_samples.clear()
        self.last_package_counter = -1
        self.last_package_index = 0
        self.resolution_bits = 0
        self.channel_mask = 0
        self.min_package_sample_count = 0
        self.K = 0.0
        self.lost_package_count = 0
