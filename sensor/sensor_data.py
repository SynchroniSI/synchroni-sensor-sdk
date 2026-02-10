from enum import Enum, IntEnum
from typing import Dict, List


# 一个采样数据
# 该类用于存储单个采样数据的相关信息，包括数据值、阻抗、饱和度、采样索引和是否丢包的标志
class Sample:
    # """
    # Initialize a Sample instance.

    # :param data: 数据值，单位为 uV
    # :param impedance:  阻抗值，单位为 Ω
    # :param saturation: 饱和度值，单位为 % ，值 0-100
    # :param sample_index: 采样索引，用于标识采样的顺序
    # :param is_lost: 是否丢包的标志，True 表示丢包，False 表示正常
    # """
    # def __init__(self, data: int, impedance: int, saturation: int, sample_index: int, is_lost: bool):
    #     self.data = data
    #     self.impedance = impedance
    #     self.saturation = saturation
    #     self.sampleIndex = sample_index
    #     self.isLost = is_lost

    def __init__(self):
        self.rawData = 0
        self.data = 0
        self.impedance = 0
        self.saturation = 0
        self.sampleIndex = 0
        self.isLost = False
        self.timeStampInMs = 0
        self.channelIndex = 0
        self.sampleIndex = 0


# 对应 DataType 枚举
# 该枚举类定义了不同类型的数据，用于区分传感器采集的不同类型的数据
class DataType(IntEnum):
    NTF_ACC = 0x1  # 加速度，用于标识加速度传感器采集的数据
    NTF_GYRO = 0x2  # 陀螺仪，用于标识陀螺仪传感器采集的数据
    NTF_EMG = 0x8  # EMG，用于标识肌电传感器采集的数据
    NTF_MAG_ANGLE_DATA = 0x0D #NeuCir设备的角度值百分比0%-100%
    NTF_EEG = 0x10  # EEG，用于标识脑电传感器采集的数据
    NTF_ECG = 0x11  # ECG，用于标识心电传感器采集的数据
    NTF_IMPEDANCE = 0x12  # 阻抗数据
    NTF_IMU = 0x13  # 包含ACC和GYRO数据
    NTF_ADS = 0x14  # 无单位ads数据
    NTF_BRTH = 0x15  # 呼吸，用于标识呼吸传感器采集的数据
    NTF_IMPEDANCE_EXT = 0x16  # 阻抗数据扩展
    NTF_DATA_TYPE_MAX = 0x17



# 一次采样的数据，包含多个通道的数据，channal_samples 为一个二维数组, 第一个维度为通道索引，第二个维度为采样索引
# 该类用于存储一次采样的完整数据，包括设备 MAC 地址、数据类型、采样率、通道数量、包中采样数量以及通道采样数据
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

    def __init__(self):
        self.deviceMac = ""
        self.dataType = DataType.NTF_EEG
        self.sampleRate = 0
        self.channelCount = 0
        self.packageSampleCount = 0
        self.packageIndexLength = 2
        self.channelSamples: List[List[Sample]] = list()
        self.lastPackageCounter = 0
        self.lastPackageIndex = 0
        self.resolutionBits = 0
        self.channelMask = 0
        self.minPackageSampleCount = 0
        self.K = 0

    def clear(self):
        self.channelSamples.clear()
        self.lastPackageCounter = -1
        self.lastPackageIndex = 0
