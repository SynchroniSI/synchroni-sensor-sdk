# API Reference (v2)

Overview of the `synchroni_sensor_sdk` package. For the legacy API, see [API.md](./API.md).

Goals relative to the legacy package:
1. Clean concurrency (async + sync wrappers over one driver stack).
2. Typed public API (`SetParamCommand`, dataclasses) instead of string `setParam`/`getParam`.
3. In-process Bleak on an asyncio loop (no multiprocess BLE host).

---

## Sync and async

| | Sync | Async |
|---|------|-------|
| Import | `from synchroni_sensor_sdk import SensorHub` | `from synchroni_sensor_sdk.async_api import SensorHub` |
| Hub teardown | `with SensorHub()` or `hub.close()` | `async with SensorHub()` or `await hub.close()` |
| Singleton | `from synchroni_sensor_sdk import sensor_hub` (optional) | — |

Typical lifecycle:

```
scan → connect → set_param (optional) → init → register_*_callback → start_streaming → stop_streaming → disconnect → close
```

See [README.md](../README.md) concurrency notes: sync callbacks run via `asyncio.to_thread`; do not call blocking hub/sensor methods from those callbacks.

---

## Package exports

| Name | Module | Description |
|------|--------|-------------|
| `SensorHub`, `sensor_hub` | `synchroni_sensor_sdk` / `sync_api` | Sync hub |
| `Sensor` | `sync_api` | Sync per-device facade |
| `SensorHub`, `Sensor`, `ScanResult` | `async_api` | Async equivalents |
| Exception taxonomy | package root + `core.exceptions` | Full hierarchy (see below) |
| `DeviceInfo`, `SetParamCommand`, `DeviceParams`, `BleChipType`, `DeviceState` | `core.device` | Shared types |
| `SensorData`, `Sample`, `NtfDataType` | `core.data` | Streaming payloads |

---

## SensorHub

