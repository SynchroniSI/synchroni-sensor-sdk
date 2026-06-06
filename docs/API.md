# API Reference

High-level overview of the main classes exported by the `sensor` package.

## Architecture

The SDK manages BLE communication on an internal asyncio event loop. Most operations are available in two forms:

| Interface | Usage | How it works |
|-----------|-------|--------------|
| **Sync** | Call directly from synchronous code | Blocks the caller while the internal loop runs the underlying coroutine |
| **Async** | `await` from your own async code | Runs on the SDK's event loop without blocking your thread |

Methods prefixed with `async` (e.g. `asyncConnect`) are the async counterparts of their sync siblings. There is no async variant for continuous scanning (`startScan` / `stopScan`) — use `asyncScan` for one-shot scans instead.

Callbacks (`onDeviceFoundCallback`, `onDataCallback`, etc.) are always **synchronous** functions. The SDK invokes them from its event loop, typically via a thread-pool executor so they do not block BLE I/O.

Typical lifecycle:

```
SensorController.scan / startScan
  → requireSensor(BLEDevice)
    → SensorProfile.connect
      → SensorProfile.init
        → SensorProfile.startDataNotification
          → onDataCallback(SensorData)   # streaming
        → SensorProfile.stopDataNotification
      → SensorProfile.disconnect
  → SensorController.terminate
```

---

## SensorController

**Role:** Entry point for the SDK. Discovers BLE devices, manages `SensorProfile` instances, and coordinates shutdown.

`SensorController` is a **singleton** — repeated construction returns the same instance. A pre-created instance is exported as `SensorControllerInstance`.

### Sync methods

| Method | Returns | Description |
|--------|---------|-------------|
| `scan(period: int)` | `list[BLEDevice]` | One-shot scan for `period` milliseconds, then return discovered devices |
| `startScan(period_in_ms: int)` | `bool` | Start continuous scanning; `onDeviceFoundCallback` fires every `period_in_ms` |
| `stopScan()` | `None` | Stop continuous scanning |
| `terminate()` | `None` | Disconnect all sensors and shut down the SDK |
| `requireSensor(device: BLEDevice)` | `SensorProfile \| None` | Get or create a profile for a discovered device |
| `getSensor(device_mac: str)` | `SensorProfile \| None` | Look up an existing profile by MAC address |
| `getConnectedSensors()` | `list[SensorProfile]` | Profiles in `Connected` or `Ready` state |
| `getConnectedDevices()` | `list[BLEDevice]` | BLE devices for connected profiles |

### Async methods

| Method | Returns | Description |
|--------|---------|-------------|
| `asyncScan(period: int)` | `list[BLEDevice]` | Async one-shot scan (equivalent to `scan`) |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `isScanning` | `bool` | Whether a scan is in progress |
| `isEnable` | `bool` | Whether Bluetooth is enabled |
| `hasDeviceFoundCallback` | `bool` | Whether a device-found callback is registered |

### Callback setters

| Setter | Signature | Description |
|--------|-----------|-------------|
| `onDeviceFoundCallback` | `(devices: list[BLEDevice]) -> None` | Called periodically during continuous scanning |
| `onEnableCallback` | `(enabled: bool) -> None` | Called when system Bluetooth is toggled |

---

## SensorProfile

**Role:** Represents a single sensor device. Handles connection, configuration, streaming, and battery monitoring.

One `SensorProfile` is created per device via `SensorController.requireSensor()`.

### Sync methods

| Method | Returns | Description |
|--------|---------|-------------|
| `connect()` | `bool` | Connect to the device; transitions through `Connecting` → `Connected` → `Ready` |
| `disconnect()` | `bool` | Disconnect and tear down the BLE session |
| `init(package_sample_count: int, power_refresh_interval: int)` | `bool` | Initialize the data context; must be called in `Ready` state |
| `startDataNotification()` | `bool` | Begin streaming sensor data via `onDataCallback` |
| `stopDataNotification()` | `bool` | Stop streaming |
| `getBatteryLevel()` | `int` | Last known battery level (0–100, or -1 if unknown) |
| `getDeviceInfo()` | `DeviceInfo \| None` | Device capabilities; available after `init()` succeeds |
| `setParam(key: str, value: str)` | `str` | Configure notifications, filters, or debug output; returns `"OK"` on success |

### Async methods

