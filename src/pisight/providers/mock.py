"""Deterministic mock data source for desktop development, tests and screenshots.

Mock mode is a first-class implementation of the provider protocol, not hardcoded screen
text. It simulates a plausible radio environment -- realistic 2.4 and 5 GHz channels,
access points, clients and bridged devices, a drifting packet rate, fluctuating signal
levels, devices arriving over time, alerts across all five severities, and a healthy
datasource -- so every screen can be exercised without a radio.

It also simulates the failure modes the UI has to survive:

* :meth:`MockDashboardProvider.simulate_kismet_offline` -- the provider raises, exactly as
  the live provider does when Kismet is down;
* :meth:`MockDashboardProvider.simulate_malformed_response` -- a bad payload reaches the
  real parsers, so recovery is exercised end to end rather than faked;
* :meth:`MockDashboardProvider.simulate_low_storage` -- host health reports a nearly full
  disk;
* :meth:`MockDashboardProvider.simulate_stale` -- snapshots stop advancing.

Seeding is explicit. With the same ``seed`` and ``evolving=False`` the provider yields the
identical snapshot every call, which is what makes the screenshot and layout tests stable.
"""

from __future__ import annotations

import random
import time
from dataclasses import replace

from ..models import (
    AlertSummary,
    Band,
    CaptureStatus,
    ChannelSummary,
    DashboardSnapshot,
    DatasourceSummary,
    DeviceSummary,
    DeviceType,
    HostHealth,
    Severity,
)
from .base import NewDeviceTracker, ProviderUnavailable, finalize_snapshot
from .parsers import MalformedResponse, normalize_band, parse_devices

__all__ = ["MOCK_MALFORMED_PAYLOAD", "MockDashboardProvider"]

#: Synthetic 2.4 GHz channel plan: the three non-overlapping channels carry most traffic.
_CHANNELS_2GHZ: tuple[tuple[str, int], ...] = (
    ("1", 2412000),
    ("6", 2437000),
    ("11", 2462000),
    ("3", 2422000),
    ("9", 2452000),
)

#: Synthetic 5 GHz channel plan across UNII-1 and UNII-3.
_CHANNELS_5GHZ: tuple[tuple[str, int], ...] = (
    ("36", 5180000),
    ("44", 5220000),
    ("48", 5240000),
    ("149", 5745000),
    ("157", 5785000),
    ("161", 5805000),
)

#: Synthetic SSIDs. These are invented names -- no real network is represented anywhere in
#: this repository, and none of these MACs are routable OUIs in use.
_SSIDS: tuple[str, ...] = (
    "harbor-guest",
    "lab-mesh-2g",
    "Orchard_5G",
    "field-unit-07",
    "WORKSHOP-IOT",
    "atrium-public-wifi-north-wing",
    "bench-test",
    "svc-relay",
)

#: Synthetic locally-administered MAC prefixes (the 0x02 bit marks them as non-vendor).
_MAC_PREFIXES: tuple[str, ...] = ("02:00:5E", "02:1A:C4", "02:9B:70", "02:44:11")

_CRYPT_CHOICES: tuple[str | None, ...] = ("WPA2", "WPA3", "WPA2-E", "Open", None)

_ALERT_TEMPLATES: tuple[tuple[str, Severity, str], ...] = (
    ("DEAUTHFLOOD", Severity.HIGH, "Deauthentication burst observed on channel 6"),
    ("PROBENOJOIN", Severity.LOW, "Client probing for networks without joining"),
    ("APSPOOF", Severity.CRITICAL, "Beacon mismatch for a previously seen BSSID"),
    ("DISASSOCTRAFFIC", Severity.MEDIUM, "Traffic seen after disassociation frame"),
    ("SOURCEERROR", Severity.INFO, "Datasource resumed after brief channel gap"),
    ("CHANCHANGE", Severity.INFO, "Access point changed operating channel"),
    ("NOCLIENTMFP", Severity.MEDIUM, "Management frame protection not negotiated"),
)

#: A payload shaped like a Kismet device response but structurally wrong: used to prove the
#: parsers reject bad data without crashing the renderer.
MOCK_MALFORMED_PAYLOAD: str = "this is not a device list"

#: Wall-clock instant the deterministic (non-evolving) simulation pretends it is:
#: 2024-06-01T12:10:00Z. The screenshot renderer uses the same constant for its status-bar
#: clock, so rendered device and alert ages read as seconds and minutes rather than as the
#: months that would separate two unrelated fixed timestamps.
DETERMINISTIC_WALL_TIME: float = 1_717_243_800.0