| Method | Returns | Description |
|--------|---------|-------------|
| Constructor `enable_multi_adapter=False` | hub | Gate multi-dongle APIs (see [Multi-adapter](#multi-adapter-experimental)) |
| `scan(timeout_ms, *, adapter_id=None, adapter_ids=None)` | `list[ScanResult]` | One-shot BLE scan (system or managed USB) |
| `scan_managed_usb(timeout_ms)` | `list[ScanResult]` | Multi-adapter only: scan free USB dongles with routes |
| `start_scan` / `stop_scan` / `is_scanning` | … | Continuous scan (single radio; optional `adapter_id`, `on_device_found`) |
| `connect(address, *, adapter_id=None)` | `Sensor` | Connect to a **previously scanned** MAC (optional dongle id) |
| `list_bluetooth_adapters()` | `list[BluetoothAdapter]` | Multi-adapter inventory |
| `claim_adapter(adapter_id)` | `ClaimResult` | Windows WinUSB bind (elevated helper) |
| `get_bluetooth_capability()` | `BluetoothCapability` | Platform/extra availability (always callable) |
| `get_scanned_device(address, *, adapter_id=None)` | `ScanResult \| None` | Look up scan cache by MAC (+ optional adapter) |
| `get_connected_sensor(address)` / `get_sensor` | `Sensor \| None` | Connected sensor |
| `list_connected_sensors` / `list_scanned_devices` | lists | Inventory |
| `disconnect` | `None` | Tear down one sensor (managed dongle claims stay until `close`) |
| `close` | `None` | Teardown all sensors and revoke multi-adapter hub-session claims |
| `configure_logging(enabled, path, level)` | `None` | Stdlib logger `synchroni_sensor_sdk` |

### Multi-adapter (experimental)

Dedicated USB Bluetooth dongles (Bumble + libusb / WinUSB) route **one sensor per
HCI controller**. This isolates radios for multi-sensor sessions; it is **not**
hardware sample-time synchronization or a shared sample clock.

| Flag | Behavior |
|------|----------|
| `enable_multi_adapter=False` (default) | OS Bluetooth only (Bleak). Multi-adapter methods raise `MultiAdapterDisabledError`. |
| `enable_multi_adapter=True` | Inventory, managed USB scan/connect, hub-session dongle claims, optional WinUSB claim + firmware helpers. |

**Enable (sync or async):**

```python
from synchroni_sensor_sdk import SensorHub  # sync
# from synchroni_sensor_sdk.async_api import SensorHub  # async

with SensorHub(enable_multi_adapter=True) as hub:
    ...
```

**Install the optional stack:**

```sh
pip install "synchroni-sensor-sdk[managed-usb]"
# or from source:
poetry install --extras managed-usb
```

Without Bumble, inventory still lists `system:default` and may list claim candidates
on Windows, but managed USB HCI raises `ManagedUsbUnavailableError`.

#### Concepts

| Concept | Meaning |
|---------|---------|
| **System radio** | OS BLE via Bleak. Id: `system:default` (`SYSTEM_DEFAULT_ADAPTER_ID`). |
| **Managed USB radio** | Dongle opened in userspace (Bumble `libusb` / WinUSB). Id prefix `usb:`. |
| **`BluetoothAdapter`** | One host controller (system or USB). Fields: `id`, `source`, `usb_transport`, `claim_required`, `is_in_use`, VID/PID/serial. |
| **`ScanResult.adapter_id` / `routes`** | Which radio saw a sensor and optional multi-route RSSI list. Each `RadioAdapter` owns its own MAC scan cache; the hub projects results across radios. |
| **`RadioAdapter`** | Internal per-HCI transport object (Bleak system vs managed USB). Owns one-shot/continuous scan and `prepare_connection` handles. Not a public app API. |
| **Hub-session claim** | After a successful managed `connect(..., adapter_id=…)`, that dongle is bound to the sensor MAC until **`hub.close()`**. `disconnect` does not free the dongle for another MAC. |
| **Radio session** | Shared powered HCI stack for a transport string. Scan reuses the same open stack for the following connect (no full USB close between them). Sessions are released on hub close. |

**SDK owns:** multi-adapter controller (inventory/claims), `RadioRegistry` + radios (scan caches / connect handles), managed USB GATT, WinUSB claim helper, optional RTK firmware cache.  
**App owns:** product UX, multi-device alignment/recording/bus, deciding which dongle maps to which sensor.

#### Prerequisites by platform

| Platform | Notes |
|----------|--------|
| **macOS** | Plug in a known USB dongle; inventory uses libusb + `system_profiler` merge. No admin WinUSB step. Consumer Realtek dongles (e.g. TP-Link UB500) may need host firmware (auto-fetched when enabled). |
| **Windows** | Known EEG VID/PID devices bound to a non-WinUSB driver show `claim_required=True`. Run `claim_adapter` once (UAC), replug if inventory does not flip to `managed_usb`. Adapter ids encode PnP ``&`` as ``%26`` so they survive `cmd.exe` / `poetry run` when pasted into a shell. |
| **Linux** | Managed inventory via libusb when Bumble is installed; udev permissions must allow userspace access to the dongle. |

Known USB Bluetooth inventory / Windows claim allowlist is currently limited to TP-Link UB500 `2357:0604` (`KNOWN_USB_BLUETOOTH_VID_PID` / `KNOWN_EEG_USB_DONGLES`).

#### Workflow A — System BLE only

Same as single-radio v2; multi-adapter flag is optional for this path.

```python
with SensorHub() as hub:  # or enable_multi_adapter=True
    devices = hub.scan(timeout_ms=3000)  # Bleak / system:default
    sensor = hub.connect(devices[0].mac_address)
    # init / stream as usual
```

CLI: `poetry run python examples/v2_cli.py scan` or  
`… connect --adapter-id system:default`.

#### Workflow B — Inventory and capability check

```python
from synchroni_sensor_sdk import SYSTEM_DEFAULT_ADAPTER_ID, SensorHub

with SensorHub(enable_multi_adapter=True) as hub:
    cap = hub.get_bluetooth_capability()
    print(cap.platform, cap.managed_usb_available, cap.supports_windows_claim, cap.notes)

    for a in hub.list_bluetooth_adapters():
        print(
            a.id,
            a.source,
            a.usb_transport,
            "claim?" if a.claim_required else "ready",
            "in_use" if a.is_in_use else "",
        )
```

CLI: `poetry run python examples/v2_cli.py list-adapters`.

#### Workflow C — Windows WinUSB claim (once per dongle model)

When inventory shows `claim_required` / `claim_action=windows_winusb_install`:

```python
with SensorHub(enable_multi_adapter=True) as hub:
    for a in hub.list_bluetooth_adapters():
        if a.claim_required:
            result = hub.claim_adapter(a.id)  # elevates via helper; may download installer
            print(result.success, result.message, result.log_path)
    # Re-list; after success + optional replug, source becomes managed_usb with usb_transport
    adapters = hub.list_bluetooth_adapters()
```

Installer resolution: constructor override `winusb_installer_path` → env
`SYNCHRONI_WINUSB_INSTALLER` → package resources → user cache → download from
public SDK assets `manifest.json` (SHA-256 verified). Override manifest base with
`SYNCHRONI_SDK_ASSETS_MANIFEST_URL`.

#### Workflow D — Scan and connect on one managed dongle

```python
with SensorHub(enable_multi_adapter=True) as hub:
    adapters = [a for a in hub.list_bluetooth_adapters()
                if a.source == "managed_usb" and a.usb_transport and not a.claim_required]
    if not adapters:
        raise RuntimeError("No ready managed USB adapters")

    dongle = adapters[0]
    devices = hub.scan(timeout_ms=3000, adapter_id=dongle.id)
    if not devices:
        raise RuntimeError("No sensors")

    # Prefer the adapter that saw this MAC (ScanResult.adapter_id / routes)
    target = max(devices, key=lambda d: d.rssi)
    sensor = hub.connect(target.mac_address, adapter_id=target.adapter_id or dongle.id)

    sensor.set_param(...)  # optional
    sensor.init(package_sample_count=10, power_refresh_interval=5000)
    sensor.register_data_callback(...)
    sensor.start_streaming()
    # ...
    sensor.stop_streaming()
    hub.disconnect(target.mac_address)  # link down; dongle session claim remains
# hub.__exit__ / close() releases claims and closes radio sessions
```

CLI:

```sh
poetry run python examples/v2_cli.py scan --adapter-id usb:…
poetry run python examples/v2_cli.py connect --adapter-id usb:… --stream-seconds 5
poetry run python examples/v2_cli.py collect --adapter-id usb:… -o ./session.csv
```

#### Workflow E — Scan all free managed dongles (multi-radio discovery)

```python
devices = hub.scan_managed_usb(timeout_ms=2000)
# Each result is route-stamped; pick adapter_id from ScanResult or routes for connect
for d in devices:
    print(d.mac_address, d.adapter_id, d.rssi, d.routes)
```

Skips dongles that are already session-claimed or reserved. For rediscovery on a
**already claimed** adapter after disconnect, pass that id explicitly:

```python
devices = hub.scan(timeout_ms=2000, adapter_id=claimed_adapter_id)
```

#### Workflow F — Multi-sensor, one dongle each

```python
with SensorHub(enable_multi_adapter=True) as hub:
    # Discover on all free radios
    hits = hub.scan_managed_usb(timeout_ms=2500)
    # Assign one strongest hit per radio (app policy may differ)
    by_adapter = {}
    for d in hits:
        aid = d.adapter_id
        if not aid:
            continue
        prev = by_adapter.get(aid)
        if prev is None or d.rssi > prev.rssi:
            by_adapter[aid] = d

    sensors = []
    for aid, d in by_adapter.items():
        s = hub.connect(d.mac_address, adapter_id=aid)
        s.init(10, 5000)
        s.start_streaming()
        sensors.append(s)
    # ...
```

Rules of thumb:

1. Open the hub with `enable_multi_adapter=True` for the whole multi-device session.
2. Claim Windows dongles before managed use.
3. Use `scan`/`scan_managed_usb` results’ `adapter_id` when calling `connect`.
4. Keep one active GATT connection per managed dongle.
5. Call `hub.close()` (context exit) when the session ends so USB is released.

#### Lifecycle: claims, disconnect, close

```
list / claim (Windows) → scan (opens/powers radio, keeps stack for connect)
  → connect (hub-session claim adapter→MAC)
  → stream …
  → disconnect (GATT only; claim + radio remain)
  → reconnect same MAC on same adapter_id  (allowed)
  → connect other MAC on same adapter      (BluetoothAdapterBusyError until close)
  → hub.close()  (disconnect remaining sensors, clear claims, power off / close radios)
```

#### Firmware and environment

| Mechanism | Purpose |
|-----------|---------|
| RTK auto-fetch | Before managed power-on, VID/PID-scoped Realtek host firmware may be downloaded into a user cache (e.g. TP-Link UB500). Soft-fails if offline. Disable: `SYNCHRONI_RTK_FIRMWARE_AUTO=0`. |
| Firmware pins | Optional hub `firmware_resource_dir` + package pin files for known dongles. |
| `SYNCHRONI_WINUSB_INSTALLER` | Local path to claim helper exe. |
| `SYNCHRONI_SDK_ASSETS_MANIFEST_URL` | Override remote installer manifest. |

#### Multi-adapter exceptions

| Exception | When |
|-----------|------|
| `MultiAdapterDisabledError` | Multi APIs called with `enable_multi_adapter=False`. |
| `ManagedUsbUnavailableError` | Bumble/managed stack missing or adapter not managed. |
| `BluetoothAdapterNotFoundError` | Unknown adapter id / no transport / peer missing. |
| `BluetoothAdapterBusyError` | Adapter reserved or session-claimed by another MAC. |
| `BluetoothAdapterClaimRequiredError` | Still needs `claim_adapter` (Windows). |
| `ClaimFailedError` / `WindowsClaimUnavailableError` | Claim helper failed or not supported. |
| `AdapterFirmwareError` | Pinned dongle firmware check failed. |

#### Types reference

`BluetoothAdapter`, `BluetoothCapability`, `SensorRoute`, `ClaimResult`,
`SYSTEM_DEFAULT_ADAPTER_ID`. `ScanResult` includes `adapter_id` and `routes`.

## Sensor

| Method | Description |
|--------|-------------|
| `connect` / `disconnect` / `is_connected` | BLE link |
| `device_state` / `is_inited` / `is_streaming` | Lifecycle (methods, not properties) |
| `ble_chip_type()` | `BleChipType.OYM` or `RFSTAR` |
| `device_info()` | Model, versions, `channel_counts`, `sample_rates`, `mtu_size` |
| `get_battery_level` / `get_cached_battery_level` / `get_temperature` | Diagnostics |
| `init(package_sample_count, power_refresh_interval)` | Channel negotiation; starts battery poll |
| `set_param(SetParamCommand)` | Typed params; NTF changes after init push subscription (restart stream if active) |
| `get_params()` | Snapshot of NTF/filter/debug state (`DeviceParams`) |
| `start_streaming` / `stop_streaming` | Data path |
| `register_*_callback` | Data, power, state, error (one active each) |
| `power_off` / `system_reset` | Device control |
| `destroy` | Cancel callbacks + driver teardown |
| `dropped_data_packets` | Drop-oldest buffer counter |

---

## Core types

### `NtfDataType`

`NTF_ACC`, `NTF_GYRO`, `NTF_EULER_DATA`, `NTF_QUATERNION`, `NTF_GEST`, `NTF_EMG`, `NTF_MAG_ANGLE_DATA`, `NTF_EEG`, `NTF_ECG`, `NTF_IMPEDANCE`, `NTF_IMU`, `NTF_ADS`, `NTF_BRTH`, `NTF_IMPEDANCE_EXT`, `NTF_SPO2`, `NTF_PPG`.

### `SensorData`

Includes `lost_package_count` (package-index gap accumulation) plus channel batch fields.

### `DeviceInfo`

| Field | Type |
|-------|------|
| `model`, `hardware_version`, `firmware_version` | `str` |
| `channel_counts` | `dict[str, int]` (e.g. `eeg`, `ppg`, `gest`) |
| `sample_rates` | `dict[str, int]` |
| `mtu_size` | `int` |

### `SetParamCommand` (optional fields; `None` = skip)

| Group | Fields |
|-------|--------|
| Streams | `enable_ntf_emg/eeg/ecg/imu/brth/impedance/mag_angle/gest/ppg/spo2` |
| IMU sub | `enable_ntf_acc/gyro/euler/quat` (`enable_ntf_imu` is master for all four) |
| Filters | `enable_filter_50hz/60hz/hpf/lpf` |
| Other | `debug_ble_data_path`, `neucir_mode`, `neucir_app_control` |

Pre-init `set_param` only updates the NTF/filter maps used by subsequent `init`. After init, NTF changes rebuild the subscription mask (and restart streaming if active). Filters apply immediately via firmware switch.

### `BleChipType`

`UNKNOWN`, `OYM`, `RFSTAR` (universal stream / RFSTAR service UUID).

### Exceptions (`core.exceptions`)

`SensorError` base; also `SensorTerminatedError`, `SensorNotConnectedError`, `SensorNotReadyError`, `SensorNotInitializedError`, `InvalidDeviceServiceError`, `DataNotificationInProgressError`, `StartDataNotificationError`, `StopDataNotificationError`, `DataContextInitInProgressError`, `DataContextInitError`, `DataContextStopStreamingError`, `DataContextNotTransferringError`, `DataContextReadSamplesError`.

Multi-adapter (when enabled): `MultiAdapterDisabledError`, `ManagedUsbUnavailableError`, `BluetoothAdapterNotFoundError`, `BluetoothAdapterBusyError`, `BluetoothAdapterClaimRequiredError`, `ClaimFailedError`, `WindowsClaimUnavailableError`, `AdapterFirmwareError`.

---

## Driver

**Module:** `async_api.driver.base` — abstract GForce-style driver. Factory: `core.driver.driver_factory`.

Concrete `GForceDriver` implements connect/init/stream/parse for OYM and RFSTAR (CONCAT_BLE + universal stream), PPG/SpO2, gesture, euler/quat (feature-dependent), filters, NeuCir, power_off/system_reset, and drop-oldest data buffering.

---

## Quick start

```python
from synchroni_sensor_sdk import SensorHub
from synchroni_sensor_sdk.core.device import SetParamCommand

with SensorHub() as hub:
    devices = hub.scan(timeout_ms=5000)
    sensor = hub.connect(devices[0].mac_address)
    sensor.set_param(SetParamCommand(enable_ntf_ecg=False, enable_ntf_imu=False, enable_filter_50hz=False,
                                     enable_filter_60hz=False, enable_filter_hpf=False, enable_filter_lpf=False))
    sensor.init(package_sample_count=10, power_refresh_interval=5000)
    sensor.register_data_callback(lambda d: print(d.data_type, d.lost_package_count))
    sensor.start_streaming()
    # ...
    sensor.stop_streaming()
```

---

## Implementation status

| Area | Status |
|------|--------|
| BLE connect / stream (`GForceDriver`) | Implemented (OYM + RFSTAR) |
| Packet parsing / reassembly | Implemented (CONCAT_BLE CRC8, universal CRC16) |
| EEG/ECG/EMG/IMU/BRTH/impedance/mag_angle | Implemented |
| PPG / SpO2 / gesture / euler / quat | Implemented (feature + NTF map gated) |
| `set_param` / `get_params` | Typed command + snapshot; post-init NTF apply with optional stream restart |
| `device_info` rates/MTU / `ble_chip_type` | Implemented |
| Parse watchdog (illegal package jump) | Implemented (clear assemble buffers + stream restart) |
| `lost_package_count` on `SensorData` | Implemented |
| `configure_logging` | Implemented (stdlib) |
| WinRT high-throughput / `SENSOR_SDK_FORCE_NO_ACK` | Optional import-side patches (mirrored from legacy) |
| `power_off` / `system_reset` / `get_temperature` | Implemented |
| Device-found callback | Implemented |
| Exception taxonomy | Implemented and exported |
| Multi-adapter USB HCI (Bumble) | Experimental (`SensorHub(enable_multi_adapter=True)` + optional extras) |
| WinUSB claim / dongle firmware pins | Optional resources + hub APIs when multi-adapter enabled |
| Auto-reconnect | Not in v2 (apps reconnect via state callbacks) |
| FlatBuffers / multi-process BLE host | Intentionally not ported |
