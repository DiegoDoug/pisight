"""The provider boundary the UI is written against.

Screens receive a :class:`~pisight.models.DashboardSnapshot` and nothing else. They cannot
tell whether it came from Kismet or from the mock generator, which is what lets the entire
interface be developed and tested on a desktop with no radio attached.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Protocol, runtime_checkable

from ..models import DashboardSnapshot, DeviceSummary

__all__ = [
    "DashboardProvider",
    "NewDeviceTracker",
    "ProviderAuthError",
    "ProviderError",
    "ProviderUnavailable",
]


class ProviderError(RuntimeError):
    """Base class for recoverable provider failures.

    A provider raising this is telling the polling coordinator "this attempt failed, keep
    the previous snapshot and back off" -- never "terminate the application".
    """


class ProviderUnavailable(ProviderError):
    """The data source could not be reached (connection refused, timeout, DNS)."""


class ProviderAuthError(ProviderError):
    """The data source rejected our credentials (HTTP 401/403).

    The token itself is never included in the message.
    """


@runtime_checkable
class DashboardProvider(Protocol):
    """Anything that can produce a complete dashboard snapshot."""

    #: Short human-readable source name shown on the Alerts/System screen.
    name: str

    def fetch(self) -> DashboardSnapshot:
        """Produce one snapshot, or raise :class:`ProviderError` on a recoverable failure."""
        ...

    def close(self) -> None:
        """Release any held resources (HTTP connections, generators)."""
        ...


class NewDeviceTracker:
    """Tracks which device keys were first observed during this PiSight process.

    "New" deliberately means *new to this process*, not new to Kismet: PiSight has no
    persistent store, and the counter resets on restart. That is documented in the README
    and shown on the Overview screen.

    The set is bounded: once ``max_keys`` distinct devices have been seen, the oldest
    entries are evicted. A busy environment therefore cannot grow this without limit; the
    only consequence of eviction is that a long-departed device may be flagged "new" again
    if it returns.
    """

    def __init__(self, max_keys: int = 4096) -> None:
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self._max_keys = max_keys
        # dict preserves insertion order, giving FIFO eviction without another dependency.
        self._seen: dict[str, None] = {}

    @property
    def known_count(self) -> int:
        """Number of device keys currently remembered."""
        return len(self._seen)

    def mark(self, devices: Iterable[DeviceSummary]) -> tuple[DeviceSummary, ...]:
        """Return ``devices`` with :attr:`DeviceSummary.is_new` populated.

        The first snapshot a device appears in marks it new; subsequent snapshots do not.
        """
        marked: list[DeviceSummary] = []
        for device in devices:
            is_new = device.key not in self._seen
            if is_new:
                self._seen[device.key] = None
                while len(self._seen) > self._max_keys:
                    self._seen.pop(next(iter(self._seen)))
            marked.append(replace(device, is_new=is_new))
        return tuple(marked)

    def reset(self) -> None:
        """Forget every device key (used by tests and by the screenshot renderer)."""
        self._seen.clear()


def finalize_snapshot(
    snapshot: DashboardSnapshot,
    tracker: NewDeviceTracker,
) -> DashboardSnapshot:
    """Apply new-device marking to a provider-built snapshot.

    Both providers call this as their last step so the "new since start" semantics are
    identical in mock and live mode.
    """
    devices = tracker.mark(snapshot.devices)
    return replace(
        snapshot,
        devices=devices,
        new_device_count=sum(1 for device in devices if device.is_new),
    )
