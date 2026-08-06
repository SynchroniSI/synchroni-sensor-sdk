import signal
import time

from sensor.sensor_controller import SensorControllerInstance
from sensor.sensor_data import DataType, SensorData
from sensor.sensor_device import BLEDevice, DeviceStateEx
from sensor.sensor_profile import SensorProfile

SCAN_DEVICE_PERIOD_IN_MS = 3000
PACKAGE_COUNT = 10
POWER_REFRESH_PERIOD_IN_MS = 5000


def SimpleTest():
    if not SensorControllerInstance.isEnable:
        print("please open bluetooth")
        return

    if not SensorControllerInstance.hasDeviceFoundCallback:
        SensorControllerInstance.onDeviceFoundCallback = deviceFoundCallback

    if not SensorControllerInstance.isScanning:
        print("start scan")
        if not SensorControllerInstance.startScan(SCAN_DEVICE_PERIOD_IN_MS):
            print("please try scan later")

    connectedSensors = SensorControllerInstance.getConnectedSensors()
    for sensor in connectedSensors:
        if sensor.hasInited:
            print(sensor.BLEDevice.Name + " power: " + str(sensor.getBatteryLevel()))
            sensor.disconnect()


def deviceFoundCallback(deviceList: list[BLEDevice]):
    print("stop scan")
    SensorControllerInstance.stopScan()

    filteredDevice = filter(
        lambda x: x.RSSI > -80 and (x.Name.startswith("OB") or x.Name.startswith("Sync") or x.Name.startswith("Orion")),
        deviceList,
    )
    for device in filteredDevice:
        sensor = SensorControllerInstance.requireSensor(device)
        if sensor is None:
            continue

        print("found: " + sensor.BLEDevice.Name)
        sensor.onDataCallback = onDataCallback
        sensor.onPowerChanged = onPowerChanged
        sensor.onStateChanged = onStateChanged
        sensor.onErrorCallback = onErrorCallback

        # check state & connect
        if sensor.deviceState != DeviceStateEx.Ready:
            print("connecting: " + sensor.BLEDevice.Address)
            if not sensor.connect():
                print("connect device: " + sensor.BLEDevice.Name + " failed")
                continue

        # init & start data transfer
        if sensor.deviceState == DeviceStateEx.Ready and not sensor.hasInited:
            # sensor.setParam("DEBUG_BLE_DATA_PATH", "d:/temp/test.csv")
            sensor.setParam("NTF_ECG", "OFF")
            sensor.setParam("NTF_IMU", "OFF")
            sensor.setParam("FILTER_50HZ", "OFF")
            sensor.setParam("FILTER_60HZ", "OFF")
            sensor.setParam("FILTER_HPF", "OFF")
            sensor.setParam("FILTER_LPF", "OFF")
            if not sensor.init(PACKAGE_COUNT, POWER_REFRESH_PERIOD_IN_MS):
                print("init device: " + sensor.BLEDevice.Name + " failed")
                continue
            deviceInfo = sensor.getDeviceInfo()
            if deviceInfo is not None:
                print("deviceInfo: Model: " + str(deviceInfo.ModelName))

        if sensor.hasInited:
            print("start data transfer")
            if not sensor.startDataNotification():
                print("start data transfer with device: " + sensor.BLEDevice.Name + " failed")
                continue
        break


def onDataCallback(sensor: SensorProfile, data: SensorData):
    if data.dataType == DataType.NTF_EEG and data.channelSamples:
        for sample in data.channelSamples[0]:
            print(sample.data)


def onPowerChanged(sensor: SensorProfile, power: int):
    print("connected sensor: " + sensor.BLEDevice.Name + " power: " + str(power))


def onStateChanged(sensor: SensorProfile, newstate: DeviceStateEx):
    print("device: " + sensor.BLEDevice.Name + str(newstate))


def onErrorCallback(sensor: SensorProfile, reason: str):
    print("device: " + sensor.BLEDevice.Name + reason)


def terminate():
    SensorControllerInstance.terminate()
    exit()


def main():
    signal.signal(signal.SIGINT, lambda signal, frame: terminate())
    SimpleTest()
    time.sleep(100)
    SensorControllerInstance.terminate()


if __name__ == "__main__":
    main()
