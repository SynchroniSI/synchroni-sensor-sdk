# synchroni-sensor-sdk

Synchroni sdk for Python

## Brief

Synchroni SDK is the software development kit for developers to access Synchroni products.

## Contributing

See the [contributing guide](CONTRIBUTING.md) to learn how to contribute to the repository and the development workflow.

## License

MIT

---

## Installation

```sh
pip install synchroni-sensor-sdk 
```

## 1. Permission

Application will obtain bluetooth permission by itself.

## 2. Import SDK

```python
from sensor import *
```

## SensorController methods

### 1. Initalize

```python
SensorControllerInstance = SensorController()

# register scan listener
if not SensorControllerInstance.hasDeviceFoundCallback:
    def on_device_callback(deviceList: List[BLEDevice]):
        # return all devices doesn't connected
        pass
    SensorControllerInstance.onDeviceFoundCallback = on_device_callback
```

### 2. Start scan

Use `def startScan(period_in_ms: int) -> bool` to start scan

```python
success = SensorControllerInstance.startScan(6000)
```

returns true if start scan success, periodInMS means onDeviceCallback will be called every periodInMS

Use `def scan(period_in_ms: int) -> list[BLEDevice]` to scan once time

```python
bleDevices = SensorControllerInstance.scan(6000)
```

### 3. Stop scan

Use `def stopScan() -> None` to stop scan

```python
SensorControllerInstance.stopScan()
```

### 4. Check scaning

Use `property isScanning: bool` to check scanning status

```python
isScanning = SensorControllerInstance.isScanning
```

### 5. Check if bluetooth is enabled

Use `property isEnable: bool` to check if bluetooth is enable

```python
isEnable = SensorControllerInstance.isEnable
```

### 6. Create SensorProfile

Use `def requireSensor(device: BLEDevice) -> SensorProfile | None` to create SensorProfile.

If bleDevice is invalid, result is None.

```python
sensorProfile = SensorControllerInstance.requireSensor(bleDevice)
```

### 7. Get SensorProfile

Use `def getSensor(device: BLEDevice) -> SensorProfile | None` to get SensorProfile.

If SensorProfile didn't created, result is None.

```python
sensorProfile = SensorControllerInstance.getSensor(bleDevice)
```

### 8. Get Connected SensorProfiles

Use `def getConnectedSensors() -> list[SensorProfile]` to get connected SensorProfiles.

```python
sensorProfiles = SensorControllerInstance.getConnectedSensors()
```

### 9. Get Connected BLE Devices

Use `def getConnectedDevices() -> list[BLEDevice]` to get connected BLE Devices.

```python
bleDevices = SensorControllerInstance.getConnectedDevices()
```

### 10. Terminate

Use `def terminate()` to terminate sdk

```python

def terminate():
    SensorControllerInstance.terminate()
    exit()
    
def main():
    signal.signal(signal.SIGINT, lambda signal, frame: terminate())
    time.sleep(30)
    SensorControllerInstance.terminate()
    
Please MAKE SURE to call terminate when exit main() or press Ctrl+C
```

## SensorProfile methods

### 11. Initalize

Please register callbacks for SensorProfile

```python
sensorProfile = SensorControllerInstance.requireSensor(bleDevice)

# register callbacks
def on_state_changed(sensor, newState):
    # please do logic when device disconnected unexpected
    pass

def on_error_callback(sensor, reason):
    # called when error occurs
    pass

def on_power_changed(sensor, power):
    # callback for get battery level of device, power from 0 - 100, -1 is invalid
    pass

def on_data_callback(sensor, data):
    # called after start data transfer
    pass

sensorProfile.onStateChanged = on_state_changed
sensorProfile.onErrorCallback = on_error_callback
sensorProfile.onPowerChanged = on_power_changed
sensorProfile.onDataCallback = on_data_callback
```

### 12. Connect device

Use `def connect() -> bool` to connect.

```python
success = sensorProfile.connect()
```

### 13. Disconnect

Use `def disconnect() -> bool` to disconnect.

```python
success = sensorProfile.disconnect()
```

### 14. Get device status

Use `property deviceState: DeviceStateEx` to get device status.

Please send command in 'Ready' state, should be after connect() return True.

```python
deviceStateEx = sensorProfile.deviceState

# deviceStateEx has define:
# class DeviceStateEx(Enum):
#     Disconnected = 0
#     Connecting = 1
#     Connected = 2
#     Ready = 3
#     Disconnecting = 4
#     Invalid = 5
```

