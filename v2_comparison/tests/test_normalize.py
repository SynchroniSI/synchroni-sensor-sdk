from synchroni_sensor_sdk.core.data import NtfDataType, Sample, SensorData
from v2_comparison.normalize import data_type_name, normalize_legacy, normalize_v2


class _LegacySample:
    def __init__(self) -> None:
        self.rawData = 10
        self.data = 20
        self.impedance = 0
        self.saturation = 0.0
        self.sampleIndex = 42
        self.isLost = False
        self.timeStampInMs = 100
        self.channelIndex = 1


class _LegacySensorData:
    def __init__(self) -> None:
        self.deviceMac = "AA:BB:CC:DD:EE:FF"
        self.dataType = int(NtfDataType.NTF_ECG)
        self.sampleRate = 500
        self.channelCount = 1
        self.packageSampleCount = 8
        self.lastPackageIndex = 3
        self.lastPackageCounter = 7
        self.channelSamples = [[_LegacySample()]]


def test_normalize_v2_sample_row() -> None:
    sample = Sample(
        raw_data=1,
        data=2,
        impedance=0,
        saturation=0.0,
        sample_index=5,
        is_lost=False,
        timestamp_ms=50,
        channel_index=0,
    )
    packet = SensorData(
        device_mac="11:22:33:44:55:66",
        data_type=NtfDataType.NTF_ECG,
        sample_rate=500,
        channel_count=1,
        package_sample_count=8,
        package_index_length=2,
        channel_samples=[[sample]],
        last_package_counter=1,
        last_package_index=2,
        resolution_bits=8,
        channel_mask=255,
        min_package_sample_count=8,
        K=1.0,
    )
    rows = normalize_v2(packet)
    assert len(rows) == 1
    row = rows[0]
    assert row.impl == "v2"
    assert row.data_type == int(NtfDataType.NTF_ECG)
    assert row.sample_index == 5
    assert row.data == 2
    assert row.package_index == 2


def test_normalize_legacy_sample_row() -> None:
    rows = normalize_legacy(_LegacySensorData())
    assert len(rows) == 1
    row = rows[0]
    assert row.impl == "legacy"
    assert row.channel_index == 1
    assert row.sample_index == 42
    assert row.data == 20


def test_data_type_name() -> None:
    assert data_type_name(int(NtfDataType.NTF_ECG)) == "NTF_ECG"
