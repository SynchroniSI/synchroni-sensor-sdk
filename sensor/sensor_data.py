from enum import IntEnum


# 一个采样数据 / A single sample datum.
# 该类用于存储单个采样数据的相关信息，包括数据值、阻抗、饱和度、采样索引和是否丢包的标志
# This class holds one sample: value, impedance, saturation, sample index, and packet-loss flag.
class Sample:
    # """
    # Initialize a Sample instance.
    # :param data: 数据值，单位为 uV / Sample value in uV
    # :param impedance: 阻抗值，单位为 Ω / Impedance in Ω
    # :param saturation: 饱和度值，单位为 % ，值 0-100 / Saturation in %, 0-100
    # :param sample_index: 采样索引，用于标识采样的顺序 / Sample index for ordering
    # :param is_lost: 是否丢包的标志，True 表示丢包，False 表示正常 / True if packet lost, False otherwise
    # """
    # def __init__(self, data: int, impedance: int, saturation: int, sample_index: int, is_lost: bool):
    #     self.data = data
    #     self.impedance = impedance
    #     self.saturation = saturation
    #     self.sampleIndex = sample_index
    #     self.isLost = is_lost

    def __init__(self) -> None:
        self.rawData: int = 0
        self.data: int = 0
        self.impedance: int = 0
        self.saturation: float = 0.0
        self.sampleIndex: int = 0
        self.isLost: bool = False
        self.timeStampInMs: int = 0
        self.channelIndex: int = 0


# 对应 DataType 枚举 / DataType enum.
# 该枚举类定义了不同类型的数据，用于区分传感器采集的不同类型的数据
# Enum of sensor data types (acceleration, gyro, EMG, EEG, ECG, impedance, etc.).
class DataType(IntEnum):
    NTF_ACC = 0x1  # 加速度 / Accelerometer
    NTF_GYRO = 0x2  # 陀螺仪 / Gyroscope
    NTF_EMG = 0x8  # EMG，肌电 / EMG (electromyography)
    NTF_MAG_ANGLE_DATA = 0x0D  # NeuCir 角度值百分比 0%-100% / NeuCir angle 0–100%
    NTF_EEG = 0x10  # EEG，脑电 / EEG (electroencephalography)
    NTF_ECG = 0x11  # ECG，心电 / ECG (electrocardiography)
    NTF_IMPEDANCE = 0x12  # 阻抗数据 / Impedance
    NTF_IMU = 0x13  # 包含 ACC 和 GYRO / IMU (ACC + gyro)
    NTF_ADS = 0x14  # 无单位 ads 数据 / Unitless ADS data
    NTF_BRTH = 0x15  # 呼吸 / Breathing
    NTF_IMPEDANCE_EXT = 0x16  # 阻抗数据扩展 / Extended impedance
    NTF_DATA_TYPE_MAX = 0x17


# 一次采样的数据，包含多个通道；channel_samples 为二维数组（通道索引 × 采样索引）
# One packet of sampled data for multiple channels; channel_samples is [channel][sample].
# 该类用于存储一次采样的完整数据，包括设备 MAC、数据类型、采样率、通道数等
# Holds one sample packet: device MAC, data type, sample rate, channel count, and channel samples.
class SensorData:
    # """
    # Initialize a SensorData instance.

    # :param device_mac: The MAC address of the device.
    # :param data_type: The type of data being collected.
    # :param sample_rate: The rate at which samples are collected.
    # :param channel_count: The number of channels in the data.
    # :param package_sample_count: The number of samples in the package.
    # :param channel_samples: A list of lists containing the sample data for each channel.
    # """
    # def __init__(self, device_mac: str, data_type: DataType, sample_rate: int, channel_count: int,
    #              package_sample_count: int, channel_samples: List[List[Sample]]):
    #     self.deviceMac = device_mac
    #     self.dataType = data_type
    #     self.sampleRate = sample_rate
    #     self.channelCount = channel_count
    #     self.packageSampleCount = package_sample_count
    #     self.channelSamples = channel_samples
    #     self.lastPackageCounter = 0
    #     self.lastPackageIndex = 0
    #     self.resolutionBits = 0
    #     self.channelMask = 0
    #     self.minPackageSampleCount = 0
    #     self.K = 0

    def __init__(self) -> None:
        self.deviceMac: str = ""
        self.dataType: DataType = DataType.NTF_EEG
        self.sampleRate: int = 0
        self.channelCount: int = 0
        self.packageSampleCount: int = 0
        self.packageIndexLength: int = 2
        self.channelSamples: list[list[Sample]] = list()
        self.lastPackageCounter: int = 0
        self.lastPackageIndex: int = 0
        self.resolutionBits: int = 0
        self.channelMask: int = 0
        self.minPackageSampleCount: int = 0
        self.K: float = 0.0

    def clear(self) -> None:
        self.channelSamples.clear()
        self.lastPackageCounter = -1
        self.lastPackageIndex = 0
