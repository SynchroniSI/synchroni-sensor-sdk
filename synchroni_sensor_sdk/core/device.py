from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class NeuCirAppControl(StrEnum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    STOP = "STOP"


class NeuCirMode(StrEnum):
    APP_REMOTE = "APP_REMOTE"


class BleChipType(IntEnum):
    """BLE chip / protocol family detected at connect."""

    UNKNOWN = -1
    OYM = 0
    RFSTAR = 1


@dataclass
class DeviceInfo:
    """Static capabilities reported by a sensor after init."""

    model: str
    hardware_version: str
    firmware_version: str
    channel_counts: dict[str, int]
    name: str = ""
    sample_rates: dict[str, int] = field(default_factory=dict)
    mtu_size: int = 0


@dataclass
class DeviceParams:
    """Snapshot of notification, filter, and debug parameter state."""

    ntf: dict[str, bool]
    filters: dict[str, bool]
    debug_ble_data_path: str | None = None


@dataclass
class SetParamCommand:
    """
    Batch of optional parameter changes for a sensor.

    Fields left as ``None`` are not applied. Maps to legacy ``setParam(key, value)`` as:

    - ``enable_ntf_*`` → :class:`~synchroni_sensor_sdk.core.params.NtfParam` with
      :class:`~synchroni_sensor_sdk.core.params.ParamToggle`
    - ``enable_filter_*`` → :class:`~synchroni_sensor_sdk.core.params.FilterParam` with
      :class:`~synchroni_sensor_sdk.core.params.ParamToggle`
    - ``debug_ble_data_path`` → ``DEBUG_BLE_DATA_PATH`` (absolute file path)
    - ``neucir_mode`` → ``NEUCIR_SET_MODE``
    - ``neucir_app_control`` → ``NEUCIR_APP_CONTROL``

    ``enable_ntf_imu`` sets or clears ACC/GYRO/EULER/QUAT together (master switch).
    """

    enable_ntf_emg: bool | None = None
    enable_ntf_eeg: bool | None = None
    enable_ntf_ecg: bool | None = None
    enable_ntf_imu: bool | None = None
    enable_ntf_brth: bool | None = None
    enable_ntf_impedance: bool | None = None
    enable_ntf_mag_angle: bool | None = None
    enable_ntf_gest: bool | None = None
    enable_ntf_ppg: bool | None = None
    enable_ntf_spo2: bool | None = None
    enable_ntf_acc: bool | None = None
    enable_ntf_gyro: bool | None = None
    enable_ntf_euler: bool | None = None
    enable_ntf_quat: bool | None = None

    enable_filter_50hz: bool | None = None
    enable_filter_60hz: bool | None = None
    enable_filter_hpf: bool | None = None
    enable_filter_lpf: bool | None = None

    debug_ble_data_path: str | None = None

    neucir_mode: NeuCirMode | None = None
    neucir_app_control: NeuCirAppControl | None = None


class DeviceState(IntEnum):
    """
    DeviceState represents the state of a device.
    """

    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    READY = 3
    DISCONNECTING = 4
    INVALID = 5


class SensorDataType(IntEnum):
    """Internal modality indices (legacy data-context layout).

    Prefer :class:`~synchroni_sensor_sdk.core.data.NtfDataType` in public code.
    """

    DATA_TYPE_EEG = 0
    DATA_TYPE_ECG = 1
    DATA_TYPE_ACC = 2
    DATA_TYPE_GYRO = 3
    DATA_TYPE_BRTH = 4
    DATA_TYPE_EMG = 5
    DATA_TYPE_MAG_ANGLE = 6
    DATA_TYPE_QUATERNION = 7
    DATA_TYPE_PPG = 8
    DATA_TYPE_SPO2 = 9
    DATA_TYPE_EULER = 10
    DATA_TYPE_GFORCE_QUAT = 11
    DATA_TYPE_IMPEDANCE = 12
    DATA_TYPE_GEST = 13
    DATA_TYPE_COUNT = 14
