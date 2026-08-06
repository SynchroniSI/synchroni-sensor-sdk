"""Typer application for smoke-testing the v2 sensor SDK."""

from __future__ import annotations

from pathlib import Path

import typer

from synchroni_sensor_sdk import SYSTEM_DEFAULT_ADAPTER_ID, SensorHub
from synchroni_sensor_sdk.core.exceptions import ClaimFailedError

from cli import display as cli_display
from cli.sample_session import run_collect_session, run_connect_session

app = typer.Typer(
    name="v2-cli",
    help="Basic Synchroni sensor SDK CLI for manual testing.",
    no_args_is_help=True,
)


def _claim_windows_adapter(hub: SensorHub, adapter_id: str | None) -> None:
    """On Windows, claim the selected adapter when it requires WinUSB binding."""
    if not adapter_id or adapter_id == SYSTEM_DEFAULT_ADAPTER_ID:
        return
    if not hub.get_bluetooth_capability().supports_windows_claim:
        return

    from synchroni_sensor_sdk.async_api.driver.managed_usb.windows import (
        normalize_windows_adapter_id,
    )

    needle = normalize_windows_adapter_id(adapter_id)
    adapter = next(
        (a for a in hub.list_bluetooth_adapters() if normalize_windows_adapter_id(a.id) == needle),
        None,
    )
    if adapter is None or not adapter.claim_required:
        return

    label = adapter.name or adapter.id
    cli_display.console.print(f"Claiming [cyan]{adapter.id}[/cyan] ({label}) for managed USB…")
    try:
        result = hub.claim_adapter(adapter.id)
    except ClaimFailedError as exc:
        cli_display.console.print(f"  [red]{exc}[/red]")
        return
    style = "green" if result.success else "red"
    cli_display.console.print(f"  [{style}]{result.message}[/{style}]")
    if result.log_path:
        cli_display.console.print(f"  log: [dim]{result.log_path}[/dim]")


@app.command("list-adapters")
def list_adapters() -> None:
    """List host Bluetooth adapters (system + managed USB inventory)."""
    with SensorHub(enable_multi_adapter=True) as hub:
        cli_display.print_capability(hub.get_bluetooth_capability())
        adapters = hub.list_bluetooth_adapters()
        if not adapters:
            cli_display.console.print("[yellow]No adapters found.[/yellow]")
            return
        cli_display.print_adapters(adapters)


@app.command()
def scan(
    timeout_ms: int = typer.Option(
        3000,
        "--timeout-ms",
        help="Scan duration in milliseconds.",
    ),
    adapter_id: str | None = typer.Option(
        None,
        "--adapter-id",
        help=(
            "Radio id. Default / system:default uses Bleak; "
            "a usb:… id uses managed USB multi-adapter scan."
        ),
    ),
) -> None:
    """Scan for nearby Synchroni sensors."""
    with SensorHub(enable_multi_adapter=True) as hub:
        _claim_windows_adapter(hub, adapter_id)
        radio = adapter_id or SYSTEM_DEFAULT_ADAPTER_ID
        cli_display.console.print(
            f"Scanning for [bold]{timeout_ms}[/bold] ms (adapter=[cyan]{radio}[/cyan])…"
        )
        devices = hub.scan(timeout_ms, adapter_id=adapter_id)
        if not devices:
            cli_display.console.print("[yellow]No devices found.[/yellow]")
            return
        cli_display.print_devices(devices)


@app.command()
def connect(
    adapter_id: str = typer.Option(
        SYSTEM_DEFAULT_ADAPTER_ID,
        "--adapter-id",
        help="Radio id to scan and connect through (default: system:default; or usb:…).",
    ),
    scan_timeout_ms: int = typer.Option(
        3000,
        "--scan-timeout-ms",
        help="Scan duration before picking a device.",
    ),
    stream_seconds: float = typer.Option(
        5.0,
        "--stream-seconds",
        help="How long to consume samples after streaming starts.",
    ),
    mac: str | None = typer.Option(
        None,
        "--mac",
        help="Optional device MAC to prefer; default is strongest-RSSI hit.",
    ),
) -> None:
    """Scan on an adapter, connect to the first device, stream briefly, print stats."""
    with SensorHub(enable_multi_adapter=True) as hub:
        _claim_windows_adapter(hub, adapter_id)
        code = run_connect_session(
            hub,
            adapter_id=adapter_id,
            scan_timeout_ms=scan_timeout_ms,
            stream_seconds=stream_seconds,
            mac=mac,
        )
    raise typer.Exit(code=code)


@app.command()
def collect(
    adapter_id: str = typer.Option(
        SYSTEM_DEFAULT_ADAPTER_ID,
        "--adapter-id",
        help="Radio id to scan and connect through (default: system:default; or usb:…).",
    ),
    scan_timeout_ms: int = typer.Option(
        3000,
        "--scan-timeout-ms",
        help="Scan duration before picking a device.",
    ),
    stream_seconds: float = typer.Option(
        10.0,
        "--stream-seconds",
        help="How long to collect samples after streaming starts.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="CSV path. Default: collect-<mac>-<adapter>-<timestamp>.csv in the cwd.",
    ),
    mac: str | None = typer.Option(
        None,
        "--mac",
        help="Optional device MAC to prefer; default is strongest-RSSI hit.",
    ),
) -> None:
    """Scan, connect, stream for an interval, and write samples to a CSV file."""
    with SensorHub(enable_multi_adapter=True) as hub:
        _claim_windows_adapter(hub, adapter_id)
        code = run_collect_session(
            hub,
            adapter_id=adapter_id,
            scan_timeout_ms=scan_timeout_ms,
            stream_seconds=stream_seconds,
            output=output,
            mac=mac,
        )
    raise typer.Exit(code=code)


@app.command()
def clean(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Wipe cached WinUSB installers, RTK host firmware, and pin firmware blobs."""
    from synchroni_sensor_sdk import clear_winusb_installer_cache, winusb_installer_cache_dir
    from synchroni_sensor_sdk.async_api.driver.managed_usb.firmware import (
        clear_firmware_resource_dir,
        default_firmware_resource_dir,
    )
    from synchroni_sensor_sdk.async_api.driver.managed_usb.rtk_firmware import (
        clear_rtk_firmware_cache,
        rtk_firmware_cache_dir,
    )

    winusb_dir = winusb_installer_cache_dir()
    rtk_dir = rtk_firmware_cache_dir()
    pin_dir = default_firmware_resource_dir()

    cli_display.console.print("Will remove caches:")
    cli_display.console.print(f"  • WinUSB installers: [cyan]{winusb_dir}[/cyan]")
    cli_display.console.print(
        f"  • RTK host firmware: [cyan]{rtk_dir if rtk_dir is not None else '(unresolved)'}[/cyan]"
    )
    cli_display.console.print(f"  • Firmware pin blobs: [cyan]{pin_dir}[/cyan]")

    if not yes and not typer.confirm("Proceed?"):
        cli_display.console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(code=1)

    cleared_winusb = clear_winusb_installer_cache()
    cleared_rtk = clear_rtk_firmware_cache()
    cleared_pins = clear_firmware_resource_dir()

    cli_display.console.print("[green]Done.[/green]")
    cli_display.console.print(f"  WinUSB cache: {cleared_winusb}")
    cli_display.console.print(f"  RTK cache: {cleared_rtk if cleared_rtk is not None else '(none)'}")
    cli_display.console.print(f"  Firmware pins dir: {cleared_pins}")
