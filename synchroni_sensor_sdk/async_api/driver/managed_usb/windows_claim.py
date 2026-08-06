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

# libwdi / helper status codes commonly returned as process exit codes.
_WDI_EXIT_HINTS: dict[int, str] = {
    -1: "I/O error",
    -2: "invalid parameter",
    -3: "access denied (run elevated; check CWD/--dest and driver signature policy)",
    -4: "device not found",
    -5: "exists",
    -6: "busy",
    -7: "timeout",
    -8: "busy pending",
    -9: "cancelled",
    -11: "resource allocation failed",
    -19: "unsigned driver rejected by Windows policy (or missing elevated .cat generation)",
}

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
    # Avoid "&" in the friendly name: command lines are easy to mis-parse.
    device_name = f"Synchroni EEG Bluetooth Dongle VID_{vid} PID_{pid}"

    work_dir = Path(tempfile.gettempdir()) / "synchroni-sensor-sdk" / "windows-winusb-setup" / f"claim-{uuid4()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    driver_dir = work_dir / "driver"
    driver_dir.mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "claim.log"
    runner_path = work_dir / "run-claim.ps1"

    helper_args = [
        "--type",
        "0",
        "--vid",
        f"0x{vid}",
        "--pid",
        f"0x{pid}",
        "--name",
        device_name,
        # Elevated Start-Process often starts in System32; force a writable dest.
        "--dest",
        str(driver_dir),
    ]

    # Elevate a small script so we can capture installer stdout/stderr.
    # Start-Process -Verb RunAs cannot redirect the elevated process streams.
    runner_path.write_text(
        _build_elevated_runner_script(installer=installer, helper_args=helper_args, log_path=log_path),
        encoding="utf-8",
    )

    logger.info("Starting WinUSB claim for %s via %s (log=%s)", adapter.id, installer, log_path)
    try:
        if _is_process_elevated():
            # Already admin: run in this process tree (no UAC, no extra console).
            code = await _run_installer_capturing_log(
                installer=installer,
                helper_args=helper_args,
                log_path=log_path,
            )
        else:
            # UAC consent is unavoidable once; hide the elevated console window.
            code = await _run_installer_elevated_hidden(runner_path=runner_path, log_path=log_path)
    except Exception as exc:
        raise ClaimFailedError(f"Failed to launch WinUSB claim helper: {exc}") from exc

    if code != 0:
        raise ClaimFailedError(_format_claim_failure(code=code, log_path=log_path))

    return ClaimResult(
        adapter_id=adapter.id,
        success=True,
        message="WinUSB claim completed. Replug the dongle if it does not appear as managed USB.",
        log_path=str(log_path),
    )


async def _run_installer_capturing_log(
    *,
    installer: Path,
    helper_args: list[str],
    log_path: Path,
) -> int:
    """Run the claim helper in-process (caller must already be elevated)."""
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write("Claim runner starting (already elevated)\n")
        fh.write(f"exe={installer}\n")
        fh.write(f"args={' '.join(helper_args)}\n")
    proc = await asyncio.create_subprocess_exec(
        str(installer),
        *helper_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(log_path.parent),
        creationflags=_subprocess_no_window_flags(),
    )
    stdout, _ = await proc.communicate()
    text = (stdout or b"").decode("utf-8", errors="replace")
    with log_path.open("a", encoding="utf-8") as fh:
        if text:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
        code = _normalize_exit_code(proc.returncode)
        fh.write(f"exit_code={code}\n")
    return code


async def _run_installer_elevated_hidden(*, runner_path: Path, log_path: Path) -> int:
    """Prompt UAC once and run the claim helper with a hidden elevated console."""
    elevate = (
        "function Normalize-ExitCode([int]$code) { "
        "  if ($code -gt 2147483647) { return ($code - 4294967296) }; "
        "  return $code "
        "}; "
        f"$p = Start-Process -FilePath powershell "
        f"-ArgumentList @("
        f"'-NoProfile',"
        f"'-NonInteractive',"
        f"'-WindowStyle','Hidden',"
        f"'-ExecutionPolicy','Bypass',"
        f"'-File',{_ps_quote(str(runner_path))}"
        f") "
        f"-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        f"if ($null -eq $p) {{ throw 'WinUSB claim helper failed to start (UAC declined?)' }}; "
        f"exit (Normalize-ExitCode $p.ExitCode)"
    )
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-Command",
        elevate,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_subprocess_no_window_flags(),
    )
    stdout, stderr = await proc.communicate()
    wrapper_tail = ((stdout or b"") + b"\n" + (stderr or b"")).decode("utf-8", errors="replace").strip()
    if wrapper_tail:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("\n--- elevation wrapper ---\n")
            fh.write(wrapper_tail)
            fh.write("\n")
    return _normalize_exit_code(proc.returncode)


