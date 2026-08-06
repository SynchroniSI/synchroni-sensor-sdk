"""Exception types for the Synchroni sensor SDK v2."""

from __future__ import annotations

__all__ = [
    "AdapterFirmwareError",
    "BluetoothAdapterBusyError",
    "BluetoothAdapterClaimRequiredError",
    "BluetoothAdapterNotFoundError",
    "ClaimFailedError",
    "DataContextInitError",
    "DataContextInitInProgressError",
    "DataContextNotTransferringError",
    "DataContextReadSamplesError",
    "DataContextStopStreamingError",
    "DataNotificationInProgressError",
    "InvalidDeviceServiceError",
    "ManagedUsbUnavailableError",
    "MultiAdapterDisabledError",
    "SensorError",
    "SensorNotConnectedError",
    "SensorNotInitializedError",
    "SensorNotReadyError",
    "SensorTerminatedError",
    "StartDataNotificationError",
    "StopDataNotificationError",
    "WindowsClaimUnavailableError",
]


class SensorError(Exception):
    """Base exception for all sensor SDK errors."""


class SensorTerminatedError(SensorError):
    """Sensor utilities or hub have been terminated."""


class SensorNotConnectedError(SensorError):
    """Device is not connected or data context is not set."""


class SensorNotReadyError(SensorError):
    """Device is not in Ready state for this operation."""


class SensorNotInitializedError(SensorError):
    """Sensor or data context has not been initialized (e.g. init() not called)."""


class InvalidDeviceServiceError(SensorError):
    """Device advertisement does not match a supported service UUID."""


class DataNotificationInProgressError(SensorError):
    """A start or stop data notification operation is already in progress."""


class StartDataNotificationError(SensorError):
    """Failed to start data notification."""


class StopDataNotificationError(SensorError):
    """Failed to stop data notification."""


class DataContextInitInProgressError(SensorError):
    """Data context initialization is already in progress."""


class DataContextInitError(SensorError):
    """Data context initialization failed."""


class DataContextStopStreamingError(SensorError):
    """Failed to stop streaming."""


class DataContextNotTransferringError(SensorError):
    """Operation requires an active data transfer (e.g. streaming was stopped)."""


class DataContextReadSamplesError(SensorError):
    """Error while reading or processing samples."""


class MultiAdapterDisabledError(SensorError):
    """Multi-adapter API used on a hub constructed with ``enable_multi_adapter=False``.

    Construct :class:`~synchroni_sensor_sdk.async_api.sensor_hub.SensorHub` with
    ``enable_multi_adapter=True`` (and optional ``managed-usb`` extras) to use
    dedicated dongle inventory, routes, and claim APIs.
    """


class ManagedUsbUnavailableError(SensorError):
    """Bumble/libusb managed-USB stack is not available on this install or platform.

    Install the optional extra (e.g. ``pip install synchroni-sensor-sdk[managed-usb]``)
    and ensure the host OS allows libusb access to the HCI dongle.
    """


class WindowsClaimUnavailableError(SensorError):
    """WinUSB claim helper is missing or this platform does not support claims.

    Provide ``winusb_installer_path``, set ``SYNCHRONI_WINUSB_INSTALLER`` to a
    local ``synchroni-winusb-installer.exe`` / ``winusb-installer.exe``, or allow
    the SDK to download the helper from the public assets manifest into the
    user cache (see ``clear_winusb_installer_cache``).
    """


class BluetoothAdapterNotFoundError(SensorError):
    """Requested adapter id is unknown or no longer present in inventory."""


class BluetoothAdapterBusyError(SensorError):
    """Adapter is already reserved or connected for another sensor."""


class BluetoothAdapterClaimRequiredError(SensorError):
    """Adapter still needs a WinUSB/userspace claim before managed USB use."""


class ClaimFailedError(SensorError):
    """WinUSB claim / driver install finished unsuccessfully."""


class AdapterFirmwareError(SensorError):
    """Mapped dongle firmware pin is missing or failed size/SHA-256 verification."""
