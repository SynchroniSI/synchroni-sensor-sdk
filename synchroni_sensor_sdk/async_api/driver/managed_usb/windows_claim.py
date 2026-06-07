"""Windows WinUSB claim runner for dedicated EEG dongles.

Runs the libwdi-based helper (``synchroni-winusb-installer.exe`` /
``winusb-installer.exe``) with admin elevation.

Installer resolution (see :mod:`winusb_installer`):
override path → ``SYNCHRONI_WINUSB_INSTALLER`` → package resources →
user cache → download from the public SDK assets manifest (SHA-256 verified).
"""

from __future__ import annotations

import asyncio
import logging
import platform
import tempfile
from pathlib import Path
from uuid import uuid4

from synchroni_sensor_sdk.async_api.driver.managed_usb.winusb_installer import (
    WINUSB_INSTALLER_ENV,
    clear_winusb_installer_cache,
    default_winusb_installer_path,
    ensure_winusb_installer,
    winusb_installer_cache_dir,
)
from synchroni_sensor_sdk.core.bluetooth import (
    KNOWN_EEG_USB_DONGLES,
    WINDOWS_CLAIM_ACTION_WINUSB,
    BluetoothAdapter,
    ClaimResult,
)
from synchroni_sensor_sdk.core.exceptions import (
    BluetoothAdapterClaimRequiredError,
    BluetoothAdapterNotFoundError,
    ClaimFailedError,
    WindowsClaimUnavailableError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "WINUSB_INSTALLER_ENV",
    "claim_windows_adapter",
    "clear_winusb_installer_cache",
    "default_winusb_installer_path",
    "ensure_winusb_installer",
    "require_claimable",
    "resolve_installer_path",
    "winusb_installer_cache_dir",
]


def resolve_installer_path(override: str | Path | None) -> Path:
    """Resolve installer path (local or downloaded cache) or raise.

    This may perform a network download when no local installer is available.
    Prefer :func:`ensure_winusb_installer` for the same behavior with a clearer name.
    """
    return ensure_winusb_installer(override)


async def claim_windows_adapter(
    adapter: BluetoothAdapter,
    *,
    installer_path: str | Path | None = None,
) -> ClaimResult:
    """Bind known EEG dongles to WinUSB via elevated claim helper.

    Parameters
    ----------
    adapter:
        Inventory row with ``claim_action == windows_winusb_install``.
    installer_path:
        Optional explicit helper path. When omitted, the SDK uses env /
        package resources / cache, or downloads the helper from the public
        assets manifest.
    """
    if platform.system().lower() != "windows":
        raise WindowsClaimUnavailableError("WinUSB claim is only supported on Windows.")

    if adapter.claim_action != WINDOWS_CLAIM_ACTION_WINUSB and not adapter.claim_required:
        return ClaimResult(
            adapter_id=adapter.id,
            success=True,
            message="Adapter does not require a WinUSB claim.",
        )

    if adapter.vendor_id is None or adapter.product_id is None:
        raise BluetoothAdapterNotFoundError("Adapter is missing vendor/product id for claim.")

    key = (adapter.vendor_id.lower(), adapter.product_id.lower())
    if key not in KNOWN_EEG_USB_DONGLES:
        raise ClaimFailedError(
            f"VID:{adapter.vendor_id} PID:{adapter.product_id} is not in the known EEG dongle allowlist."
        )

    # Network + disk I/O for cache/download; keep the event loop free.
    installer = await asyncio.to_thread(ensure_winusb_installer, installer_path)
    vid = adapter.vendor_id.upper()
    pid = adapter.product_id.upper()
    device_name = f"Synchroni EEG Bluetooth Dongle VID_{vid}&PID_{pid}"
    args = [
        str(installer),
        "--type",
        "0",
        "--vid",
        f"0x{vid}",
        "--pid",
        f"0x{pid}",
        "--name",
        device_name,
    ]

    log_dir = Path(tempfile.gettempdir()) / "synchroni-sensor-sdk" / "windows-winusb-setup"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"winusb-claim-{uuid4()}.log"

    logger.info("Starting WinUSB claim for %s via %s", adapter.id, installer)
    # Elevation: Start-Process -Verb RunAs; process exits only after UAC dialog.
    # For non-interactive CI this will fail; that is intentional.
    ps = (
        f"$p = Start-Process -FilePath {_ps_quote(args[0])} "
        f"-ArgumentList @({', '.join(_ps_quote(a) for a in args[1:])}) "
        f"-Verb RunAs -Wait -PassThru; "
        f"exit $p.ExitCode"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-Command",
            ps,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        log_path.write_bytes((stdout or b"") + b"\n" + (stderr or b""))
        code = proc.returncode or 0
    except Exception as exc:
        raise ClaimFailedError(f"Failed to launch WinUSB claim helper: {exc}") from exc

    if code != 0:
        raise ClaimFailedError(f"WinUSB claim helper exited with code {code}. See log: {log_path}")

    return ClaimResult(
        adapter_id=adapter.id,
        success=True,
        message="WinUSB claim completed. Replug the dongle if it does not appear as managed USB.",
        log_path=str(log_path),
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def require_claimable(adapter: BluetoothAdapter) -> None:
    """Raise if adapter explicitly needs claim before managed connect."""
    if adapter.claim_required:
        raise BluetoothAdapterClaimRequiredError(
            adapter.claim_message or f"Adapter {adapter.id} requires claim_adapter() before managed USB use."
        )
