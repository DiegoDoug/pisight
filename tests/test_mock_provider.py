"""Mock mode: determinism, realistic content, and the failure modes it simulates."""

from __future__ import annotations

import pytest

from pisight.models import Band, DeviceType, Severity
from pisight.providers.base import NewDeviceTracker, ProviderUnavailable
from pisight.providers.mock import (
    DETERMINISTIC_WALL_TIME,
    MockDashboardProvider,
    screenshot_snapshot,
)


def test_deterministic_seed_reproduces_identical_snapshots() -> None:
    def first_snapshot() -> object:
        provider = MockDashboardProvider(seed=2024, evolving=False)
        try:
            snapshot = provider.fetch()
            return [
                (d.key, d.mac, d.display_name, d.signal_dbm, d.channel) for d in snapshot.devices
            ]
        finally:
            provider.close()

    assert first_snapshot() == first_snapshot()


def test_different_seeds_produce_different_environments() -> None:
    def signals(seed: int) -> list[int | None]:
        provider = MockDashboardProvider(seed=seed, evolving=False)
        try:
            return [d.signal_dbm for d in provider.fetch().devices]
        finally:
            provider.close()

    assert signals(1) != signals(2)


def test_non_evolving_provider_repeats_itself() -> None:
    provider = MockDashboardProvider(seed=5, evolving=False)
    try:
        first = provider.fetch()
        second = provider.fetch()
    finally:
        provider.close()

    assert [d.key for d in first.devices] == [d.key for d in second.devices]
    assert [d.signal_dbm for d in first.devices] == [d.signal_dbm for d in second.devices]


def test_evolving_provider_changes_over_time() -> None:
    provider = MockDashboardProvider(seed=5, evolving=True)
    try:
        first = provider.fetch()
        for _ in range(5):
            latest = provider.fetch()
    finally:
        provider.close()

    changed = [d.signal_dbm for d in first.devices] != [d.signal_dbm for d in latest.devices]
    assert changed or len(latest.devices) != len(first.devices)


def test_simulation_covers_both_bands_and_several_device_types() -> None:
    provider = MockDashboardProvider(seed=99, evolving=False, device_count=20)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    bands = {device.band for device in snapshot.devices}
    types = {device.device_type for device in snapshot.devices}
    assert Band.BAND_2GHZ in bands
    assert Band.BAND_5GHZ in bands
    assert DeviceType.ACCESS_POINT in types
    assert DeviceType.CLIENT in types


def test_channels_span_realistic_plans() -> None:
    provider = MockDashboardProvider(seed=11, evolving=False)
    try:
        channels = {channel.channel for channel in provider.fetch().channels}
    finally:
        provider.close()

    assert {"1", "6", "11"} <= channels  # 2.4 GHz non-overlapping
    assert {"36", "149"} <= channels  # 5 GHz UNII-1 and UNII-3


def test_alerts_cover_several_severities() -> None:
    provider = MockDashboardProvider(seed=3, evolving=True)
    try:
        severities: set[Severity] = set()
        for _ in range(12):
            severities.update(alert.severity for alert in provider.fetch().alerts)
    finally:
        provider.close()

    assert len(severities) >= 3
    assert Severity.CRITICAL in severities


def test_packet_rate_varies() -> None:
    provider = MockDashboardProvider(seed=8, evolving=True)
    try:
        rates = {provider.fetch().capture.packets_per_second for _ in range(15)}
    finally:
        provider.close()
    assert len(rates) > 1


def test_new_devices_arrive_over_time() -> None:
    provider = MockDashboardProvider(seed=17, evolving=True)
    try:
        first = provider.fetch()
        counts = [len(provider.fetch().devices) for _ in range(40)]
    finally:
        provider.close()

    assert max(counts) > len(first.devices)


def test_device_population_stays_bounded() -> None:
    provider = MockDashboardProvider(seed=21, evolving=True)
    try:
        counts = [len(provider.fetch().devices) for _ in range(300)]
    finally:
        provider.close()
    assert max(counts) <= 40


def test_datasource_is_healthy_by_default() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    assert snapshot.capture.kismet_online is True
    assert len(snapshot.datasources) == 1
    source = snapshot.datasources[0]
    assert source.running is True
    assert source.error is None
    assert source.state_label == "HOP"


# --- Simulated failure modes -----------------------------------------------------------


def test_simulated_kismet_outage_raises_like_the_live_provider() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False)
    try:
        provider.fetch()
        provider.simulate_kismet_offline()
        with pytest.raises(ProviderUnavailable):
            provider.fetch()

        provider.simulate_kismet_offline(False)
        assert provider.fetch() is not None
    finally:
        provider.close()


