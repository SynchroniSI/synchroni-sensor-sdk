# synchroni-sensor-sdk

Python SDK for Synchroni BLE sensor devices (EEG, EMG, ECG, IMU, and more).

**Requirements:** Python 3.10+, Bluetooth enabled.

## Install

```sh
pip install synchroni-sensor-sdk
# Optional multi-adapter (dedicated USB HCI dongles):
pip install "synchroni-sensor-sdk[managed-usb]"
```

From source:

```sh
git clone https://github.com/SynchroniSI/synchroni-sensor-sdk.git
cd synchroni-sensor-sdk
poetry install
# with Bumble for multi-adapter:
poetry install --extras managed-usb
```

## Multi-adapter (optional)

Route each sensor through a dedicated USB Bluetooth dongle (Bumble/libusb) with
`SensorHub(enable_multi_adapter=True)`. This **isolates radios**; it does **not**
provide hardware sample-clock sync.

Full workflows (inventory, Windows claim, single- and multi-dongle scan/connect,
session claims, firmware env vars): **[docs/APIv2.md — Multi-adapter](docs/APIv2.md#multi-adapter-experimental)**.

### Quick workflow

```python
from synchroni_sensor_sdk import SensorHub

with SensorHub(enable_multi_adapter=True) as hub:
    # Windows: bind known EEG dongles to WinUSB once if claim_required
    for a in hub.list_bluetooth_adapters():
        if a.claim_required:
            hub.claim_adapter(a.id)

    # Discover on free managed dongles (or use scan(adapter_id=…))
    devices = hub.scan_managed_usb(timeout_ms=2000)
    if not devices:
        raise RuntimeError("No sensors on managed radios")

    d = max(devices, key=lambda x: x.rssi)
    sensor = hub.connect(d.mac_address, adapter_id=d.adapter_id)
    sensor.init(package_sample_count=10, power_refresh_interval=5000)
    sensor.start_streaming()
    # disconnect tears down GATT only; hub.close() (context exit) frees dongles
```

### CLI smoke tests

```sh
poetry install --extras managed-usb
poetry run python examples/v2_cli.py list-adapters
poetry run python examples/v2_cli.py scan --adapter-id usb:…
poetry run python examples/v2_cli.py connect --adapter-id usb:… --stream-seconds 5
poetry run python examples/v2_cli.py collect --adapter-id usb:… -o ./session.csv
poetry run python examples/v2_cli.py clean -y
```

Useful env vars: `SYNCHRONI_WINUSB_INSTALLER`, `SYNCHRONI_SDK_ASSETS_MANIFEST_URL`,
`SYNCHRONI_RTK_FIRMWARE_AUTO=0` (disable Realtek host firmware auto-download).

## Quick start (v2)

Scan, connect, stream EEG, and print samples:

```python
from synchroni_sensor_sdk import SensorHub
from synchroni_sensor_sdk.core.data import NtfDataType
from synchroni_sensor_sdk.core.device import SetParamCommand

with SensorHub() as hub:
    devices = hub.scan(timeout_ms=5000)
    if not devices:
        raise Exception("No devices found")

    sensor = hub.connect(devices[0].mac_address)
    sensor.set_param(SetParamCommand(
        enable_ntf_ecg=False,
        enable_ntf_imu=False,
        enable_filter_50hz=False,
        enable_filter_60hz=False,
        enable_filter_hpf=False,
        enable_filter_lpf=False,
    ))
    sensor.init(package_sample_count=10, power_refresh_interval=5000)

    def on_data(data):
        if data.data_type == NtfDataType.NTF_EEG and data.channel_samples:
            for sample in data.channel_samples[0]:
                print(sample.data)

    sensor.register_data_callback(on_data)
    sensor.start_streaming()

    input("Press Enter to stop...")
    sensor.stop_streaming()
```

Async API — same flow with `await` and `async with`:

```python
from synchroni_sensor_sdk.async_api import SensorHub

async with SensorHub() as hub:
    devices = await hub.scan(timeout_ms=5000)
    sensor = await hub.connect(devices[0].mac_address)
    # await sensor.init(...), await sensor.start_streaming(), etc.
```

See [examples/v2_console.py](examples/v2_console.py) and [examples/v2_async_console.py](examples/v2_async_console.py) for full demos.

## Development CLI

For local smoke tests, [examples/v2_cli.py](examples/v2_cli.py) is a small Typer CLI (Typer is a **dev** dependency: `poetry install`).

```sh
poetry install --extras managed-usb   # needed for multi-adapter inventory / USB radios
poetry run python examples/v2_cli.py --help
```

| Command | Description |
|---------|-------------|
| `list-adapters` | List host Bluetooth adapters (system + managed USB inventory) |
| `scan` | Scan for nearby sensors |
| `connect` | Scan/connect (default radio: `system:default`); stream briefly; print sample stats |
| `collect` | Like `connect`, but write all samples to a CSV file |
| `clean` | Wipe WinUSB installer cache, RTK host firmware cache, and pin firmware blobs |

Commands live under `examples/cli/` (`app.py`, display helpers, connect/collect session). Launch with
`examples/v2_cli.py`.

```sh
# List adapters (capability notes + id / source / claim flags)
poetry run python examples/v2_cli.py list-adapters

# System BLE scan (3s default)
poetry run python examples/v2_cli.py scan

# Longer scan and/or a specific radio
poetry run python examples/v2_cli.py scan --timeout-ms 5000
poetry run python examples/v2_cli.py scan --adapter-id system:default
poetry run python examples/v2_cli.py scan --adapter-id usb:…

# Connect + short stream (default radio is system:default; strongest RSSI unless --mac)
poetry run python examples/v2_cli.py connect
poetry run python examples/v2_cli.py connect --adapter-id usb:… --stream-seconds 3
poetry run python examples/v2_cli.py connect --mac AA:BB:CC:DD:EE:FF

# Collect samples to CSV (default filename in cwd; override with -o)
poetry run python examples/v2_cli.py collect --stream-seconds 10
poetry run python examples/v2_cli.py collect --adapter-id usb:… -o ./session.csv

# Clear cached installers / host firmware (skips prompt with -y)
poetry run python examples/v2_cli.py clean -y
```

All commands use `SensorHub(enable_multi_adapter=True)`. Managed-USB options require the
`managed-usb` extra (Bumble).

## Concurrency

### Sync API

The sync API runs BLE I/O on a **background event loop** thread (`EventLoopRunner`). Your main thread only blocks while waiting for hub/sensor method calls to finish (`scan`, `connect`, `init`, etc.).

**Callbacks (data, power, state, error)**

| | Detail |
|---|--------|
| Thread | Sync callbacks run on a **thread-pool worker** via `asyncio.to_thread` — not the main thread and not the BLE loop thread itself |
| Main thread | **Not blocked** by callbacks while it is free (e.g. sleeping or in your own loop) |
| Ordering | Dispatch for a given callback type is sequential: each invocation is awaited before the next |
| Throughput | Slow handlers can cause the drop-oldest data buffer (default 64 packets) to discard data; that is buffer pressure, not main-thread blocking |

**Do not call blocking hub/sensor methods from a sync callback** (`connect`, `disconnect`, `start_streaming`, `stop_streaming`, etc.). Those schedule work on the same background loop the dispatch task is waiting on and will **deadlock**.

The continuous-scan **device-found** callback runs on the **hub loop thread**, so the same rule applies: only do lightweight work, or enqueue work for the main thread.

Pattern used in [examples/v2_console.py](examples/v2_console.py): queue reconnect/restart work from callbacks and process it on the main thread.

### Async API

All I/O is `await`ed on the caller's event loop. Prefer **async** callbacks (`async def`) so work can run without leaving the loop. If you register a sync callback, it still uses `asyncio.to_thread` and follows the same “no blocking hub calls from the callback” rule if you mix patterns incorrectly—prefer async handlers that `await` hub/sensor methods.

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/APIv2.md](docs/APIv2.md) | v2 API reference (`synchroni_sensor_sdk`), including multi-adapter workflows |
| [docs/API.md](docs/API.md) | Legacy API reference (`sensor` package) |
| [examples/](examples/) | Console demos, GUI, and [v2_cli.py](examples/v2_cli.py) Typer smoke-test CLI |


The legacy `sensor` package remains available for existing integrations. New projects should use `synchroni_sensor_sdk`.

If the key is not supported, the result starts with `"Error"`.
