from dataclasses import dataclass, field, replace
from enum import IntEnum, StrEnum


@dataclass(frozen=True)
class SignalAcquisitionMode:
    """One profile-declared sampling-rate and ADC-resolution pairing."""

    sample_rate_hz: int
    adc_resolution_bits: int | None = None


@dataclass(frozen=True)
class ProductSpecification:
    """Technical product capabilities, distinct from negotiated runtime values."""

    emg_channel_count: int | None = None
    adc_resolution_bits: int | None = None
    imu_axis_count: int | None = None
    eeg_channel_count: int | None = None
    ecg_channel_count: int | None = None
    breathing_channel_count: int | None = None
    eeg_acquisition_modes: tuple[SignalAcquisitionMode, ...] = ()
    emg_acquisition_modes: tuple[SignalAcquisitionMode, ...] = ()
    breathing_acquisition_modes: tuple[SignalAcquisitionMode, ...] = ()


@dataclass(frozen=True)
class NativeDeviceProfile:
    """Stable SDK identity and conservative capabilities for a product family.

    The profile is selected from the BLE-advertised name and the model number
    returned after initialization. Runtime ``DeviceInfo`` values remain the
    authority for the exact streams, channel counts, and rates of a connected
    unit.
    """

    profile_id: str
    label: str
    name_markers: tuple[str, ...]
    supported_streams: frozenset[str]
    supported_filters: frozenset[str] = frozenset()
    configurable_eeg_sample_rates_hz: tuple[int, ...] = ()
    configurable_emg_sample_rates_hz: tuple[int, ...] = ()
    legacy_profile_ids: tuple[str, ...] = ()
    product_specification: ProductSpecification | None = None


_EEG_250_24 = (SignalAcquisitionMode(250, 24),)
_EEG_250_500_24 = (*_EEG_250_24, SignalAcquisitionMode(500, 24))
_EMG_500_12_1000_8 = (SignalAcquisitionMode(500, 12), SignalAcquisitionMode(1_000, 8))


ORION_PRODUCT_SPECIFICATION = ProductSpecification(
    adc_resolution_bits=24,
    imu_axis_count=6,
)


def _orion_variant(channels: int, *, supports_500_hz: bool) -> ProductSpecification:
    return replace(
        ORION_PRODUCT_SPECIFICATION,
        eeg_channel_count=channels,
        eeg_acquisition_modes=_EEG_250_500_24 if supports_500_hz else _EEG_250_24,
    )


ORION_A_PRODUCT_SPECIFICATION = _orion_variant(16, supports_500_hz=True)
ORION_B_PRODUCT_SPECIFICATION = _orion_variant(24, supports_500_hz=True)
ORION_C_PRODUCT_SPECIFICATION = _orion_variant(32, supports_500_hz=False)

_ORION_PRODUCT_SPECIFICATIONS_BY_VARIANT = {
    "a": ORION_A_PRODUCT_SPECIFICATION,
    "b": ORION_B_PRODUCT_SPECIFICATION,
    "c": ORION_C_PRODUCT_SPECIFICATION,
}
_ORION_PRODUCT_SPECIFICATIONS_BY_CHANNEL_COUNT = {
    specification.eeg_channel_count: specification
    for specification in _ORION_PRODUCT_SPECIFICATIONS_BY_VARIANT.values()
}

NURA_PRODUCT_SPECIFICATION = ProductSpecification(
    adc_resolution_bits=24,
    imu_axis_count=6,
    eeg_acquisition_modes=_EEG_250_24,
)


def _nura_variant(eeg_channels: int, ecg_channels: int) -> ProductSpecification:
    return replace(
        NURA_PRODUCT_SPECIFICATION,
        eeg_channel_count=eeg_channels,
        ecg_channel_count=ecg_channels,
    )


NURA_UNO_PRODUCT_SPECIFICATION = _nura_variant(1, 0)
NURA_TRIO_PRODUCT_SPECIFICATION = _nura_variant(2, 1)
NURA_PENTO_PRODUCT_SPECIFICATION = _nura_variant(4, 1)
NURA_OCTO_PRODUCT_SPECIFICATION = _nura_variant(7, 1)
NURA_NEO_PRODUCT_SPECIFICATION = _nura_variant(8, 1)
OB3000_PRODUCT_SPECIFICATION = ProductSpecification(
    eeg_channel_count=23,
    ecg_channel_count=1,
    imu_axis_count=6,
)

