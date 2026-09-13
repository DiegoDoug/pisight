"""Snapshot store bounding, backoff behaviour, and coordinator failure handling."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import replace

import pytest

from pisight.config import AppConfig, HttpConfig, PollingConfig
from pisight.models import CaptureStatus, DashboardSnapshot
from pisight.polling.backoff import ExponentialBackoff
from pisight.polling.coordinator import PollingCoordinator
from pisight.polling.store import SnapshotStore
from pisight.providers.base import ProviderAuthError, ProviderUnavailable
from pisight.providers.parsers import MalformedResponse

# --- Store -----------------------------------------------------------------------------


def snapshot(online: bool = True, tag: int = 0) -> DashboardSnapshot:
    return DashboardSnapshot(
        capture=CaptureStatus(kismet_online=online, total_devices=tag),
        source_name="test",
    )


def test_store_starts_empty() -> None:
    store = SnapshotStore()
    state = store.get()
    assert state.snapshot is None
    assert state.has_data is False
    assert state.sequence == 0


def test_store_returns_the_newest_snapshot() -> None:
    store = SnapshotStore()
    store.publish(snapshot(tag=1))
    store.publish(snapshot(tag=2))
    assert store.get().snapshot is not None
    assert store.get().snapshot.capture.total_devices == 2  # type: ignore[union-attr]


def test_store_is_bounded_to_a_single_slot() -> None:
    """Publishing far faster than reading must not grow memory."""
    store = SnapshotStore()
    for tag in range(1000):
        store.publish(snapshot(tag=tag))

    state = store.get()
    assert SnapshotStore.CAPACITY == 1
    assert state.sequence == 1000
    # 999 snapshots were superseded before the reader saw them, and none were queued.
    assert state.dropped == 999
    assert state.snapshot.capture.total_devices == 999  # type: ignore[union-attr]


def test_store_does_not_count_a_consumed_snapshot_as_dropped() -> None:
    store = SnapshotStore()
    store.publish(snapshot(tag=1))
    store.get()
    store.publish(snapshot(tag=2))
    assert store.get().dropped == 0


def test_failure_does_not_discard_the_last_snapshot() -> None:
    store = SnapshotStore()
    store.publish(snapshot(tag=7))
    store.get()
    store.record_failure("connection refused")

    state = store.get()
    assert state.snapshot is not None
    assert state.snapshot.capture.total_devices == 7
    assert state.consecutive_failures == 1
    assert state.last_error == "connection refused"


def test_a_successful_publish_clears_the_failure_counter() -> None:
    store = SnapshotStore()
    store.record_failure("boom")
    store.record_failure("boom again")
    assert store.get().consecutive_failures == 2

    store.publish(snapshot())
    state = store.get()
    assert state.consecutive_failures == 0
    assert state.last_error is None


def test_wait_for_update_returns_on_publish() -> None:
    store = SnapshotStore()
    result: list[bool] = []

    def waiter() -> None:
        result.append(store.wait_for_update(timeout=2.0))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    store.publish(snapshot())
    thread.join(3.0)

    assert result == [True]


def test_wait_for_update_times_out_without_a_publish() -> None:
    assert SnapshotStore().wait_for_update(timeout=0.05) is False


def test_store_is_safe_under_concurrent_access() -> None:
    store = SnapshotStore()
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for tag in range(2000):
                store.publish(snapshot(tag=tag))
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                store.get()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10.0)

    assert not errors
    assert store.sequence == 2000


# --- Backoff ---------------------------------------------------------------------------


def test_backoff_grows_exponentially_without_jitter() -> None:
    backoff = ExponentialBackoff(initial=1.0, factor=2.0, maximum=30.0, jitter=0.0)
    assert [backoff.next_delay() for _ in range(6)] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_backoff_is_capped_at_the_maximum() -> None:
    backoff = ExponentialBackoff(initial=1.0, factor=3.0, maximum=5.0, jitter=0.0)
    for _ in range(20):
        assert backoff.next_delay() <= 5.0


def test_backoff_jitter_stays_within_the_configured_band() -> None:
    backoff = ExponentialBackoff(
        initial=10.0, factor=1.0, maximum=10.0, jitter=0.25, rng=random.Random(7)
    )
    delays = [backoff.next_delay() for _ in range(200)]
    assert all(7.5 <= delay <= 12.5 for delay in delays)
    # Jitter actually varies the delay rather than returning a constant.
    assert len(set(delays)) > 1


def test_backoff_is_deterministic_with_a_seeded_rng() -> None:
    def run() -> list[float]:
        backoff = ExponentialBackoff(jitter=0.25, rng=random.Random(99))
        return [backoff.next_delay() for _ in range(5)]

    assert run() == run()


def test_backoff_reset_returns_to_the_start() -> None:
    backoff = ExponentialBackoff(initial=1.0, factor=2.0, maximum=30.0, jitter=0.0)
    backoff.next_delay()
    backoff.next_delay()
    assert backoff.failures == 2
    backoff.reset()
    assert backoff.failures == 0
    assert backoff.next_delay() == 1.0


def test_backoff_never_returns_zero() -> None:
    backoff = ExponentialBackoff(initial=0.06, factor=1.0, maximum=0.06, jitter=1.0)
    assert all(backoff.next_delay() > 0 for _ in range(50))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial": 0},
        {"factor": 0.5},
        {"maximum": 0.1, "initial": 1.0},
        {"jitter": 1.5},
        {"jitter": -0.1},
    ],
)
def test_backoff_rejects_invalid_parameters(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ExponentialBackoff(**kwargs)  # type: ignore[arg-type]


# --- Coordinator -----------------------------------------------------------------------


class ScriptedProvider:
    """A provider that replays a scripted sequence of results and exceptions."""

    name = "scripted"

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls = 0
        self.closed = False

    def fetch(self) -> DashboardSnapshot:
        self.calls += 1
        item = self._script.pop(0) if self._script else snapshot()
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, DashboardSnapshot)
        return item

    def close(self) -> None:
        self.closed = True


def fast_config() -> AppConfig:
    """Configuration with tiny intervals, so threaded tests finish quickly."""
    return replace(
        AppConfig(),
        polling=PollingConfig(
            interval_seconds=0.01, stale_after_seconds=0.05, offline_after_seconds=0.1
        ),
        http=HttpConfig(backoff_initial_seconds=0.01, backoff_max_seconds=0.05, backoff_jitter=0.0),
    )


def test_poll_once_publishes_on_success() -> None:
    provider = ScriptedProvider([snapshot(tag=3)])
    coordinator = PollingCoordinator(provider, AppConfig())

    assert coordinator.poll_once() is True
    state = coordinator.store.get()
    assert state.snapshot.capture.total_devices == 3  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("connection refused"),
        ProviderAuthError("token rejected"),
        MalformedResponse("bad payload"),
        RuntimeError("a bug nobody anticipated"),
    ],
)
def test_poll_once_survives_every_failure_kind(error: BaseException) -> None:
    """No provider failure -- including an unexpected one -- may reach the UI thread."""
    coordinator = PollingCoordinator(ScriptedProvider([error]), AppConfig())

    assert coordinator.poll_once() is False
    assert coordinator.store.get().consecutive_failures == 1


def test_failure_retains_the_previous_snapshot_and_marks_it_offline() -> None:
    provider = ScriptedProvider([snapshot(tag=11), ProviderUnavailable("gone")])
    coordinator = PollingCoordinator(provider, AppConfig())

    assert coordinator.poll_once() is True
    assert coordinator.poll_once() is False

    state = coordinator.store.get()
    assert state.snapshot is not None
    # Observations survive...
    assert state.snapshot.capture.total_devices == 11
    # ...but the link is visibly down.
    assert state.snapshot.capture.kismet_online is False
    assert state.snapshot.capture.last_error is not None


def test_snapshot_keeps_ageing_while_offline() -> None:
    """The retained snapshot must not have its age reset, or staleness never engages."""
    original = snapshot(tag=5)
    provider = ScriptedProvider([original, ProviderUnavailable("gone")])
    coordinator = PollingCoordinator(provider, AppConfig())

    coordinator.poll_once()
    coordinator.poll_once()

    retained = coordinator.store.get().snapshot
    assert retained is not None
    assert retained.monotonic_at == original.monotonic_at


def test_recovery_republishes_fresh_data() -> None:
    provider = ScriptedProvider([snapshot(tag=1), ProviderUnavailable("gone"), snapshot(tag=2)])
    coordinator = PollingCoordinator(provider, AppConfig())

    coordinator.poll_once()
    coordinator.poll_once()
    assert coordinator.poll_once() is True

    state = coordinator.store.get()
    assert state.snapshot.capture.kismet_online is True  # type: ignore[union-attr]
    assert state.snapshot.capture.total_devices == 2  # type: ignore[union-attr]
    assert state.consecutive_failures == 0


def test_repeated_failures_are_counted() -> None:
    provider = ScriptedProvider([ProviderUnavailable("a"), ProviderUnavailable("b")])
    coordinator = PollingCoordinator(provider, AppConfig())
    coordinator.poll_once()
    coordinator.poll_once()
    assert coordinator.store.get().consecutive_failures == 2


def test_thread_starts_publishes_and_stops_cleanly() -> None:
    provider = ScriptedProvider([snapshot(tag=i) for i in range(200)])
    coordinator = PollingCoordinator(provider, fast_config())

    coordinator.start()
    try:
        assert coordinator.store.wait_for_update(timeout=3.0)
        assert coordinator.running
    finally:
        coordinator.stop(timeout=3.0)

    assert not coordinator.running
    assert provider.closed is True


def test_stop_is_idempotent_and_safe_before_start() -> None:
    coordinator = PollingCoordinator(ScriptedProvider([]), fast_config())
    coordinator.stop()
    coordinator.stop()
    assert not coordinator.running


def test_start_twice_creates_only_one_thread() -> None:
    coordinator = PollingCoordinator(ScriptedProvider([]), fast_config())
    coordinator.start()
    try:
        first = coordinator._thread
        coordinator.start()
        assert coordinator._thread is first
    finally:
        coordinator.stop(timeout=3.0)


def test_context_manager_starts_and_stops() -> None:
    provider = ScriptedProvider([snapshot()])
    with PollingCoordinator(provider, fast_config()) as coordinator:
        assert coordinator.running
    assert provider.closed is True


def test_a_persistently_failing_provider_stops_promptly() -> None:
    """Cancellation must interrupt a backoff wait instead of sitting it out."""
    provider = ScriptedProvider([ProviderUnavailable("down")] * 500)
    config = replace(
        AppConfig(),
        http=HttpConfig(backoff_initial_seconds=5.0, backoff_max_seconds=30.0, backoff_jitter=0.0),
    )
    coordinator = PollingCoordinator(provider, config)

    coordinator.start()
    time.sleep(0.1)
    started = time.monotonic()
    coordinator.stop(timeout=3.0)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, "stop() waited out the backoff instead of cancelling it"
    assert not coordinator.running


def test_polling_does_not_busy_loop() -> None:
    """A slow poll interval must not spin; the call count proves the thread sleeps."""
    provider = ScriptedProvider([snapshot() for _ in range(10_000)])
    config = replace(AppConfig(), polling=PollingConfig(interval_seconds=0.5))
    coordinator = PollingCoordinator(provider, config)

    coordinator.start()
    try:
        time.sleep(0.4)
    finally:
        coordinator.stop(timeout=3.0)

    # At a 0.5s interval, roughly one poll should have happened in 0.4s.
    assert provider.calls <= 3
