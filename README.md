# synchroni-sensor-sdk

Python SDK for interfacing with Synchroni BLE sensor devices.

## Requirements

- Python 3.10+
- Bluetooth enabled on the host machine

## Installation

```sh
pip install synchroni-sensor-sdk

# Or install with poetry
poetry add synchroni-sensor-sdk
```

To install from source for development:

```sh
git clone https://github.com/SynchroniSI/synchroni-sensor-sdk.git
cd synchroni-sensor-sdk
pip install .
```

Or with [Poetry](https://python-poetry.org/):

```sh
poetry install
```

## Quick start

The SDK requests Bluetooth permissions automatically on supported platforms.

```python
from sensor import *
```

`SensorController` is a singleton. A pre-created instance is available as `SensorControllerInstance`:

```python
controller = SensorControllerInstance  # equivalent to SensorController()
```

See the [examples](examples/) directory for full working scripts:

- `console.py` — synchronous scan, connect, and stream
- `async_console.py` — async equivalents
- `SynchroniSDKPython_Demo.py` / `SynchroniSDKPython_DemoEMG.py` — GUI demos

For a class-by-class overview of the API (including sync vs async methods), see the [API reference](docs/API.md).

## SensorController methods

### Initialize

```python
controller = SensorController()

# register scan listener
if not controller.hasDeviceFoundCallback:
    def on_device_callback(device_list: list[BLEDevice]):
        # called periodically with discovered devices
        pass

    controller.onDeviceFoundCallback = on_device_callback
```

### Start scan

Use `startScan(period_in_ms: int) -> bool` to start continuous scanning:

```python
success = controller.startScan(6000)
```

Returns `True` if scanning started. `period_in_ms` controls how often `onDeviceFoundCallback` is invoked.

Use `scan(period_in_ms: int) -> list[BLEDevice]` for a one-shot scan:

```python
ble_devices = controller.scan(6000)
```

### Stop scan

```python
controller.stopScan()
```

### Check scanning status

```python
is_scanning = controller.isScanning
```

### Check if Bluetooth is enabled

```python
is_enabled = controller.isEnable
```

Register a callback for Bluetooth enable/disable changes:

```python
controller.onEnableCallback = lambda enabled: print(f"Bluetooth enabled: {enabled}")
```

### Create SensorProfile

Use `requireSensor(device: BLEDevice) -> SensorProfile | None` to get or create a `SensorProfile` for a device:

```python
sensor_profile = controller.requireSensor(ble_device)
```

### Get SensorProfile

Use `getSensor(device_mac: str) -> SensorProfile | None` to look up an existing profile by MAC address:

```python
sensor_profile = controller.getSensor(ble_device.Address)
```

Returns `None` if no profile exists for that address.

### Get connected SensorProfiles

```python
sensor_profiles = controller.getConnectedSensors()
```

### Get connected BLE devices

```python
ble_devices = controller.getConnectedDevices()
```

### Terminate

Call `terminate()` when your application exits (including on Ctrl+C) to disconnect sensors and release resources:

```python
import signal
import time

def shutdown():
    controller.terminate()
    exit()

def main():
    signal.signal(signal.SIGINT, lambda sig, frame: shutdown())
    # ... your application logic ...
    controller.terminate()

if __name__ == "__main__":
    main()
```

## SensorProfile methods

### Initialize callbacks

Register callbacks before connecting:

```python
sensor_profile = controller.requireSensor(ble_device)

def on_state_changed(sensor, new_state):
    # handle unexpected disconnects
    pass

def on_error_callback(sensor, reason):
    pass

def on_power_changed(sensor, power):
    # power ranges from 0–100; -1 is invalid
    pass

def on_data_callback(sensor, data):
    pass

sensor_profile.onStateChanged = on_state_changed
sensor_profile.onErrorCallback = on_error_callback
sensor_profile.onPowerChanged = on_power_changed
sensor_profile.onDataCallback = on_data_callback
```

### Connect

```python
success = sensor_profile.connect()
```

### Disconnect

```python
success = sensor_profile.disconnect()
```

### Device state

Use `deviceState: DeviceStateEx` to check connection status. Send commands only when the device is in the `Ready` state (after `connect()` returns `True`):

```python
state = sensor_profile.deviceState

# class DeviceStateEx(Enum):
#     Disconnected = 0
#     Connecting = 1
#     Connected = 2
#     Ready = 3
#     Disconnecting = 4
#     Invalid = 5
```

### BLE device

```python
ble_device = sensor_profile.BLEDevice
```

### Device info

Use `getDeviceInfo() -> DeviceInfo | None`. Call after the device reaches `Ready` state and `init()` succeeds. Returns `None` if not initialized.

```python
device_info = sensor_profile.getDeviceInfo()

# DeviceInfo attributes:
# device_info.DeviceName
# device_info.ModelName
# device_info.HardwareVersion
# device_info.FirmwareVersion
# device_info.EmgChannelCount, device_info.EmgSampleRate
# device_info.EegChannelCount, device_info.EegSampleRate
# device_info.EcgChannelCount, device_info.EcgSampleRate
# device_info.AccChannelCount, device_info.AccSampleRate
# device_info.GyroChannelCount, device_info.GyroSampleRate
# device_info.BrthChannelCount, device_info.BrthSampleRate
# device_info.MagAngleChannelCount, device_info.MagAngleSampleRate
# device_info.MTUSize
```

### Init data transfer

Use `init(package_sample_count: int, power_refresh_interval: int) -> bool`. Call when the device is `Ready`. Returns `True` on success.

```python
success = sensor_profile.init(5, 60 * 1000)
```

- `package_sample_count`: number of samples per channel in each `onDataCallback` packet
- `power_refresh_interval`: interval in ms between `onPowerChanged` callbacks

### Check initialization

```python
has_inited = sensor_profile.hasInited
```

### Data notification

#### Start data transfer

Call `startDataNotification() -> bool` after `hasInited` is `True`:

```python
success = sensor_profile.startDataNotification()
```

Supported data types:

```python
class DataType(IntEnum):
    NTF_ACC = 0x1           # accelerometer, unit is g
    NTF_GYRO = 0x2          # gyroscope, unit is degree/s
    NTF_EMG = 0x8           # EMG, unit is uV
    NTF_MAG_ANGLE_DATA = 0x0D  # NeuCir angle 0–100%
    NTF_EEG = 0x10          # EEG, unit is uV
    NTF_ECG = 0x11          # ECG, unit is uV
    NTF_IMPEDANCE = 0x12    # impedance
    NTF_IMU = 0x13          # combined ACC + GYRO
    NTF_ADS = 0x14          # unitless ADS data
    NTF_BRTH = 0x15         # breathing, unit is uV
    NTF_IMPEDANCE_EXT = 0x16  # extended impedance
```

Process data in `onDataCallback`:

```python
def on_data_callback(sensor, data):
    if data.dataType == DataType.NTF_EEG:
        pass
    elif data.dataType == DataType.NTF_ECG:
        pass

    for channel_samples in data.channelSamples:
        for sample in channel_samples:
            if sample.isLost:
                pass
            else:
                # sample.data, sample.channelIndex, sample.sampleIndex
                # sample.impedance, sample.saturation, sample.timeStampInMs
                pass

sensor_profile.onDataCallback = on_data_callback
```

#### Stop data transfer

```python
success = sensor_profile.stopDataNotification()
```

#### Check if streaming

```python
is_transferring = sensor_profile.isDataTransfering
```

### Battery level

```python
battery_power = sensor_profile.getBatteryLevel()
# 0–100; -1 if unknown
```

### setParam

Use `setParam(key: str, value: str) -> str` when the device is `Ready`. Returns `"OK"` on success.

```python
sensor_profile.setParam("NTF_EMG", "ON")        # ON or OFF
sensor_profile.setParam("NTF_EEG", "ON")        # ON or OFF
sensor_profile.setParam("NTF_ECG", "ON")        # ON or OFF
sensor_profile.setParam("NTF_IMU", "ON")        # ON or OFF
sensor_profile.setParam("NTF_BRTH", "ON")       # ON or OFF
sensor_profile.setParam("NTF_IMPEDANCE", "ON")  # ON or OFF

sensor_profile.setParam("FILTER_50HZ", "ON")    # 50 Hz notch filter
sensor_profile.setParam("FILTER_60HZ", "ON")    # 60 Hz notch filter
sensor_profile.setParam("FILTER_HPF", "ON")     # 0.5 Hz high-pass filter
sensor_profile.setParam("FILTER_LPF", "ON")     # 80 Hz low-pass filter

sensor_profile.setParam("DEBUG_BLE_DATA_PATH", "/absolute/path/to/debug.csv")

# NeuCir-specific
sensor_profile.setParam("NEUCIR_SET_MODE", "APP_REMOTE")
sensor_profile.setParam("NEUCIR_APP_CONTROL", "OPEN")   # OPEN, CLOSE, or STOP
```

## Async methods

Methods prefixed with `async` are coroutine equivalents of their sync counterparts:

| Sync | Async |
|------|-------|
| `scan()` | `asyncScan()` |
| `connect()` | `asyncConnect()` |
| `disconnect()` | `asyncDisconnect()` |
| `init()` | `asyncInit()` |
| `getBatteryLevel()` | `asyncGetBatteryLevel()` |
| `startDataNotification()` | `asyncStartDataNotification()` |
| `stopDataNotification()` | `asyncStopDataNotification()` |
| `setParam()` | `asyncSetParam()` |

See `examples/async_console.py` for usage.

## Exceptions

The SDK raises typed exceptions (exported from `sensor`) for error conditions:

| Exception | Description |
|-----------|-------------|
| `SensorError` | Base class for all SDK errors |
| `SensorTerminatedError` | SDK has been terminated |
| `SensorNotConnectedError` | Device is not connected |
| `SensorNotReadyError` | Device is not in `Ready` state |
| `SensorNotInitializedError` | `init()` has not been called |
| `InvalidDeviceServiceError` | Unsupported device service UUID |
| `DataNotificationInProgressError` | Start/stop notification already in progress |
| `StartDataNotificationError` | Failed to start data notification |
| `StopDataNotificationError` | Failed to stop data notification |
| `DataContextInitInProgressError` | Initialization already in progress |
| `DataContextInitError` | Data context initialization failed |
| `DataContextStopStreamingError` | Failed to stop streaming |
| `DataContextNotTransferringError` | Operation requires active streaming |
| `DataContextReadSamplesError` | Error reading or processing samples |

## License

MIT — see [LICENSE.txt](LICENSE.txt).