class MockDashboardProvider:
    """Generates synthetic dashboard snapshots.

    Args:
        seed: RNG seed. Identical seeds produce identical output.
        evolving: when ``True`` the environment drifts over time (new devices arrive,
            signals move, the packet rate wanders) for demonstrations. When ``False`` the
            provider is fully deterministic and repeatable, which is what tests use.
        device_count: how many devices the simulated environment starts with.
    """

    name = "mock"

    def __init__(
        self,
        *,
        seed: int = 1337,
        evolving: bool = True,
        device_count: int = 14,
        tracker: NewDeviceTracker | None = None,
        storage_path: str = "/var/log/kismet",
    ) -> None:
        self._seed = seed
        self._evolving = evolving
        self._rng = random.Random(seed)  # noqa: S311 - simulation data, not cryptography
        self._tracker = tracker if tracker is not None else NewDeviceTracker()
        self._storage_path = storage_path
        self._tick = 0
        self._start_wall = time.time() if evolving else DETERMINISTIC_WALL_TIME
        self._devices: list[DeviceSummary] = [
            self._make_device(index) for index in range(max(1, device_count))
        ]

        self._offline = False
        self._malformed = False
        self._low_storage = False
        self._frozen_snapshot: DashboardSnapshot | None = None

    # ---------------------------------------------------------------------------------
    # Failure simulation
    # ---------------------------------------------------------------------------------

    def simulate_kismet_offline(self, offline: bool = True) -> None:
        """Make subsequent fetches raise :class:`ProviderUnavailable`, as a dead Kismet would."""
        self._offline = offline

    def simulate_malformed_response(self, malformed: bool = True) -> None:
        """Make the next fetch push a structurally invalid payload through the real parsers."""
        self._malformed = malformed

    def simulate_low_storage(self, low: bool = True) -> None:
        """Report a nearly full capture volume so the low-storage warning can be seen."""
        self._low_storage = low

    def simulate_stale(self, stale: bool = True) -> None:
        """Freeze snapshot generation so the staleness indicator engages."""
        if stale:
            self._frozen_snapshot = self._frozen_snapshot or self._build_snapshot()
        else:
            self._frozen_snapshot = None

    # ---------------------------------------------------------------------------------
    # Synthetic environment
    # ---------------------------------------------------------------------------------

    def _make_device(self, index: int) -> DeviceSummary:
        """Build one synthetic device with a plausible type/band/signal combination."""
        rng = self._rng
        is_ap = index % 3 == 0
        prefer_5ghz = rng.random() < 0.45
        channel, frequency = rng.choice(_CHANNELS_5GHZ if prefer_5ghz else _CHANNELS_2GHZ)

        if is_ap:
            device_type = DeviceType.ACCESS_POINT
            name = _SSIDS[index % len(_SSIDS)]
        elif index % 7 == 5:
            device_type = DeviceType.BRIDGED
            name = ""
        else:
            device_type = DeviceType.CLIENT
            name = ""

        prefix = _MAC_PREFIXES[index % len(_MAC_PREFIXES)]
        octets = ":".join(
            f"{(index * factor + offset) % 256:02X}"
            for factor, offset in ((37, 0), (11, 3), (53, 7))
        )
        mac = f"{prefix}:{octets}"
        first_seen = self._start_wall - rng.uniform(30, 3600)
        return DeviceSummary(
            key=f"MOCK_{index:04d}",
            mac=mac,
            display_name=name,
            device_type=device_type,
            channel=channel,
            frequency_khz=frequency,
            band=normalize_band(channel, frequency),
            signal_dbm=rng.randint(-88, -38),
            first_seen=first_seen,
            last_seen=self._start_wall - rng.uniform(0, 45),
            packets=rng.randint(12, 9000),
            crypt=rng.choice(_CRYPT_CHOICES) if is_ap else None,
        )

    def _advance(self) -> None:
        """Move the simulated environment forward by one poll."""
        rng = self._rng
        now = time.time() if self._evolving else self._start_wall + self._tick

        for index, device in enumerate(self._devices):
            drift = rng.randint(-4, 4)
            signal = device.signal_dbm if device.signal_dbm is not None else -70
            new_signal = max(-92, min(-30, signal + drift))
            touched = rng.random() < 0.55
            self._devices[index] = replace(
                device,
                signal_dbm=new_signal,
                last_seen=now - rng.uniform(0, 20) if touched else device.last_seen,
                packets=device.packets + (rng.randint(1, 140) if touched else 0),
            )

        # A new device arrives occasionally, and the population stays bounded.
        if rng.random() < 0.35:
            arrival = self._make_device(len(self._devices) + self._tick)
            self._devices.append(replace(arrival, first_seen=now, last_seen=now))
        if len(self._devices) > 40:
            self._devices.sort(key=lambda d: d.last_seen, reverse=True)
            del self._devices[40:]

    def _channels(self) -> tuple[ChannelSummary, ...]:
        """Aggregate the simulated device population into per-channel counts."""
        buckets: dict[str, ChannelSummary] = {}
        for channel, frequency in _CHANNELS_2GHZ + _CHANNELS_5GHZ:
            buckets[channel] = ChannelSummary(
                channel=channel,
                band=normalize_band(channel, frequency),
                frequency_khz=frequency,
                device_count=0,
                packet_count=0,
            )
        for device in self._devices:
            if device.channel is None or device.channel not in buckets:
                continue
            current = buckets[device.channel]
            buckets[device.channel] = replace(
                current,
                device_count=current.device_count + 1,
                packet_count=current.packet_count + device.packets,
            )
        summaries = sorted(
            buckets.values(), key=lambda c: (-c.device_count, -c.packet_count, c.channel)
        )
        return tuple(summaries)

    def _alerts(self) -> tuple[AlertSummary, ...]:
        """Produce a stable, newest-first alert list spanning several severities."""
        now = time.time() if self._evolving else self._start_wall
        count = 3 + (self._tick % 4)
        alerts: list[AlertSummary] = []
        for index in range(count):
            header, severity, text = _ALERT_TEMPLATES[(index + self._tick) % len(_ALERT_TEMPLATES)]
            alerts.append(
                AlertSummary(
                    timestamp=now - index * 47.0,
                    severity=severity,
                    header=header,
                    text=text,
                    source_mac=self._devices[index % len(self._devices)].mac,
                )
            )
        alerts.sort(key=lambda a: a.timestamp, reverse=True)
        return tuple(alerts)

    def _host_health(self) -> HostHealth:
        """Synthetic host health, including the optional low-storage condition."""
        total = 512 * 1024**3
        free = int(total * (0.07 if self._low_storage else 0.62))
        return HostHealth(
            cpu_temp_c=47.5 + (self._tick % 7) * 0.4,
            disk_total_bytes=total,
            disk_free_bytes=free,
            uptime_seconds=float(60 + self._tick * 2),
            platform="Linux",
            architecture="aarch64",
            display_available=True,
            storage_path=self._storage_path,
        )

    def _datasources(self) -> tuple[DatasourceSummary, ...]:
        """A single healthy PAU0B-style datasource, hopping across the channel plan."""
        plan = _CHANNELS_2GHZ + _CHANNELS_5GHZ
        channel = plan[self._tick % len(plan)][0]
        return (
            DatasourceSummary(
                uuid="00000000-0000-0000-0000-00000000mock"[:36],
                name="wlan1mon",
                interface="wlan1",
                running=True,
                hopping=True,
                channel=channel,
                packets=45_000 + self._tick * 137,
                error=None,
            ),
        )

    def _build_snapshot(self) -> DashboardSnapshot:
        """Assemble one snapshot from the current simulated environment."""
        devices = sorted(self._devices, key=lambda d: d.last_seen, reverse=True)
        channels = self._channels()
        datasources = self._datasources()
        rate = 40.0 + 30.0 * abs((self._tick % 20) - 10) / 10.0

        snapshot = DashboardSnapshot(
            capture=CaptureStatus(
                kismet_online=True,
                kismet_version="2024-07-R1 (mock)",
                packets_per_second=round(rate, 1),
                total_packets=sum(source.packets for source in datasources),
                total_devices=len(devices),
                current_channel=datasources[0].channel,
                hopping=True,
            ),
            host=self._host_health(),
            devices=tuple(devices),
            channels=channels,
            alerts=self._alerts(),
            datasources=datasources,
            source_name=self.name,
            warnings=(),
        )
        return finalize_snapshot(snapshot, self._tracker)

    # ---------------------------------------------------------------------------------
    # Provider protocol
    # ---------------------------------------------------------------------------------

    def fetch(self) -> DashboardSnapshot:
        """Return the next simulated snapshot, honouring any active failure simulation."""
        if self._offline:
            raise ProviderUnavailable("mock: simulated Kismet outage")

        if self._malformed:
            self._malformed = False
            # Push the bad payload through the real parser so recovery is genuinely tested.
            try:
                parse_devices(MOCK_MALFORMED_PAYLOAD)
            except MalformedResponse as exc:
                raise ProviderUnavailable(f"mock: {exc}") from exc

        if self._frozen_snapshot is not None:
            return self._frozen_snapshot

        if self._evolving:
            self._advance()
        self._tick += 1
        return self._build_snapshot()

    def close(self) -> None:
        """No resources to release; present to satisfy the provider protocol."""
        return None

    def reset(self) -> None:
        """Return the simulation to its seeded initial state."""
        self._rng = random.Random(self._seed)  # noqa: S311 - simulation data
        self._tick = 0
        self._tracker.reset()
        self._devices = [self._make_device(index) for index in range(14)]
        self._offline = False
        self._malformed = False
        self._low_storage = False
        self._frozen_snapshot = None


def screenshot_snapshot(seed: int = 20240501) -> DashboardSnapshot:
    """Build one deterministic snapshot for screenshot generation and layout tests.

    Includes at least one very long SSID, an unnamed client, a device with no signal
    reading and alerts at several severities, so the rendered output exercises the
    ellipsis, empty-value and severity paths rather than only the happy path.
    """
    provider = MockDashboardProvider(seed=seed, evolving=False, device_count=14)
    snapshot = provider.fetch()

    devices = list(snapshot.devices)
    if devices:
        devices[0] = replace(
            devices[0],
            display_name="atrium-public-wifi-north-wing-guest-portal-registration-desk",
            device_type=DeviceType.ACCESS_POINT,
            crypt="WPA3",
            band=Band.BAND_5GHZ,
            channel="149",
            is_new=True,
        )
    if len(devices) > 2:
        devices[2] = replace(devices[2], signal_dbm=None, display_name="")
    provider.close()
    return replace(snapshot, devices=tuple(devices))