### 15. Get BLE device of SensorProfile

Use `property BLEDevice: BLEDevice` to get BLE device of SensorProfile.

```python
bleDevice = sensorProfile.BLEDevice
```

### 16. Get device info of SensorProfile

Use `def getDeviceInfo() -> dict | None` to get device info of SensorProfile.

Please call after device in 'Ready' state, return None if it's not connected.

```python
    deviceInfo = sensorProfile.getDeviceInfo()

# deviceInfo has defines:
# deviceInfo = {
#     "deviceName": str,
#     "modelName": str,
#     "hardwareVersion": str,
#     "firmwareVersion": str,
#     "emgChannelCount": int,
#     "eegChannelCount": int,
#     "ecgChannelCount": int,
#     "accChannelCount": int,
#     "gyroChannelCount": int,
#     "brthChannelCount": int,
#     "mtuSize": int
# }
```

### 17. Init data transfer

Use `def init(packageSampleCount: int, powerRefreshInterval: int) -> bool`.

Please call after device in 'Ready' state, return True if init succeed.

```python
success = sensorProfile.init(5, 60*1000)
```

packageSampleCount:   set sample counts of SensorData.channelSamples in onDataCallback()
powerRefreshInterval: callback period for onPowerChanged()

### 18. Check if init data transfer succeed

Use `property hasInited: bool` to check if init data transfer succeed.

```python
hasInited = sensorProfile.hasInited
```

### 19. DataNotify

Use `def startDataNotification() -> bool` to start data notification.

Please call if hasInited return True

#### 19.1 Start data transfer

```python
success = sensorProfile.startDataNotification()
```

Data type list：

```python
class DataType(Enum):
    NTF_ACC = 0x1  # unit is g
    NTF_GYRO = 0x2  # unit is degree/s
    NTF_EEG = 0x10  # unit is uV
    NTF_ECG = 0x11  # unit is uV
    NTF_BRTH = 0x15  # unit is uV
```

Process data in onDataCallback.

```python
def on_data_callback(sensor, data):
    if data.dataType == DataType.NTF_EEG:
        pass
    elif data.dataType == DataType.NTF_ECG:
        pass

    # process data as you wish
    for oneChannelSamples in data.channelSamples:
        for sample in oneChannelSamples:
            if sample.isLost:
                # do some logic
                pass
            else:
                # draw with sample.data & sample.channelIndex
                # print(f"{sample.channelIndex} | {sample.sampleIndex} | {sample.data} | {sample.impedance}")
                pass

sensorProfile.onDataCallback = on_data_callback
```

#### 19.2 Stop data transfer

Use `def stopDataNotification() -> bool` to stop data transfer.

```python
success = sensorProfile.stopDataNotification()
```

#### 19.3 Check if it's data transfering

Use `property isDataTransfering: bool` to check if it's data transfering.

```python
isDataTransfering = sensorProfile.isDataTransfering
```

### 20. Get battery level

Use `def getBatteryLevel() -> int` to get battery level. Please call after device in 'Ready' state.

```python
batteryPower = sensorProfile.getBatteryLevel()

# batteryPower is battery level returned, value ranges from 0 to 100, 0 means out of battery, while 100 means full.
```

Please check console.py in examples directory

### Async methods

all methods start with async is async methods, they has same params and return result as sync methods.

Please check async_console.py in examples directory

### setParam method

Use `def setParam(self, key: str, value: str) -> str` to set parameter of sensor profile. Please call after device in 'Ready' state.

Below is available key and value:

```python
result = sensorProfile.setParam("NTF_EMG", "ON")
# set EMG data to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("NTF_EEG", "ON")
# set EEG data to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("NTF_ECG", "ON")
# set ECG data to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("NTF_IMU", "ON")
# set IMU data to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("NTF_BRTH", "ON")
# set BRTH data to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("FILTER_50HZ", "ON")
# set 50Hz notch filter to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("FILTER_60HZ", "ON")
# set 60Hz notch filter to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("FILTER_HPF", "ON")
# set 0.5Hz hpf filter to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("FILTER_LPF", "ON")
# set 80Hz lpf filter to ON or OFF, result is "OK" if succeed

result = sensorProfile.setParam("DEBUG_BLE_DATA_PATH", "d:/temp/test.csv")
# set debug ble data path, result is "OK" if succeed
# please give an absolute path and make sure it is valid and writeable by yourself
```