def _is_process_elevated() -> bool:
    """Return True when the current process token is elevated (admin)."""
    if platform.system().lower() != "windows":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _subprocess_no_window_flags() -> int:
    """Avoid flashing a console for helper PowerShell/installer processes."""
    if platform.system().lower() != "windows":
        return 0
    return 0x08000000  # CREATE_NO_WINDOW


def _build_elevated_runner_script(*, installer: Path, helper_args: list[str], log_path: Path) -> str:
    quoted_args = ", ".join(_ps_quote(a) for a in helper_args)
    return (
        "$ErrorActionPreference = 'Continue'\n"
        f"Set-Location -LiteralPath {_ps_quote(str(log_path.parent))}\n"
        f"$exe = {_ps_quote(str(installer))}\n"
        f"$argList = @({quoted_args})\n"
        f"$log = {_ps_quote(str(log_path))}\n"
        '"Claim runner starting" | Set-Content -LiteralPath $log -Encoding utf8\n'
        '"exe=$exe" | Add-Content -LiteralPath $log -Encoding utf8\n'
        "\"args=$($argList -join ' ')\" | Add-Content -LiteralPath $log -Encoding utf8\n"
        "try {\n"
        "  & $exe @argList 2>&1 | ForEach-Object {\n"
        '    $line = "$_"\n'
        "    $line | Add-Content -LiteralPath $log -Encoding utf8\n"
        "    $line\n"
        "  }\n"
        "  $code = $LASTEXITCODE\n"
        "} catch {\n"
        "  $_ | Out-String | Add-Content -LiteralPath $log -Encoding utf8\n"
        "  $code = 1\n"
        "}\n"
        "if ($null -eq $code) { $code = 0 }\n"
        "if ($code -gt 2147483647) { $code = $code - 4294967296 }\n"
        '"exit_code=$code" | Add-Content -LiteralPath $log -Encoding utf8\n'
        "exit $code\n"
    )


def _normalize_exit_code(code: int | None) -> int:
    if code is None:
        return 0
    # Windows / PowerShell sometimes surface negative codes as uint32.
    if code > 0x7FFFFFFF:
        return code - 0x100000000
    return code


def _format_claim_failure(*, code: int, log_path: Path) -> str:
    hint = _WDI_EXIT_HINTS.get(code)
    parts = [f"WinUSB claim helper exited with code {code}"]
    if hint:
        parts.append(f"({hint})")
    parts.append(f"See log: {log_path}.")
    tail = _read_log_tail(log_path)
    if tail:
        parts.append(f"Log tail:\n{tail}")
    return " ".join(parts) if not tail else " ".join(parts[:-1]) + f"\nLog tail:\n{tail}"


def _read_log_tail(log_path: Path, *, max_lines: int = 20) -> str:
    try:
        if not log_path.is_file() or log_path.stat().st_size == 0:
            return ""
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_cmdline_arg(value: str) -> str:
    """Quote one argument for a Windows process command line."""
    if not value:
        return '""'
    if any(ch in value for ch in ' \t"&<>|()^%'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _windows_argument_list(args: list[str]) -> str:
    """Join argv into a single Start-Process -ArgumentList string."""
    return " ".join(_windows_cmdline_arg(a) for a in args)


def require_claimable(adapter: BluetoothAdapter) -> None:
    """Raise if adapter explicitly needs claim before managed connect."""
    if adapter.claim_required:
        raise BluetoothAdapterClaimRequiredError(
            adapter.claim_message or f"Adapter {adapter.id} requires claim_adapter() before managed USB use."
        )
