"""``pisight doctor`` -- a strictly read-only environment diagnostic.

What this command will never do: enable monitor mode, bring an interface up or down, change
a channel, install a driver, modify a configuration file, write anything to disk, or run a
shell. Every external command is invoked with an argument list (never ``shell=True``), with
a short timeout, and is treated as optional -- a missing executable is a skipped check, not
an error.

The Kismet checks report whether the API answers and whether the token is accepted. The
token value itself is never printed, logged or included in the JSON output.
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .config import API_TOKEN_ENV, AppConfig, get_api_token
from .health.host import read_cpu_temperature_c, read_disk_usage
from .sanitize import sanitize_text

__all__ = ["DoctorCheck", "DoctorReport", "format_report", "run_doctor"]

Status = Literal["ok", "warn", "fail", "skip"]

#: Timeout for every external command, in seconds. Diagnostics must never hang a terminal.
COMMAND_TIMEOUT = 4.0

#: Maximum characters kept from any command's output.
_MAX_DETAIL = 400


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One diagnostic result."""

    name: str
    status: Status
    detail: str
    category: str = "general"

    @property
    def symbol(self) -> str:
        """ASCII status marker; readable over a serial console with no Unicode support."""
        return {"ok": "[ ok ]", "warn": "[warn]", "fail": "[FAIL]", "skip": "[skip]"}[self.status]


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """The full set of checks plus a roll-up verdict."""

    checks: tuple[DoctorCheck, ...]

    @property
    def failures(self) -> int:
        """Number of checks that failed outright."""
        return sum(1 for check in self.checks if check.status == "fail")

    @property
    def warnings(self) -> int:
        """Number of checks that produced a warning."""
        return sum(1 for check in self.checks if check.status == "warn")

    @property
    def exit_code(self) -> int:
        """``1`` when anything failed, otherwise ``0``. Warnings do not fail the command."""
        return 1 if self.failures else 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for ``--json``."""
        return {
            "checks": [asdict(check) for check in self.checks],
            "failures": self.failures,
            "warnings": self.warnings,
            "ok": self.failures == 0,
        }


def _run_command(args: Sequence[str]) -> tuple[bool, str]:
    """Run a read-only command, returning ``(succeeded, output)``.

    ``shell=False`` is implicit and deliberate: the argument list goes straight to
    ``execve``, so nothing in the environment or in a filename can be interpreted as shell
    syntax.
    """
    executable = shutil.which(args[0])
    if executable is None:
        return (False, f"{args[0]} not installed")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, short timeout
            [executable, *args[1:]],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, f"{args[0]} failed: {type(exc).__name__}")

    output = (completed.stdout or completed.stderr or "").strip()
    return (completed.returncode == 0, sanitize_text(output, max_length=_MAX_DETAIL))


def _check_platform() -> list[DoctorCheck]:
    """OS, architecture and Python version."""
    machine = platform.machine() or "unknown"
    is_arm64 = machine.lower() in ("aarch64", "arm64")
    return [
        DoctorCheck(
            "operating system",
            "ok",
            f"{platform.system()} {platform.release()}",
            category="platform",
        ),
        DoctorCheck(
            "architecture",
            "ok" if is_arm64 else "warn",
            f"{machine}"
            + ("" if is_arm64 else " (PiSight targets ARM64; this is a development host)"),
            category="platform",
        ),
        DoctorCheck(
            "python",
            "ok" if sys.version_info >= (3, 11) else "fail",
            platform.python_version(),
            category="platform",
        ),
    ]


def _check_display() -> list[DoctorCheck]:
    """Display environment, DRM/framebuffer nodes and SDL/Pygame availability."""
    checks: list[DoctorCheck] = []

    driver = os.environ.get("SDL_VIDEODRIVER", "")
    session = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or ""
    checks.append(
        DoctorCheck(
            "display environment",
            "ok" if (driver or session) else "warn",
            f"SDL_VIDEODRIVER={driver or '(unset)'} DISPLAY={session or '(unset)'}",
            category="display",
        )
    )

    dri = Path("/dev/dri")
    if dri.is_dir():
        try:
            nodes = sorted(entry.name for entry in dri.iterdir())
        except OSError as exc:
            nodes = [f"unreadable: {type(exc).__name__}"]
        checks.append(
            DoctorCheck(
                "/dev/dri",
                "ok" if nodes else "warn",
                ", ".join(nodes) or "empty",
                category="display",
            )
        )
    else:
        checks.append(
            DoctorCheck("/dev/dri", "skip", "not present (expected off-Linux)", category="display")
        )

    framebuffers = [p.name for p in (Path("/dev/fb0"), Path("/dev/fb1")) if p.exists()]
    checks.append(
        DoctorCheck(
            "framebuffer devices",
            "ok" if framebuffers else "skip",
            ", ".join(framebuffers) or "none present",
            category="display",
        )
    )

    try:
        import pygame

        version_module = getattr(pygame, "version", None)
        pygame_version = getattr(version_module, "ver", "unknown")
        detail = f"pygame-ce {pygame_version}"
        try:
            sdl = ".".join(str(part) for part in pygame.get_sdl_version())
            detail += f" (SDL {sdl})"
        except (pygame.error, AttributeError):  # pragma: no cover - unusual SDL build
            detail += " (SDL version unavailable)"
        checks.append(DoctorCheck("pygame/SDL", "ok", detail, category="display"))
    except ImportError as exc:
        checks.append(
            DoctorCheck(
                "pygame/SDL", "fail", f"pygame-ce not importable: {exc}", category="display"
            )
        )

    return checks


def _check_input() -> list[DoctorCheck]:
    """Input devices visible to the current user."""
    input_dir = Path("/dev/input")
    if not input_dir.is_dir():
        return [DoctorCheck("input devices", "skip", "/dev/input not present", category="input")]
    try:
        entries = sorted(
            entry.name for entry in input_dir.iterdir() if entry.name.startswith("event")
        )
    except OSError as exc:
        return [
            DoctorCheck(
                "input devices", "warn", f"unreadable: {type(exc).__name__}", category="input"
            )
        ]

    readable = [name for name in entries if os.access(input_dir / name, os.R_OK)]
    status: Status = "ok" if readable else "warn"
    detail = f"{len(entries)} event devices, {len(readable)} readable by this user"
    return [DoctorCheck("input devices", status, detail, category="input")]


def _check_usb_and_wireless() -> list[DoctorCheck]:
    """USB inventory, wireless interfaces and driver information, all read-only."""
    checks: list[DoctorCheck] = []

    ok, output = _run_command(["lsusb"])
    if "not installed" in output:
        checks.append(DoctorCheck("usb devices", "skip", output, category="hardware"))
    else:
        lines = [line for line in output.splitlines() if line.strip()]
        checks.append(
            DoctorCheck(
                "usb devices",
                "ok" if ok else "warn",
                f"{len(lines)} devices" + (f"; first: {lines[0]}" if lines else ""),
                category="hardware",
            )
        )

    interfaces: list[str] = []
    wireless_dir = Path("/sys/class/net")
    if wireless_dir.is_dir():
        try:
            for entry in sorted(wireless_dir.iterdir()):
                if (entry / "wireless").exists() or (entry / "phy80211").exists():
                    interfaces.append(entry.name)
        except OSError:
            pass
    checks.append(
        DoctorCheck(
            "wireless interfaces",
            "ok" if interfaces else "warn",
            ", ".join(interfaces) or "none found",
            category="hardware",
        )
    )

    for interface in interfaces[:4]:
        driver_link = Path("/sys/class/net") / interface / "device" / "driver"
        try:
            driver = driver_link.resolve().name if driver_link.exists() else "unknown"
        except OSError:
            driver = "unknown"
        checks.append(DoctorCheck(f"driver: {interface}", "ok", driver, category="hardware"))

    ok, output = _run_command(["iw", "list"])
    if "not installed" in output:
        checks.append(
            DoctorCheck(
                "monitor mode support",
                "skip",
                "iw not installed; cannot inspect supported interface modes",
                category="hardware",
            )
        )
    elif ok:
        supported = "monitor" in output.lower()
        checks.append(
            DoctorCheck(
                "monitor mode support",
                "ok" if supported else "warn",
                "a wireless phy reports monitor mode"
                if supported
                else "no phy advertises monitor mode",
                category="hardware",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "monitor mode support", "warn", "iw list returned an error", category="hardware"
            )
        )

    return checks


def _check_groups() -> list[DoctorCheck]:
    """Membership in the ``video`` and ``input`` groups, needed for direct DRM/KMS output.

    ``grp`` and ``os.getgroups`` are POSIX-only. They are resolved dynamically so this
    module imports and type-checks cleanly on a Windows development host, where the check
    simply reports itself as skipped.
    """
    unavailable = [
        DoctorCheck(
            "group membership",
            "skip",
            "group information unavailable on this platform",
            category="permissions",
        )
    ]

    get_groups = getattr(os, "getgroups", None)
    if get_groups is None:
        return unavailable
    try:
        grp = importlib.import_module("grp")
    except ImportError:
        return unavailable

    names: set[str] = set()
    try:
        for gid in get_groups():
            try:
                names.add(str(grp.getgrgid(gid).gr_name))
            except (KeyError, OSError):
                continue
    except OSError:
        return unavailable

    missing = [group for group in ("video", "input") if group not in names]
    return [
        DoctorCheck(
            "group membership",
            "ok" if not missing else "warn",
            "member of video and input"
            if not missing
            else f"not in: {', '.join(missing)} (needed for the DRM/KMS profile)",
            category="permissions",
        )
    ]


def _check_storage(config: AppConfig) -> list[DoctorCheck]:
    """Configured storage path, free space and CPU temperature."""
    path = config.storage.kismet_log_path
    total, free = read_disk_usage(path)
    exists = Path(path).exists()

    if total is None or free is None:
        storage_check = DoctorCheck(
            "storage", "warn", f"{path}: usage unavailable", category="storage"
        )
    else:
        pct = 100.0 * free / total if total else 0.0
        status: Status = "ok" if pct >= config.storage.low_space_warn_pct else "warn"
        storage_check = DoctorCheck(
            "storage",
            status,
            f"{path}: {free / 1024**3:.1f} GiB free of {total / 1024**3:.1f} GiB ({pct:.0f}%)",
            category="storage",
        )

    temp = read_cpu_temperature_c()
    temp_check = DoctorCheck(
        "cpu temperature",
        "ok" if temp is None or temp < 80 else "warn",
        f"{temp:.1f} C" if temp is not None else "unavailable (no thermal zone)",
        category="platform",
    )

    return [
        DoctorCheck(
            "storage path",
            "ok" if exists else "warn",
            f"{path} {'exists' if exists else 'does not exist yet'}",
            category="storage",
        ),
        storage_check,
        temp_check,
    ]


def _check_kismet(config: AppConfig) -> list[DoctorCheck]:
    """Kismet reachability and token acceptance, without revealing the token."""
    checks: list[DoctorCheck] = []
    token = get_api_token()

    checks.append(
        DoctorCheck(
            "api token",
            "ok" if token else "warn",
            f"{API_TOKEN_ENV} is set ({len(token)} characters)"
            if token
            else f"{API_TOKEN_ENV} is not set; live mode will not authenticate",
            category="kismet",
        )
    )

    try:
        import httpx

        from .health.host import HostHealthProvider
        from .providers.kismet import KismetDashboardProvider
    except ImportError as exc:  # pragma: no cover - httpx is a hard dependency
        return [
            *checks,
            DoctorCheck("kismet api", "fail", f"httpx unavailable: {exc}", category="kismet"),
        ]

    provider = KismetDashboardProvider(
        config,
        api_token=token,
        host_health=HostHealthProvider(config.storage.kismet_log_path),
    )
    try:
        try:
            server_time = provider.server_time()
        except Exception as exc:
            reason = sanitize_text(str(exc), max_length=120)
            checks.append(
                DoctorCheck(
                    "kismet api",
                    "warn",
                    f"{config.kismet.base_url} unreachable: {reason}",
                    category="kismet",
                )
            )
            return checks

        checks.append(
            DoctorCheck(
                "kismet api",
                "ok",
                f"{config.kismet.base_url} responded (server time {server_time:.0f})",
                category="kismet",
            )
        )

        if token:
            try:
                authenticated = provider.check_login()
            except Exception as exc:
                checks.append(
                    DoctorCheck(
                        "kismet auth",
                        "warn",
                        f"login check failed: {sanitize_text(str(exc), max_length=120)}",
                        category="kismet",
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        "kismet auth",
                        "ok" if authenticated else "fail",
                        "token accepted (readonly role)"
                        if authenticated
                        else "token rejected by Kismet",
                        category="kismet",
                    )
                )
        else:
            checks.append(
                DoctorCheck("kismet auth", "skip", "no token to verify", category="kismet")
            )
    finally:
        provider.close()
        _ = httpx  # keep the import meaningful for readers

    return checks


def run_doctor(config: AppConfig, *, check_kismet: bool = True) -> DoctorReport:
    """Run every diagnostic and return the report.

    ``check_kismet=False`` skips the network probes, which keeps the unit tests offline.
    """
    checks: list[DoctorCheck] = []
    checks.extend(_check_platform())
    checks.extend(_check_display())
    checks.extend(_check_input())
    checks.extend(_check_usb_and_wireless())
    checks.extend(_check_groups())
    checks.extend(_check_storage(config))
    if check_kismet:
        checks.extend(_check_kismet(config))
    else:
        checks.append(
            DoctorCheck("kismet api", "skip", "network checks disabled", category="kismet")
        )
    return DoctorReport(tuple(checks))


def format_report(report: DoctorReport, *, as_json: bool = False) -> str:
    """Render the report as JSON or as a grouped, aligned text table."""
    if as_json:
        return json.dumps(report.to_dict(), indent=2, sort_keys=True)

    lines: list[str] = ["PiSight doctor - read-only environment diagnostic", ""]
    current_category = ""
    for check in report.checks:
        if check.category != current_category:
            current_category = check.category
            lines.append(f"-- {current_category} " + "-" * max(0, 58 - len(current_category)))
        lines.append(f"  {check.symbol} {check.name:<22} {check.detail}")

    lines.append("")
    lines.append(
        f"{report.failures} failure(s), {report.warnings} warning(s). "
        "This command changes nothing on the system."
    )
    return "\n".join(lines)
