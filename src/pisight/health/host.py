"""Read-only host health collection.

Everything here uses plain filesystem reads and :func:`shutil.disk_usage`. Nothing needs
root, nothing shells out, and nothing writes. Off a Raspberry Pi the collectors return
``None`` rather than raising, so the same code path runs during desktop development.

This runs on the polling thread, never on the rendering thread: reading sysfs and calling
``disk_usage`` on a cold NVMe can block for milliseconds, which would show up as dropped
frames on a 30 FPS display.
"""

from __future__ import annotations

import os
import platform
import shutil
import time
from collections.abc import Sequence
from pathlib import Path

from ..models import HostHealth

__all__ = ["HostHealthProvider", "read_cpu_temperature_c", "read_disk_usage"]

#: Thermal zones exposed by the Raspberry Pi kernel, in preference order.
THERMAL_PATHS: tuple[Path, ...] = (
    Path("/sys/class/thermal/thermal_zone0/temp"),
    Path("/sys/devices/virtual/thermal/thermal_zone0/temp"),
)

#: Device nodes whose presence indicates a usable display pipeline.
DISPLAY_PATHS: tuple[Path, ...] = (Path("/dev/dri"), Path("/dev/fb0"), Path("/dev/fb1"))

#: Plausible CPU temperature range; anything outside is a misread file.
_MIN_TEMP_C = -40.0
_MAX_TEMP_C = 150.0


def read_cpu_temperature_c(paths: Sequence[Path] = THERMAL_PATHS) -> float | None:
    """Read CPU temperature in degrees Celsius from Linux sysfs.

    The kernel reports milli-degrees (``48312`` meaning 48.3 C). Returns ``None`` on any
    platform without the thermal zone, and on unreadable or implausible values.
    """
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, ValueError):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        celsius = value / 1000.0 if abs(value) > 200 else value
        if _MIN_TEMP_C <= celsius <= _MAX_TEMP_C:
            return round(celsius, 1)
    return None


def read_disk_usage(path: str | Path) -> tuple[int | None, int | None]:
    """Return ``(total_bytes, free_bytes)`` for ``path``.

    Walks up to the nearest existing parent so a configured-but-not-yet-created Kismet log
    directory still reports the usage of the filesystem it will live on.
    """
    candidate = Path(path)
    for _ in range(8):
        try:
            usage = shutil.disk_usage(candidate)
        except (OSError, ValueError):
            parent = candidate.parent
            if parent == candidate:
                return (None, None)
            candidate = parent
            continue
        return (usage.total, usage.free)
    return (None, None)


def display_is_visible(paths: Sequence[Path] = DISPLAY_PATHS) -> bool:
    """True when a DRM/KMS or framebuffer device node is present, or a desktop session is."""
    for path in paths:
        try:
            if path.exists():
                return True
        except OSError:
            continue
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class HostHealthProvider:
    """Collects :class:`~pisight.models.HostHealth` with a short internal cache.

    Disk and thermal reads are cheap but not free, and the dashboard polls faster than
    these values meaningfully change, so results are cached for ``cache_seconds``.
    """

    def __init__(
        self,
        storage_path: str | Path,
        *,
        cache_seconds: float = 10.0,
        process_start: float | None = None,
    ) -> None:
        self._storage_path = str(storage_path)
        self._cache_seconds = max(0.0, cache_seconds)
        self._process_start = time.monotonic() if process_start is None else process_start
        self._cached: HostHealth | None = None
        self._cached_at = 0.0

    @property
    def uptime_seconds(self) -> float:
        """Seconds since this PiSight process started."""
        return max(0.0, time.monotonic() - self._process_start)

    def collect(self, *, force: bool = False) -> HostHealth:
        """Return current host health, using the cache unless ``force`` is set."""
        now = time.monotonic()
        cached = self._cached
        if not force and cached is not None and (now - self._cached_at) < self._cache_seconds:
            # Uptime must stay live even when the expensive fields are cached.
            return HostHealth(
                cpu_temp_c=cached.cpu_temp_c,
                disk_total_bytes=cached.disk_total_bytes,
                disk_free_bytes=cached.disk_free_bytes,
                uptime_seconds=self.uptime_seconds,
                platform=cached.platform,
                architecture=cached.architecture,
                display_available=cached.display_available,
                storage_path=cached.storage_path,
            )

        total, free = read_disk_usage(self._storage_path)
        health = HostHealth(
            cpu_temp_c=read_cpu_temperature_c(),
            disk_total_bytes=total,
            disk_free_bytes=free,
            uptime_seconds=self.uptime_seconds,
            platform=platform.system() or "unknown",
            architecture=platform.machine() or "unknown",
            display_available=display_is_visible(),
            storage_path=self._storage_path,
        )
        self._cached = health
        self._cached_at = now
        return health