_NURA_PRODUCT_SPECIFICATIONS_BY_MARKER = (
    ("ob3000", OB3000_PRODUCT_SPECIFICATION),
    ("syncneo", NURA_NEO_PRODUCT_SPECIFICATION),
    ("ob5000", NURA_NEO_PRODUCT_SPECIFICATION),
    ("pento", NURA_PENTO_PRODUCT_SPECIFICATION),
    ("trio", NURA_TRIO_PRODUCT_SPECIFICATION),
    ("octo", NURA_OCTO_PRODUCT_SPECIFICATION),
    ("uno", NURA_UNO_PRODUCT_SPECIFICATION),
    ("neo", NURA_NEO_PRODUCT_SPECIFICATION),
)

BREATHE_PRODUCT_SPECIFICATION = ProductSpecification(
    breathing_channel_count=1,
    adc_resolution_bits=24,
    breathing_acquisition_modes=(SignalAcquisitionMode(250, 24),),
)

GFORCE_PRO_PRODUCT_SPECIFICATION = ProductSpecification(
    emg_channel_count=8,
    imu_axis_count=9,
    emg_acquisition_modes=_EMG_500_12_1000_8,
)

OYWW1000_PRODUCT_SPECIFICATION = ProductSpecification(
    emg_channel_count=8,
    adc_resolution_bits=24,
    imu_axis_count=6,
    emg_acquisition_modes=(SignalAcquisitionMode(1_000, 24),),
)


GFORCE_OCT_PRODUCT_SPECIFICATION = GFORCE_PRO_PRODUCT_SPECIFICATION


_ALL_FIRMWARE_FILTERS = frozenset(("50hz", "60hz", "hpf", "lpf"))

NATIVE_DEVICE_PROFILES: tuple[NativeDeviceProfile, ...] = (
    # Specific gForce identities must precede the gForcePro/Force fallback.
    NativeDeviceProfile(
        profile_id="force_ultra",
        label="OYMotion gForce Ultra (OYWW1000)",
        name_markers=("oyww1000", "oyww", "gforce ultra", "force ultra", "forceultra"),
        supported_streams=frozenset(("emg", "imu")),
        supported_filters=_ALL_FIRMWARE_FILTERS,
        configurable_emg_sample_rates_hz=(500, 1_000),
        legacy_profile_ids=("wristband",),
        product_specification=OYWW1000_PRODUCT_SPECIFICATION,
    ),
    NativeDeviceProfile(
        profile_id="force_oct",
        label="Synchroni Force Oct / OYMotion gForceOct",
        name_markers=("gforceoct", "gforce oct", "forceoct", "force oct"),
        supported_streams=frozenset(("emg", "imu")),
        configurable_emg_sample_rates_hz=(500, 1_000),
        product_specification=GFORCE_OCT_PRODUCT_SPECIFICATION,
    ),
    NativeDeviceProfile(
        profile_id="force",
        label="Synchroni Force / OYMotion gForcePro+",
        name_markers=(
            "gforcepro+",
            "gforcepro",
            "oym-gf-p001",
            "synchroni force",
            "force-",
            "force(",
        ),
        supported_streams=frozenset(("emg", "imu")),
        configurable_emg_sample_rates_hz=(500, 1_000),
        product_specification=GFORCE_PRO_PRODUCT_SPECIFICATION,
    ),
    NativeDeviceProfile(
        profile_id="orion",
        label="Synchroni Orion / OYMotion OB6000",
        name_markers=("orion", "ob6000", "ob6000a", "ob6000b", "ob6000c"),
        supported_streams=frozenset(("eeg", "imu", "impedance")),
        supported_filters=_ALL_FIRMWARE_FILTERS,
        configurable_eeg_sample_rates_hz=(250, 500),
        product_specification=ORION_PRODUCT_SPECIFICATION,
    ),
    NativeDeviceProfile(
        profile_id="nura",
        label="Synchroni Nura / OYMotion OB3000/OB5000",
        name_markers=(
            "ob3000",
            "ob5000",
            "sync-neo",
            "syncneo",
            "nura",
            "uno",
            "trio",
            "pento",
            "octo",
            "neo",
        ),
        supported_streams=frozenset(("eeg", "ecg", "imu", "impedance")),
        supported_filters=_ALL_FIRMWARE_FILTERS,
        product_specification=NURA_PRODUCT_SPECIFICATION,
    ),
    NativeDeviceProfile(
        profile_id="breathe",
        label="Synchroni Breathe (SyncBelt)",
        name_markers=("syncbelt", "sync-belt", "sync_belt", "breathe", "breath", "brth"),
        supported_streams=frozenset(("brth",)),
        supported_filters=_ALL_FIRMWARE_FILTERS,
        product_specification=BREATHE_PRODUCT_SPECIFICATION,
    ),
)

