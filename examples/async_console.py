import signal
import time
from typing import List
from sensor import *

SCAN_DEVICE_PERIOD_IN_MS = 3000
PACKAGE_COUNT = 10
POWER_REFRESH_PERIOD_IN_MS = 5000


def terminate():
    SensorControllerInstance.terminate()
    exit()


async def main():
    signal.signal(signal.SIGINT, lambda signal, frame: terminate())

    if not SensorControllerInstance.isEnable:
        print("please open bluetooth")
        return

    deviceList = await SensorControllerInstance.asyncScan(3000)

    filteredDevice = filter(
        lambda x: x.RSSI > -80 and (x.Name.startswith("OB") or x.Name.startswith("Orion") or x.Name.startswith("Sync")),
        deviceList,
    )
    for device in filteredDevice:
        sensor = SensorControllerInstance.requireSensor(device)
        if sensor == None:
            continue

        print("found: " + sensor.BLEDevice.Name)
        sensor.onDataCallback = onDataCallback
        sensor.onPowerChanged = onPowerChanged
        sensor.onStateChanged = onStateChanged
        sensor.onErrorCallback = onErrorCallback

        # check state & connect
        if sensor.deviceState != DeviceStateEx.Ready:
            print("connecting: " + sensor.BLEDevice.Address)
            if not await sensor.asyncConnect():
                print("connect device: " + sensor.BLEDevice.Name + " failed")
                continue

        # init & start data transfer
        if sensor.deviceState == DeviceStateEx.Ready and not sensor.hasInited:
            await sensor.asyncSetParam("DEBUG_BLE_DATA_PATH", "d:/temp/test.csv")
            await sensor.asyncSetParam("NTF_ECG", "OFF")
            await sensor.asyncSetParam("NTF_IMU", "OFF")
            await sensor.asyncSetParam("FILTER_50HZ", "OFF")
            await sensor.asyncSetParam("FILTER_60HZ", "OFF")
            await sensor.asyncSetParam("FILTER_HPF", "OFF")
            await sensor.asyncSetParam("FILTER_LPF", "OFF")
            if not await sensor.asyncInit(PACKAGE_COUNT, POWER_REFRESH_PERIOD_IN_MS):
                print("init device: " + sensor.BLEDevice.Name + " failed")
                continue
            deviceInfo = sensor.getDeviceInfo()
            print("deviceInfo: Model: " + str(deviceInfo.ModelName))

        if sensor.hasInited:
            print("start data transfer")
            if not await sensor.asyncStartDataNotification():
                print("start data transfer with device: " + sensor.BLEDevice.Name + " failed")
                continue
    print("end")
    await asyncio.sleep(60)
    for sensor in SensorControllerInstance.getConnectedSensors():
        await sensor.asyncStopDataNotification()
    SensorControllerInstance.terminate()


def onDataCallback(sensor: SensorProfile, data: SensorData):
    # if (
    #     data.dataType == DataType.NTF_EEG
    #     or data.dataType == DataType.NTF_ECG
    #     or data.dataType == DataType.NTF_BRTH
    #     or data.dataType == DataType.NTF_ACC
    #     or data.dataType == DataType.NTF_GYRO
    # ):
    # print("got data from sensor: " + sensor.BLEDevice.Name + " data type: " + str(data.dataType))
    # print(str(data.channelSamples[0][0].sampleIndex))

    if data.channelSamples[0][0].sampleIndex == 1000:
        print("do stopDataNotification")
        sensor.stopDataNotification()

    if data.dataType == DataType.NTF_EEG:
        print(str(data.channelSamples[0][0].sampleIndex))
        # for sample in data.channelSamples[0]:
        #     print(sample.data)
    pass


def onPowerChanged(sensor: SensorProfile, power: int):
    print("connected sensor: " + sensor.BLEDevice.Name + " power: " + str(power))
    if not sensor.isDataTransfering:
        print("do disconnect")
        sensor.disconnect()


def onStateChanged(sensor: SensorProfile, newstate: DeviceStateEx):
    print("device: " + sensor.BLEDevice.Name + str(newstate))


def onErrorCallback(sensor: SensorProfile, reason: str):
    print("device: " + sensor.BLEDevice.Name + reason)
    pass


if __name__ == "__main__":
    asyncio.run(main())
