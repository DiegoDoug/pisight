"""Bounded latest-value store handing snapshots from the poller to the renderer.

The renderer only ever wants the newest snapshot: a frame drawn from three-second-old data
is worthless once fresher data exists. So rather than a growing queue, this store keeps
exactly one snapshot slot. Publishing overwrites; obsolete snapshots are discarded at the
moment they are superseded. Memory is therefore constant regardless of how far the
renderer falls behind the poller.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..models import DashboardSnapshot

__all__ = ["SnapshotState", "SnapshotStore"]


@dataclass(frozen=True, slots=True)
class SnapshotState:
    """An atomic view of everything the renderer needs to know about data freshness."""

    snapshot: DashboardSnapshot | None
    sequence: int
    consecutive_failures: int
    last_error: str | None
    #: Number of snapshots dropped because a newer one arrived first.
    dropped: int

    @property
    def has_data(self) -> bool:
        """True once at least one snapshot has been published."""
        return self.snapshot is not None


class SnapshotStore:
    """Thread-safe single-slot store.

    One writer (the polling thread) and one reader (the rendering thread) is the expected
    pattern, but the lock makes any number of either safe.
    """

    #: Capacity, in snapshots. Fixed at one by design; exposed so the bound is assertable.
    CAPACITY = 1

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: DashboardSnapshot | None = None
        self._sequence = 0
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._dropped = 0
        self._unread = False
        self._updated = threading.Event()

    def publish(self, snapshot: DashboardSnapshot) -> None:
        """Store ``snapshot`` as the newest value, discarding any unread predecessor."""
        with self._lock:
            if self._unread:
                # The renderer never saw the previous snapshot; it is superseded, not queued.
                self._dropped += 1
            self._snapshot = snapshot
            self._sequence += 1
            self._consecutive_failures = 0
            self._last_error = None
            self._unread = True
        self._updated.set()

    def record_failure(self, error: str) -> None:
        """Record a failed poll without discarding the last good snapshot."""
        with self._lock:
            self._consecutive_failures += 1
            self._last_error = error

    def replace_snapshot(self, snapshot: DashboardSnapshot) -> None:
        """Swap in a re-marked snapshot (for example flagged offline) without resetting counters.

        Used when a poll fails and the coordinator wants the retained observations to stay
        on screen while the capture status shows the link is down.
        """
        with self._lock:
            self._snapshot = snapshot
            self._sequence += 1
            self._unread = True
        self._updated.set()

    def get(self) -> SnapshotState:
        """Return the current state. Never blocks for longer than the lock is held."""
        with self._lock:
            self._unread = False
            self._updated.clear()
            return SnapshotState(
                snapshot=self._snapshot,
                sequence=self._sequence,
                consecutive_failures=self._consecutive_failures,
                last_error=self._last_error,
                dropped=self._dropped,
            )

    def wait_for_update(self, timeout: float | None = None) -> bool:
        """Block until a new snapshot is published or ``timeout`` elapses.

        Used by the smoke test and by tests; the renderer does not use this, because it
        draws on a fixed frame cadence rather than on data arrival.
        """
        return self._updated.wait(timeout)

    @property
    def sequence(self) -> int:
        """Monotonic count of published snapshots."""
        with self._lock:
            return self._sequence