NATIVE_DEVICE_PROFILES_BY_ID = {profile.profile_id: profile for profile in NATIVE_DEVICE_PROFILES}
for _native_profile in NATIVE_DEVICE_PROFILES:
    for _legacy_profile_id in _native_profile.legacy_profile_ids:
        NATIVE_DEVICE_PROFILES_BY_ID[_legacy_profile_id] = _native_profile


def native_device_profile(*identities: str) -> NativeDeviceProfile | None:
    """Resolve a native product family from advertised and initialized identities."""
    normalized = " ".join(identities).strip().casefold()
    if not normalized:
        return None
    for profile in NATIVE_DEVICE_PROFILES:
        if any(_identity_matches_marker(normalized, marker) for marker in profile.name_markers):
            return profile
    return None


def _identity_matches_marker(normalized_identity: str, marker: str) -> bool:
    """Match BLE identity aliases without matching fragments of unrelated words."""
    start = normalized_identity.find(marker)
    while start >= 0:
        end = start + len(marker)
        starts_at_boundary = not marker[0].isalnum() or start == 0 or not normalized_identity[start - 1].isalnum()
        ends_at_boundary = (
            not marker[-1].isalnum() or end == len(normalized_identity) or not normalized_identity[end].isalnum()
        )
        if starts_at_boundary and ends_at_boundary:
            return True
        start = normalized_identity.find(marker, start + 1)
    return False


def native_device_profile_by_id(profile_id: str | None) -> NativeDeviceProfile | None:
    """Resolve a canonical or compatibility profile id."""
    if profile_id is None:
        return None
    return NATIVE_DEVICE_PROFILES_BY_ID.get(profile_id.strip().casefold())


def published_product_specification(
    *identities: str,
    eeg_channel_count: int | None = None,
) -> ProductSpecification | None:
    """Resolve technical variant capabilities without changing protocol behavior."""
    profile = native_device_profile(*identities)
    if profile is not None and profile.profile_id == "orion":
        channel_specification = _ORION_PRODUCT_SPECIFICATIONS_BY_CHANNEL_COUNT.get(eeg_channel_count)
        if channel_specification is not None:
            return channel_specification
        normalized_identity = " ".join(identities).strip().casefold()
        for variant, specification in _ORION_PRODUCT_SPECIFICATIONS_BY_VARIANT.items():
            channel_count = specification.eeg_channel_count
            variant_markers = (f"ob6000{variant}", f"orion {variant}", f"orion-{variant}")
            channel_markers = (
                ()
                if channel_count is None
                else (f"orion {channel_count}", f"orion-{channel_count}", f"orion{channel_count}")
            )
            if any(
                _identity_matches_marker(normalized_identity, marker) for marker in variant_markers + channel_markers
            ):
                return specification
    if profile is not None and profile.profile_id == "nura":
        normalized_identity = " ".join(identities).strip().casefold()
        for marker, specification in _NURA_PRODUCT_SPECIFICATIONS_BY_MARKER:
            if _identity_matches_marker(normalized_identity, marker):
                return specification
    return profile.product_specification if profile is not None else None


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
    supported_streams: frozenset[str] = field(default_factory=frozenset)
    supported_filters: frozenset[str] = field(default_factory=frozenset)
    product_specification: ProductSpecification | None = None


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

    ``enable_ntf_imu`` first sets ACC/GYRO/EULER/QUAT together. Explicit
    ``enable_ntf_acc`` / ``gyro`` / ``euler`` / ``quat`` values in the same
    command then override their corresponding sub-stream, which allows callers
    to request acceleration and gyro without enabling orientation streams.
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

    # New fields stay at the end to preserve the legacy positional constructor.
    eeg_sample_rate_hz: int | None = None
    emg_sample_rate_hz: int | None = None


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
