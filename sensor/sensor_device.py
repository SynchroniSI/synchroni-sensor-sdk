# 详细设备信息
# 该类用于存储设备的详细信息，包括设备名称、型号、硬件和固件版本、各种通道数量以及 MTU 大小
from enum import Enum


class DeviceInfo:
    # """
    # Initialize a DeviceInfo instance.

    # :param    device_name (str): 设备名称
    # :param    model_name (str): 设备型号
    # :param    hardware_version (str): 设备硬件版本
    # :param    firmware_version (str): 设备固件版本
    # :param    emg_channel_count (int): EMG 通道数量
    # :param    eeg_channel_count (int): EEG 通道数量
    # :param    ecg_channel_count (int): ECG 通道数量
    # :param    acc_channel_count (int): 加速度通道数量
    # :param    gyro_channel_count (int): 陀螺仪通道数量
    # :param    brth_channel_count (int): 呼吸通道数量
    # :param    mtu_size (int): MTU 大小
    # """
    # def __init__(self, device_name: str, model_name: str, hardware_version: str, firmware_version: str,
    #              emg_channel_count: int, eeg_channel_count: int, ecg_channel_count: int,
    #              acc_channel_count: int, gyro_channel_count: int, brth_channel_count: int, mtu_size: int):
    #     self.DeviceName = device_name
    #     self.ModelName = model_name
    #     self.HardwareVersion = hardware_version
    #     self.FirmwareVersion = firmware_version
    #     self.EmgChannelCount = emg_channel_count
    #     self.EegChannelCount = eeg_channel_count
    #     self.EcgChannelCount = ecg_channel_count
    #     self.AccChannelCount = acc_channel_count
    #     self.GyroChannelCount = gyro_channel_count
    #     self.BrthChannelCount = brth_channel_count
    #     self.MTUSize = mtu_size

    def __init__(self):
        self.DeviceName = ""
        self.ModelName = ""
        self.HardwareVersion = ""
        self.FirmwareVersion = ""
        self.EmgChannelCount = 0
        self.EmgSampleRate = 0
        self.EegChannelCount = 0
        self.EegSampleRate = 0
        self.EcgChannelCount = 0
        self.EcgSampleRate = 0
        self.AccChannelCount = 0
        self.AccSampleRate = 0
        self.GyroChannelCount = 0
        self.GyroSampleRate = 0
        self.BrthChannelCount = 0
        self.BrthSampleRate = 0
        self.MagAngleChannelCount = 0
        self.MagAngleSampleRate = 0
        self.MTUSize = 0


class DeviceStateEx(Enum):
    Disconnected = 0
    Connecting = 1
    Connected = 2
    Ready = 3
    Disconnecting = 4
    Invalid = 5


# 蓝牙设备信息
# 该类用于存储蓝牙设备的基本信息，包括设备名称、地址和信号强度
class BLEDevice:
    """
    Initialize a BLEDevice instance.
    :param   name (str): 设备名称
    :param   address (str): 设备地址
    :param   rssi (int): 信号强度
    """

    def __init__(self, name: str, address: str, rssi: int):

        # 初始化函数，用于创建一个Beacon对象
        self.Name = name  # 设置Beacon的名称
        self.Address = address  # 设置Beacon的地址
        self.RSSI = rssi
