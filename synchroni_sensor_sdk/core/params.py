"""Legacy setParam / data-context toggle key names (firmware protocol strings)."""

from enum import StrEnum


class ParamToggle(StrEnum):
    """ON/OFF values for notification and filter toggles."""

    ON = "ON"
    OFF = "OFF"

    @classmethod
    def from_bool(cls, enabled: bool) -> "ParamToggle":
        return cls.ON if enabled else cls.OFF

    def as_bool(self) -> bool:
        return self == ParamToggle.ON


class NtfParam(StrEnum):
    """Notification stream toggles stored in ``DataContext.init_map``."""

    NTF_EMG = "NTF_EMG"
    NTF_EEG = "NTF_EEG"
    NTF_ECG = "NTF_ECG"
    NTF_IMU = "NTF_IMU"
    NTF_BRTH = "NTF_BRTH"
    NTF_MAG_ANGLE = "NTF_MAG_ANGLE"
    NTF_IMPEDANCE = "NTF_IMPEDANCE"
    NTF_GEST = "NTF_GEST"
    NTF_PPG = "NTF_PPG"
    NTF_SPO2 = "NTF_SPO2"
    NTF_GFORCE_ACC = "NTF_GFORCE_ACC"
    NTF_GFORCE_GYRO = "NTF_GFORCE_GYRO"
    NTF_GFORCE_EULER = "NTF_GFORCE_EULER"
    NTF_GFORCE_QUAT = "NTF_GFORCE_QUAT"


# IMU sub-keys aggregated under NTF_IMU.
IMU_SUB_PARAMS: tuple[NtfParam, ...] = (
    NtfParam.NTF_GFORCE_ACC,
    NtfParam.NTF_GFORCE_GYRO,
    NtfParam.NTF_GFORCE_EULER,
    NtfParam.NTF_GFORCE_QUAT,
)


class FilterParam(StrEnum):
    """Firmware filter toggles stored in ``DataContext.filter_map``."""

    FILTER_50HZ = "FILTER_50HZ"
    FILTER_60HZ = "FILTER_60HZ"
    FILTER_HPF = "FILTER_HPF"
    FILTER_LPF = "FILTER_LPF"

    @property
    def firmware_switch_bit(self) -> int:
        return _FILTER_FIRMWARE_BITS[self]


_FILTER_FIRMWARE_BITS: dict[FilterParam, int] = {
    FilterParam.FILTER_50HZ: 1,
    FilterParam.FILTER_60HZ: 2,
    FilterParam.FILTER_HPF: 4,
    FilterParam.FILTER_LPF: 8,
}

DEFAULT_NTF_PARAMS: dict[NtfParam, ParamToggle] = dict.fromkeys(NtfParam, ParamToggle.ON)
DEFAULT_FILTER_PARAMS: dict[FilterParam, ParamToggle] = dict.fromkeys(FilterParam, ParamToggle.ON)
