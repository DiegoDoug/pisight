"""Supervised background polling worker.

One daemon thread owns all network and filesystem I/O. It fetches a snapshot from the
provider, publishes it to a bounded latest-value store, and sleeps until the next poll. The
rendering thread never touches HTTP, never touches sysfs, and never blocks on either.

Failure handling, in order of preference:

1. a poll succeeds -- publish, reset backoff;
2. a poll fails and a previous snapshot exists -- keep the observations on screen, re-mark
   the capture status offline so the UI shows it, and back off;
3. a poll fails with nothing to retain -- record the error; the UI shows its connecting
   state.

Waiting always goes through :meth:`threading.Event.wait`, so the thread consumes no CPU
between polls and cancellation is immediate rather than waiting out a sleep.
"""

from __future__ import annotations

import logging
import random
import threading

from ..config import AppConfig
from ..providers.base import DashboardProvider, ProviderAuthError, ProviderError
from ..providers.kismet import degrade
from ..providers.parsers import MalformedResponse
from ..sanitize import sanitize_for_log
from .backoff import ExponentialBackoff
from .store import SnapshotStore

__all__ = ["PollingCoordinator"]

logger = logging.getLogger(__name__)


class PollingCoordinator:
    """Runs a provider on a background thread and publishes snapshots to a store."""

    def __init__(
        self,
        provider: DashboardProvider,
        config: AppConfig,
        *,
        store: SnapshotStore | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self.store = store if store is not None else SnapshotStore()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = ExponentialBackoff(
            initial=config.http.backoff_initial_seconds,
            factor=config.http.backoff_factor,
            maximum=config.http.backoff_max_seconds,
            jitter=config.http.backoff_jitter,
            rng=rng,
        )

    # ---------------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """True while the worker thread is alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the worker thread. Calling twice is a no-op."""
        if self.running:
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="pisight-poller", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the worker to finish and wait for it, then close the provider.

        Safe to call from a signal handler or an atexit hook, and safe to call twice.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("polling thread did not stop within %.1fs", timeout)
        self._thread = None
        try:
            self._provider.close()
        except Exception:
            logger.debug("provider close failed during shutdown", exc_info=True)

    def __enter__(self) -> PollingCoordinator:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # ---------------------------------------------------------------------------------
    # Worker
    # ---------------------------------------------------------------------------------

    def poll_once(self) -> bool:
        """Perform exactly one poll cycle and return whether it succeeded.

        Exposed separately from the thread loop so tests can drive the failure, retention
        and recovery behaviour deterministically without threads or sleeps.
        """
        try:
            snapshot = self._provider.fetch()
        except ProviderAuthError as exc:
            self._handle_failure(str(exc), auth=True)
            return False
        except (ProviderError, MalformedResponse) as exc:
            self._handle_failure(f"{type(exc).__name__}: {exc}")
            return False
        except Exception as exc:
            logger.exception("unexpected provider failure")
            self._handle_failure(f"unexpected: {type(exc).__name__}: {exc}")
            return False

        self.store.publish(snapshot)
        self._backoff.reset()
        return True

    def _handle_failure(self, error: str, *, auth: bool = False) -> None:
        """Record a failed poll and keep the last known snapshot visible but marked offline."""
        message = sanitize_for_log(error, max_length=160)
        if auth:
            logger.error("Kismet authentication failed: %s", message)
        else:
            logger.warning("poll failed: %s", message)

        self.store.record_failure(message)
        state = self.store.get()
        if state.snapshot is not None and state.snapshot.capture.kismet_online:
            self.store.replace_snapshot(degrade(state.snapshot, message))

    def _run(self) -> None:
        """Thread body: poll, publish, wait; back off on failure; exit cleanly on stop."""
        logger.info("polling worker started (source=%s)", self._provider.name)
        while not self._stop.is_set():
            succeeded = self.poll_once()
            delay = (
                self._config.polling.interval_seconds if succeeded else self._backoff.next_delay()
            )
            if not succeeded:
                logger.debug("retrying in %.2fs (failure %d)", delay, self._backoff.failures)
            # Event.wait both paces the loop and makes stop() take effect immediately.
            if self._stop.wait(delay):
                break
        logger.info("polling worker stopped")
