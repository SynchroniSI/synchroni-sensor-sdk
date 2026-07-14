from sensor.sensor_controller import SensorController, SensorControllerInstance
from sensor.sensor_profile import SensorProfile
from sensor.sensor_device import BLEDevice, DeviceInfo, DeviceStateEx
from sensor.sensor_data import DataType, Sample, SensorData
from sensor.winrt_high_throughput import apply as _apply_winrt_high_throughput_patch
from sensor.bleak_no_ack_patch import apply as _apply_bleak_no_ack_patch
import os

# Windows 下尝试给 bleak WinRT backend 打高吞吐率连接参数补丁
_apply_winrt_high_throughput_patch()

# 如果设置了环境变量 SENSOR_SDK_FORCE_NO_ACK=1，则对命令特征强制 write-without-response
if os.environ.get("SENSOR_SDK_FORCE_NO_ACK", "0") == "1":
    _apply_bleak_no_ack_patch()

__all__ = [
    "SensorController",
    "SensorControllerInstance",
    "SensorProfile",
    "BLEDevice",
    "DeviceInfo",
    "DeviceStateEx",
    "DataType",
    "Sample",
    "SensorData",
]
