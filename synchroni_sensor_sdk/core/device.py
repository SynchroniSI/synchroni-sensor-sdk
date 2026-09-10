from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


@dataclass(frozen=True)
class SignalAcquisitionMode:
    """One manufacturer-published sampling-rate and ADC-resolution pairing."""

    sample_rate_hz: int
    adc_resolution_bits: int | None = None


@dataclass(frozen=True)
class ProductSpecification:
    """Manufacturer-published product facts, distinct from negotiated runtime values."""

    emg_channel_count: int | None = None
    adc_resolution_bits: int | None = None
    imu_axis_count: int | None = None
    eeg_channel_count: int | None = None
    ecg_channel_count: int | None = None
    breathing_channel_count: int | None = None
    eeg_acquisition_modes: tuple[SignalAcquisitionMode, ...] = ()
    emg_acquisition_modes: tuple[SignalAcquisitionMode, ...] = ()
    breathing_acquisition_modes: tuple[SignalAcquisitionMode, ...] = ()

    # Recorder metadata extends the upstream constructor without shifting its arguments.
    manufacturer: str = field(default="", kw_only=True)
    product_name: str = field(default="", kw_only=True)
    model_aliases: tuple[str, ...] = field(default=(), kw_only=True)
    nominal_emg_sample_rate_hz: int | None = field(default=None, kw_only=True)
    bluetooth_version: str | None = field(default=None, kw_only=True)
    nominal_battery_runtime_hours: float | None = field(default=None, kw_only=True)
    source_urls: tuple[str, ...] = field(default=(), kw_only=True)
    nominal_imu_sample_rate_hz: int | None = field(default=None, kw_only=True)
    notes: tuple[str, ...] = field(default=(), kw_only=True)


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


ORION_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer="Synchroni / OYMotion",
    product_name="Synchroni Orion EEG System",
    model_aliases=(
        "OB6000",
        "OB6000A",
        "OB6000B",
        "OB6000C",
        "Synchroni Orion",
        "Synchroni Orion A",
        "Synchroni Orion B",
        "Synchroni Orion C",
        "Orion-16",
        "Orion-24",
        "Orion-32",
    ),
    adc_resolution_bits=24,
    imu_axis_count=6,
    bluetooth_version="5.0",
    nominal_battery_runtime_hours=10.0,
    nominal_imu_sample_rate_hz=50,
    source_urls=("https://synchroni.co/products/hardware/orion",),
    notes=("OB6000A, OB6000B, and OB6000C are Recorder device-identity aliases.",),
)

ORION_A_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer=ORION_PRODUCT_SPECIFICATION.manufacturer,
    product_name="Orion-16",
    model_aliases=("Orion-16", "OB6000A", "Orion A", "Synchroni Orion A"),
    eeg_channel_count=16,
    adc_resolution_bits=24,
    imu_axis_count=6,
    bluetooth_version="5.0",
    nominal_battery_runtime_hours=10.0,
    eeg_acquisition_modes=(SignalAcquisitionMode(250, 24), SignalAcquisitionMode(500, 24)),
    nominal_imu_sample_rate_hz=50,
    notes=("OB6000A and Orion A are Recorder device-name aliases for the 16-channel variant.",),
    source_urls=ORION_PRODUCT_SPECIFICATION.source_urls,
)

ORION_B_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer=ORION_PRODUCT_SPECIFICATION.manufacturer,
    product_name="Orion-24",
    model_aliases=("Orion-24", "OB6000B", "Orion B", "Synchroni Orion B"),
    eeg_channel_count=24,
    adc_resolution_bits=24,
    imu_axis_count=6,
    bluetooth_version="5.0",
    nominal_battery_runtime_hours=10.0,
    eeg_acquisition_modes=(SignalAcquisitionMode(250, 24), SignalAcquisitionMode(500, 24)),
    nominal_imu_sample_rate_hz=50,
    notes=("OB6000B and Orion B are Recorder device-name aliases for the 24-channel variant.",),
    source_urls=ORION_PRODUCT_SPECIFICATION.source_urls,
)

ORION_C_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer=ORION_PRODUCT_SPECIFICATION.manufacturer,
    product_name="Orion-32",
    model_aliases=("Orion-32", "OB6000C", "Orion C", "Synchroni Orion C"),
    eeg_channel_count=32,
    adc_resolution_bits=24,
    imu_axis_count=6,
    bluetooth_version="5.0",
    nominal_battery_runtime_hours=10.0,
    eeg_acquisition_modes=(SignalAcquisitionMode(250, 24),),
    nominal_imu_sample_rate_hz=50,
    notes=("OB6000C and Orion C are Recorder device-name aliases for the 32-channel variant.",),
    source_urls=ORION_PRODUCT_SPECIFICATION.source_urls,
)

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
    manufacturer="Synchroni / OYMotion",
    product_name="Nura EEG & ECG System",
    model_aliases=(
        "OB5000",
        "Synchroni Nura",
        "Synchroni Uno",
        "Synchroni Trio",
        "Synchroni Pento",
        "Synchroni Octo",
        "Synchroni Neo",
        "Synchroni Sync-Neo",
    ),
    adc_resolution_bits=24,
    imu_axis_count=6,
    bluetooth_version="4.2",
    nominal_battery_runtime_hours=12.0,
    eeg_acquisition_modes=(SignalAcquisitionMode(250, 24),),
    nominal_imu_sample_rate_hz=50,
    source_urls=("https://synchroni.co/products/hardware/nura",),
    notes=("OB5000 and Sync-Neo identify the eight-channel Neo variant.",),
)


