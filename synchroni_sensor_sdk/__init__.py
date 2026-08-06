"""Synchroni sensor SDK v2."""

import os

from synchroni_sensor_sdk.async_api.driver.gforce.bleak_no_ack_patch import (
    apply as _apply_bleak_no_ack_patch,
)
from synchroni_sensor_sdk.async_api.driver.gforce.winrt_high_throughput import (
    apply as _apply_winrt_high_throughput_patch,
)
from synchroni_sensor_sdk.async_api.driver.managed_usb.winusb_installer import (
    clear_winusb_installer_cache,
    ensure_winusb_installer,
    winusb_installer_cache_dir,
)
from synchroni_sensor_sdk.async_api.sensor_hub import ScanResult
from synchroni_sensor_sdk.async_api.sensor_hub import SensorHub as AsyncSensorHub
from synchroni_sensor_sdk.core.bluetooth import (
    SYSTEM_DEFAULT_ADAPTER_ID,
    BluetoothAdapter,
    BluetoothCapability,
    ClaimResult,
    SensorRoute,
)
from synchroni_sensor_sdk.core.exceptions import (
    AdapterFirmwareError,
    BluetoothAdapterBusyError,
    BluetoothAdapterClaimRequiredError,
    BluetoothAdapterNotFoundError,
    ClaimFailedError,
    DataContextInitError,
    DataContextInitInProgressError,
    DataContextNotTransferringError,
    DataContextReadSamplesError,
    DataContextStopStreamingError,
    DataNotificationInProgressError,
    InvalidDeviceServiceError,
    ManagedUsbUnavailableError,
    MultiAdapterDisabledError,
    SensorError,
    SensorNotConnectedError,
    SensorNotInitializedError,
    SensorNotReadyError,
    SensorTerminatedError,
    StartDataNotificationError,
    StopDataNotificationError,
    WindowsClaimUnavailableError,
)
from synchroni_sensor_sdk.core.logging_config import configure_logging
from synchroni_sensor_sdk.sync_api.sensor_hub import SensorHub, sensor_hub

# Windows: prefer high-throughput BLE connection parameters when available.
_apply_winrt_high_throughput_patch()

# Optional: force write-without-response on known command characteristic UUIDs.
if os.environ.get("SENSOR_SDK_FORCE_NO_ACK", "0") == "1":
    _apply_bleak_no_ack_patch()

__all__ = [
    "AdapterFirmwareError",
    "AsyncSensorHub",
    "BluetoothAdapter",
    "BluetoothAdapterBusyError",
    "BluetoothAdapterClaimRequiredError",
    "BluetoothAdapterNotFoundError",
    "BluetoothCapability",
    "ClaimFailedError",
    "ClaimResult",
    "clear_winusb_installer_cache",
    "DataContextInitError",
    "DataContextInitInProgressError",
    "DataContextNotTransferringError",
    "DataContextReadSamplesError",
    "DataContextStopStreamingError",
    "DataNotificationInProgressError",
    "ensure_winusb_installer",
    "InvalidDeviceServiceError",
    "ManagedUsbUnavailableError",
    "MultiAdapterDisabledError",
    "SYSTEM_DEFAULT_ADAPTER_ID",
    "ScanResult",
    "SensorError",
    "SensorHub",
    "SensorNotConnectedError",
    "SensorNotInitializedError",
    "SensorNotReadyError",
    "SensorRoute",
    "SensorTerminatedError",
    "StartDataNotificationError",
    "StopDataNotificationError",
    "WindowsClaimUnavailableError",
    "configure_logging",
    "sensor_hub",
    "winusb_installer_cache_dir",
]
