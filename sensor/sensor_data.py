from enum import IntEnum




class Sample:
    # """
    # Initialize a Sample instance.






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




class DataType(IntEnum):
    NTF_ACC = 0x1
    NTF_GYRO = 0x2
    NTF_EULER_DATA = 0x4
    NTF_QUATERNION = 0x5
    NTF_GEST = 0x07
    NTF_EMG = 0x8
    NTF_MAG_ANGLE_DATA = 0x0D
    NTF_EEG = 0x10
    NTF_ECG = 0x11
    NTF_IMPEDANCE = 0x12
    NTF_IMU = 0x13
    NTF_ADS = 0x14
    NTF_BRTH = 0x15
    NTF_IMPEDANCE_EXT = 0x16
    NTF_SPO2 = 0x17
    NTF_PPG = 0x18


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
        self.lostPackageCount = 0
        self.resolutionBits = 0
        self.resolutionSigned = 0
        self.channelMask = 0
        self.minPackageSampleCount = 0
        self.K = 0

    def clear(self) -> None:
        self.channelSamples.clear()
        self.lastPackageCounter = -1
        self.lastPackageIndex = 0
        self.lostPackageCount = 0
