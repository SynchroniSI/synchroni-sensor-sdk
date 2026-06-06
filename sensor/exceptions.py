"""Exception types for the Synchroni sensor SDK."""


class SensorError(Exception):
    """Base exception for all sensor SDK errors."""


# Connection and lifecycle


class SensorTerminatedError(SensorError):
    """Sensor utilities have been terminated."""


class SensorNotConnectedError(SensorError):
    """Device is not connected or data context is not set."""


class SensorNotReadyError(SensorError):
    """Device is not in Ready state for this operation."""


class SensorNotInitializedError(SensorError):
    """Sensor or data context has not been initialized (e.g. init() not called)."""


class InvalidDeviceServiceError(SensorError):
    """Device advertisement does not match a supported service UUID."""


# Data notification


class DataNotificationInProgressError(SensorError):
    """A start or stop data notification operation is already in progress."""


class StartDataNotificationError(SensorError):
    """Failed to start data notification."""


class StopDataNotificationError(SensorError):
    """Failed to stop data notification."""


# Data context


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