| Method | Returns | Sync equivalent | Notes |
|--------|---------|-----------------|-------|
| `asyncConnect()` | `bool` | `connect()` | |
| `asyncDisconnect()` | `bool` | `disconnect()` | |
| `asyncInit(package_sample_count, power_refresh_interval)` | `bool` | `init()` | |
| `asyncStartDataNotification()` | `bool` | `startDataNotification()` | |
| `asyncStopDataNotification()` | `bool` | `stopDataNotification()` | |
| `asyncGetBatteryLevel()` | `int` | `getBatteryLevel()` | Fetches a fresh reading from the device |
| `asyncSetParam(key, value)` | `str` | `setParam()` | |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `deviceState` | `DeviceStateEx` | Current connection state |
| `hasInited` | `bool` | Whether `init()` completed successfully |
| `isDataTransfering` | `bool` | Whether data notifications are active |
| `BLEDevice` | `BLEDevice` | The underlying discovered device |

### Callback setters

| Setter | Signature | Description |
|--------|-----------|-------------|
| `onStateChanged` | `(sensor: SensorProfile, state: DeviceStateEx) -> None` | Connection state transitions |
| `onErrorCallback` | `(sensor: SensorProfile, reason: str) -> None` | Errors during operation |
| `onDataCallback` | `(sensor: SensorProfile, data: SensorData) -> None` | Incoming sensor data packets |
| `onPowerChanged` | `(sensor: SensorProfile, power: int) -> None` | Battery level updates (0–100, -1 invalid) |

### setParam keys

| Key | Values | Description |
|-----|--------|-------------|
| `NTF_EMG`, `NTF_EEG`, `NTF_ECG`, `NTF_IMU`, `NTF_BRTH`, `NTF_IMPEDANCE` | `ON` / `OFF` | Enable or disable data streams |
| `FILTER_50HZ`, `FILTER_60HZ`, `FILTER_HPF`, `FILTER_LPF` | `ON` / `OFF` | Signal filters |
| `DEBUG_BLE_DATA_PATH` | absolute file path | Write raw BLE data to CSV |
| `NEUCIR_SET_MODE` | `APP_REMOTE` | NeuCir operating mode |
| `NEUCIR_APP_CONTROL` | `OPEN` / `CLOSE` / `STOP` | NeuCir app control |

---

## BLEDevice

**Role:** Lightweight descriptor for a discovered Bluetooth device. Created by the scanner; passed to `requireSensor()`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `Name` | `str` | Advertised device name |
| `Address` | `str` | MAC address |
| `RSSI` | `int` | Signal strength |

---

## DeviceInfo

**Role:** Static device capabilities, populated after `SensorProfile.init()` succeeds. Returned by `getDeviceInfo()`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `DeviceName` | `str` | Device name |
| `ModelName` | `str` | Model identifier |
| `HardwareVersion` | `str` | Hardware revision |
| `FirmwareVersion` | `str` | Firmware revision |
| `EmgChannelCount` | `int` | EMG channel count |
| `EmgSampleRate` | `int` | EMG sample rate (Hz) |
| `EegChannelCount` | `int` | EEG channel count |
| `EegSampleRate` | `int` | EEG sample rate (Hz) |
| `EcgChannelCount` | `int` | ECG channel count |
| `EcgSampleRate` | `int` | ECG sample rate (Hz) |
| `AccChannelCount` | `int` | Accelerometer channel count |
| `AccSampleRate` | `int` | Accelerometer sample rate (Hz) |
| `GyroChannelCount` | `int` | Gyroscope channel count |
| `GyroSampleRate` | `int` | Gyroscope sample rate (Hz) |
| `BrthChannelCount` | `int` | Breathing channel count |
| `BrthSampleRate` | `int` | Breathing sample rate (Hz) |
| `MagAngleChannelCount` | `int` | Magnetic angle channel count |
| `MagAngleSampleRate` | `int` | Magnetic angle sample rate (Hz) |
| `MTUSize` | `int` | BLE MTU size |

---

## DeviceStateEx

**Role:** Enum representing a sensor's connection lifecycle.

| Value | Meaning |
|-------|---------|
| `Disconnected` | Not connected |
| `Connecting` | Connection in progress |
| `Connected` | BLE link established, not yet ready for commands |
| `Ready` | Ready to receive commands (`init`, `setParam`, streaming) |
| `Disconnecting` | Disconnection in progress |
| `Invalid` | Unrecoverable state |

---

## SensorData