def _nura_variant(
    name: str,
    aliases: tuple[str, ...],
    eeg_channels: int,
    ecg_channels: int,
    *,
    notes: tuple[str, ...] = (),
    source_urls: tuple[str, ...] = (),
) -> ProductSpecification:
    return ProductSpecification(
        manufacturer=NURA_PRODUCT_SPECIFICATION.manufacturer,
        product_name=name,
        model_aliases=aliases,
        eeg_channel_count=eeg_channels,
        ecg_channel_count=ecg_channels,
        adc_resolution_bits=24,
        imu_axis_count=6,
        bluetooth_version="4.2",
        nominal_battery_runtime_hours=12.0,
        eeg_acquisition_modes=(SignalAcquisitionMode(250, 24),),
        nominal_imu_sample_rate_hz=50,
        notes=notes,
        source_urls=NURA_PRODUCT_SPECIFICATION.source_urls + source_urls,
    )


NURA_UNO_PRODUCT_SPECIFICATION = _nura_variant("Synchroni Uno", ("Synchroni Uno", "Uno"), 1, 0)
NURA_TRIO_PRODUCT_SPECIFICATION = _nura_variant("Synchroni Trio", ("Synchroni Trio", "Trio"), 2, 1)
NURA_PENTO_PRODUCT_SPECIFICATION = _nura_variant("Synchroni Pento", ("Synchroni Pento", "Pento"), 4, 1)
NURA_OCTO_PRODUCT_SPECIFICATION = _nura_variant("Synchroni Octo", ("Synchroni Octo", "Octo"), 7, 1)
NURA_NEO_PRODUCT_SPECIFICATION = _nura_variant(
    "Synchroni Neo / Sync-Neo",
    ("Synchroni Neo", "Synchroni Sync-Neo", "Sync-Neo", "Neo", "OB5000"),
    8,
    1,
    notes=("Neo's ECG input is optional and replaces one EEG lead when enabled.",),
    source_urls=(
        "https://www.oymotion.com/product58/203",
        "https://oymotion.com/product63",
    ),
)
OB3000_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer="OYMotion",
    product_name="OB3000 EEG System",
    model_aliases=("OB3000",),
    eeg_channel_count=23,
    ecg_channel_count=1,
    imu_axis_count=6,
    source_urls=("https://oymotion.com/product58/202",),
    notes=("OB3000 is not one of the 1/2/4/7/8-channel Synchroni Nura variants.",),
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
    manufacturer="Synchroni",
    product_name="Breathe Respiratory Sensor",
    model_aliases=("Synchroni Breathe", "Breathe", "SyncBelt"),
    breathing_channel_count=1,
    adc_resolution_bits=24,
    bluetooth_version="4.2",
    nominal_battery_runtime_hours=6.0,
    breathing_acquisition_modes=(SignalAcquisitionMode(250, 24),),
    source_urls=("https://synchroni.co/products/hardware/breathe",),
    notes=("SyncBelt is a Recorder BLE-name alias.",),
)

GFORCE_PRO_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer="Synchroni / OYMotion",
    product_name="Synchroni Force / gForcePro+",
    model_aliases=("gForcePro", "gForcePro+", "OYM-GF-P001", "Synchroni Force"),
    emg_channel_count=8,
    nominal_emg_sample_rate_hz=1_000,
    imu_axis_count=9,
    bluetooth_version="4.0 or 4.2 by model revision",
    nominal_battery_runtime_hours=6.0,
    emg_acquisition_modes=(SignalAcquisitionMode(500, 12), SignalAcquisitionMode(1_000, 8)),
    nominal_imu_sample_rate_hz=50,
    source_urls=(
        "https://synchroni.co/products/hardware/force",
        "https://www.oymotion.com/en/product32/149",
    ),
    notes=("OYM-GF-P001 is a Recorder model-identity alias.",),
)

OYWW1000_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer="OYMotion",
    product_name="gForce Ultra",
    model_aliases=("OYWW1000", "gForce Ultra"),
    emg_channel_count=8,
    nominal_emg_sample_rate_hz=1000,
    adc_resolution_bits=24,
    imu_axis_count=6,
    bluetooth_version="4.2",
    nominal_battery_runtime_hours=5.0,
    emg_acquisition_modes=(SignalAcquisitionMode(1_000, 24),),
    source_urls=(
        "https://www.oymotion.com/en/product32/215",
        "https://oymotion.com/product17/206",
        "https://www.oymotion.com/en/news37/489",
    ),
    notes=("OYMotion publishes 1000 Hz at 24-bit; 500 Hz is retained as an SDK/device-negotiated mode.",),
)


GFORCE_OCT_PRODUCT_SPECIFICATION = ProductSpecification(
    manufacturer="OYMotion",
    product_name="gForceOct",
    model_aliases=("gForceOct", "Synchroni Force Oct"),
    emg_channel_count=8,
    nominal_emg_sample_rate_hz=1_000,
    imu_axis_count=9,
    bluetooth_version="4.2",
    emg_acquisition_modes=(SignalAcquisitionMode(500, 12), SignalAcquisitionMode(1_000, 8)),
    source_urls=("https://www.oymotion.com/en/product32/148",),
    notes=("Synchroni Force Oct is Recorder's name for the gForceOct family.",),
)


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
    """Resolve manufacturer facts without changing discovery or protocol behavior."""
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
