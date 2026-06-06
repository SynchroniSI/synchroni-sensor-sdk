# 详细设备信息 / Detailed device info.
# 该类用于存储设备的详细信息，包括设备名称、型号、硬件和固件版本、各种通道数量以及 MTU 大小
# Holds device name, model, hardware/firmware version, channel counts, and MTU size.
from enum import Enum


class DeviceInfo:
    # """
    # Initialize a DeviceInfo instance.
    # :param device_name: 设备名称 / Device name
    # :param model_name: 设备型号 / Model name
    # :param hardware_version: 设备硬件版本 / Hardware version
    # :param firmware_version: 设备固件版本 / Firmware version
    # :param emg_channel_count: EMG 通道数量 / EMG channel count
    # :param eeg_channel_count: EEG 通道数量 / EEG channel count
    # :param ecg_channel_count: ECG 通道数量 / ECG channel count
    # :param acc_channel_count: 加速度通道数量 / Accelerometer channel count
    # :param gyro_channel_count: 陀螺仪通道数量 / Gyro channel count
    # :param brth_channel_count: 呼吸通道数量 / Breathing channel count
    # :param mtu_size: MTU 大小 / MTU size
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
        self.MTUSize: int = 0


class DeviceStateEx(Enum):
    Disconnected = 0
    Connecting = 1
    Connected = 2
    Ready = 3
    Disconnecting = 4
    Invalid = 5


# 蓝牙设备信息 / BLE device info.
# 该类用于存储蓝牙设备的基本信息，包括设备名称、地址和信号强度
# Holds BLE device name, address, and RSSI.
class BLEDevice:
    """
    Initialize a BLEDevice instance.
    :param name: 设备名称 / Device name
    :param address: 设备地址 / Device (MAC) address
    :param rssi: 信号强度 / Signal strength (RSSI)
    """

    def __init__(self, name: str, address: str, rssi: int) -> None:
        # 初始化 / Initialize (creates a beacon-like object)
        self.Name: str = name  # 设备名称 / Device name
        self.Address: str = address  # 设备地址 / Address
        self.RSSI: int = rssi