**Role:** A single data packet delivered to `onDataCallback`. Contains one or more channels of samples.

| Attribute | Type | Description |
|-----------|------|-------------|
| `deviceMac` | `str` | Source device MAC address |
| `dataType` | `DataType` | Kind of sensor data in this packet |
| `sampleRate` | `int` | Sample rate (Hz) |
| `channelCount` | `int` | Number of channels |
| `packageSampleCount` | `int` | Samples per channel in this packet |
| `channelSamples` | `list[list[Sample]]` | `[channel][sample]` sample data |
| `lastPackageCounter` | `int` | Packet sequence counter |
| `lastPackageIndex` | `int` | Packet index |
| `resolutionBits` | `int` | ADC resolution |
| `channelMask` | `int` | Active channel bitmask |
| `minPackageSampleCount` | `int` | Minimum expected samples per packet |
| `K` | `float` | Scaling factor |

| Method | Description |
|--------|-------------|
| `clear()` | Reset internal sample buffers |

---

## Sample

**Role:** A single measurement within a `SensorData` packet.

| Attribute | Type | Description |
|-----------|------|-------------|
| `rawData` | `int` | Unprocessed ADC value |
| `data` | `int` | Processed value (e.g. uV for EEG/ECG) |
| `impedance` | `int` | Electrode impedance (Ω) |
| `saturation` | `float` | Saturation level (0–100%) |
| `sampleIndex` | `int` | Index within the packet |
| `isLost` | `bool` | `True` if this sample was lost in transmission |
| `timeStampInMs` | `int` | Timestamp in milliseconds |
| `channelIndex` | `int` | Channel this sample belongs to |

---

## DataType

**Role:** Enum identifying the kind of data in a `SensorData` packet.

| Value | Hex | Description |
|-------|-----|-------------|
| `NTF_ACC` | `0x01` | Accelerometer (g) |
| `NTF_GYRO` | `0x02` | Gyroscope (°/s) |
| `NTF_EMG` | `0x08` | EMG (µV) |
| `NTF_MAG_ANGLE_DATA` | `0x0D` | NeuCir angle (0–100%) |
| `NTF_EEG` | `0x10` | EEG (µV) |
| `NTF_ECG` | `0x11` | ECG (µV) |
| `NTF_IMPEDANCE` | `0x12` | Impedance |
| `NTF_IMU` | `0x13` | Combined accelerometer + gyroscope |
| `NTF_ADS` | `0x14` | Unitless ADS data |
| `NTF_BRTH` | `0x15` | Breathing (µV) |
| `NTF_IMPEDANCE_EXT` | `0x16` | Extended impedance |

---

## Exceptions

All exceptions inherit from `SensorError`.

| Exception | When raised |
|-----------|-------------|
| `SensorTerminatedError` | SDK has been terminated via `SensorController.terminate()` |
| `SensorNotConnectedError` | Operation requires an active BLE connection |
| `SensorNotReadyError` | Device is not in `Ready` state |
| `SensorNotInitializedError` | `init()` has not been called |
| `InvalidDeviceServiceError` | Device does not advertise a supported service UUID |
| `DataNotificationInProgressError` | Concurrent start/stop notification call |
| `StartDataNotificationError` | Failed to start streaming |
| `StopDataNotificationError` | Failed to stop streaming |
| `DataContextInitInProgressError` | `init()` already in progress |
| `DataContextInitError` | Data context initialization failed |
| `DataContextStopStreamingError` | Failed to stop the data context |
| `DataContextNotTransferringError` | Operation requires active streaming |
| `DataContextReadSamplesError` | Error parsing incoming sample data |

---

## Choosing sync vs async

Use **sync** when your application is single-threaded and blocking is acceptable (e.g. scripts, simple demos). See `examples/console.py`.

Use **async** when you already run an asyncio event loop or need non-blocking I/O alongside other async work. See `examples/async_console.py`.

```python
# Sync workflow
devices = controller.scan(3000)
profile = controller.requireSensor(devices[0])
profile.connect()
profile.init(10, 5000)
profile.startDataNotification()

# Async workflow
devices = await controller.asyncScan(3000)
profile = controller.requireSensor(devices[0])
await profile.asyncConnect()
await profile.asyncInit(10, 5000)
await profile.asyncStartDataNotification()
```

Do not mix sync and async calls on the same profile from different threads without care — both interfaces share the same internal event loop.