def test_simulated_malformed_response_goes_through_the_real_parser() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False)
    try:
        provider.simulate_malformed_response()
        with pytest.raises(ProviderUnavailable):
            provider.fetch()
        # One bad response only: the next fetch recovers.
        assert provider.fetch() is not None
    finally:
        provider.close()


def test_simulated_low_storage_trips_the_warning() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False)
    try:
        assert provider.fetch().host.storage_is_low is False
        provider.simulate_low_storage()
        snapshot = provider.fetch()
    finally:
        provider.close()

    assert snapshot.host.storage_is_low is True
    assert snapshot.host.disk_free_pct is not None
    assert snapshot.host.disk_free_pct < 15.0


def test_simulated_staleness_freezes_the_snapshot() -> None:
    """While frozen, every fetch returns the same snapshot, so its age keeps growing."""
    import time

    provider = MockDashboardProvider(seed=1, evolving=True)
    try:
        provider.fetch()
        provider.simulate_stale()
        frozen = provider.fetch()
        # Generous relative to the ~16ms timer granularity on Windows.
        time.sleep(0.2)
        again = provider.fetch()
    finally:
        provider.close()

    assert frozen is again
    assert frozen.monotonic_at == again.monotonic_at
    # The snapshot ages even though it is being re-served.
    assert again.age_seconds() >= 0.1
    assert again.is_stale(0.05) is True
    assert again.is_stale(1000.0) is False


def test_reset_returns_to_the_seeded_start() -> None:
    provider = MockDashboardProvider(seed=33, evolving=True)
    try:
        first = [d.key for d in provider.fetch().devices]
        for _ in range(10):
            provider.fetch()
        provider.reset()
        after = [d.key for d in provider.fetch().devices]
    finally:
        provider.close()

    assert after == first


def test_provider_uses_the_deterministic_clock_when_not_evolving() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    for device in snapshot.devices:
        # Every observation falls within an hour of the deterministic instant.
        assert abs(device.last_seen - DETERMINISTIC_WALL_TIME) < 3700


# --- New-device tracking ---------------------------------------------------------------


def test_new_marks_only_the_first_sighting() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False, tracker=NewDeviceTracker())
    try:
        first = provider.fetch()
        second = provider.fetch()
    finally:
        provider.close()

    assert first.new_device_count == len(first.devices)
    assert second.new_device_count == 0


def test_tracker_is_bounded() -> None:
    """A busy environment must not grow the known-device set without limit."""
    from pisight.models import DeviceSummary

    tracker = NewDeviceTracker(max_keys=10)
    for index in range(500):
        tracker.mark([DeviceSummary(key=f"k{index}", mac="02:00:5E:00:00:01", display_name="")])

    assert tracker.known_count == 10


def test_tracker_reset_forgets_everything() -> None:
    from pisight.models import DeviceSummary

    tracker = NewDeviceTracker()
    device = DeviceSummary(key="k1", mac="02:00:5E:00:00:01", display_name="")
    assert tracker.mark([device])[0].is_new is True
    assert tracker.mark([device])[0].is_new is False

    tracker.reset()
    assert tracker.mark([device])[0].is_new is True


def test_tracker_rejects_a_non_positive_bound() -> None:
    with pytest.raises(ValueError):
        NewDeviceTracker(max_keys=0)


# --- Screenshot snapshot ---------------------------------------------------------------


def test_screenshot_snapshot_is_deterministic() -> None:
    first = screenshot_snapshot()
    second = screenshot_snapshot()
    assert [d.key for d in first.devices] == [d.key for d in second.devices]
    assert [a.header for a in first.alerts] == [a.header for a in second.alerts]


def test_screenshot_snapshot_exercises_the_hard_cases() -> None:
    snapshot = screenshot_snapshot()
    assert any(len(d.display_name) > 40 for d in snapshot.devices), "no long SSID to ellipsize"
    assert any(d.signal_dbm is None for d in snapshot.devices), "no missing-signal device"
    assert any(not d.display_name for d in snapshot.devices), "no unnamed device"
    assert len({a.severity for a in snapshot.alerts}) >= 2, "alerts lack severity variety"


def test_mock_never_emits_a_routable_vendor_mac() -> None:
    """Simulated MACs must be locally administered so they cannot collide with real kit."""
    provider = MockDashboardProvider(seed=7, evolving=True)
    try:
        for _ in range(30):
            for device in provider.fetch().devices:
                first_octet = int(device.mac.split(":")[0], 16)
                assert first_octet & 0b10, f"{device.mac} is not locally administered"
    finally:
        provider.close()
