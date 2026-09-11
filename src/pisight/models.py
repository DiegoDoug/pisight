"""Immutable domain models shared by every PiSight provider, screen and test.

Everything here is a frozen dataclass or an enum. The rendering thread reads snapshots
concurrently with the polling thread writing them, so snapshots must never be mutated
after construction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "AlertSummary",
    "Band",
    "CaptureStatus",
    "ChannelSummary",
    "DashboardSnapshot",
    "DatasourceSummary",
    "DeviceSummary",
    "DeviceType",
    "HostHealth",
    "Severity",
]


class Band(StrEnum):
    """Coarse radio band classification."""

    BAND_2GHZ = "2.4 GHz"
    BAND_5GHZ = "5 GHz"
    BAND_6GHZ = "6 GHz"
    UNKNOWN = "Unknown"

    @property
    def short_label(self) -> str:
        """Compact label that fits the 320x240 canvas."""
        return {
            Band.BAND_2GHZ: "2.4G",
            Band.BAND_5GHZ: "5G",
            Band.BAND_6GHZ: "6G",
            Band.UNKNOWN: "?",
        }[self]


class DeviceType(StrEnum):
    """Normalized device classification.

    Kismet emits free-form phy-specific strings such as "Wi-Fi AP" or "Wi-Fi Device".
    Unknown strings normalize to :attr:`UNKNOWN` rather than raising.
    """

    ACCESS_POINT = "AP"
    CLIENT = "Client"
    BRIDGED = "Bridged"
    ADHOC = "Ad-Hoc"
    WDS = "WDS"
    UNKNOWN = "Unknown"

    @property
    def short_label(self) -> str:
        """Four-character label used by the Devices screen."""
        return {
            DeviceType.ACCESS_POINT: "AP",
            DeviceType.CLIENT: "STA",
            DeviceType.BRIDGED: "BRDG",
            DeviceType.ADHOC: "ADHC",
            DeviceType.WDS: "WDS",
            DeviceType.UNKNOWN: "?",
        }[self]


class Severity(StrEnum):
    """Alert severity, mapped from Kismet's numeric severity scale."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """Sortable rank; higher is more severe."""
        return {
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
            Severity.CRITICAL: 4,
        }[self]

    @property
    def is_elevated(self) -> bool:
        """True for severities that deserve a red treatment."""
        return self.rank >= Severity.HIGH.rank


@dataclass(frozen=True, slots=True)
class DeviceSummary:
    """A single observed device, already normalized and privacy-masked at render time."""

    key: str
    mac: str
    display_name: str
    device_type: DeviceType = DeviceType.UNKNOWN
    channel: str | None = None
    frequency_khz: int | None = None
    band: Band = Band.UNKNOWN
    signal_dbm: int | None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    packets: int = 0
    crypt: str | None = None
    is_new: bool = False

    @property
    def signal_label(self) -> str:
        """Signal rendered for the UI, or a dash when Kismet reported nothing usable."""
        if self.signal_dbm is None:
            return "--"
        return f"{self.signal_dbm}dBm"


@dataclass(frozen=True, slots=True)
class ChannelSummary:
    """Observed activity on one channel."""

    channel: str
    band: Band = Band.UNKNOWN
    frequency_khz: int | None = None
    device_count: int = 0
    packet_count: int = 0


@dataclass(frozen=True, slots=True)
class AlertSummary:
    """A Kismet alert, normalized to PiSight's five severity levels.

    ``text`` is a human-readable description. PiSight never stores or renders raw packet
    content, so anything beyond the description is intentionally dropped.
    """

    timestamp: float
    severity: Severity
    header: str
    text: str
    source_mac: str | None = None


@dataclass(frozen=True, slots=True)
class DatasourceSummary:
    """State of one Kismet capture source (for PiSight v1, the PAU0B adapter)."""

    uuid: str
    name: str
    interface: str
    running: bool = False
    hopping: bool = False
    channel: str | None = None
    packets: int = 0
    error: str | None = None

    @property
    def state_label(self) -> str:
        """Short textual state; status is never conveyed by colour alone."""
        if self.error:
            return "ERROR"
        if not self.running:
            return "STOPPED"
        return "HOP" if self.hopping else "LOCKED"


@dataclass(frozen=True, slots=True)
class HostHealth:
    """Read-only host metrics. Values are ``None`` when unavailable off-Pi."""

    cpu_temp_c: float | None = None
    disk_total_bytes: int | None = None
    disk_free_bytes: int | None = None
    uptime_seconds: float = 0.0
    platform: str = "unknown"
    architecture: str = "unknown"
    display_available: bool = False
    storage_path: str = ""

    @property
    def disk_free_pct(self) -> float | None:
        """Free storage as a percentage, or ``None`` when the path is unreadable."""
        if not self.disk_total_bytes or self.disk_free_bytes is None:
            return None
        return 100.0 * self.disk_free_bytes / self.disk_total_bytes

    @property
    def storage_is_low(self) -> bool:
        """True when free storage has dropped below the 15% warning threshold."""
        pct = self.disk_free_pct
        return pct is not None and pct < 15.0


@dataclass(frozen=True, slots=True)
class CaptureStatus:
    """Capture-engine state as reported by Kismet, plus PiSight's view of the link."""

    kismet_online: bool = False
    kismet_version: str | None = None
    packets_per_second: float = 0.0
    total_packets: int = 0
    total_devices: int = 0
    current_channel: str | None = None
    hopping: bool = False
    last_error: str | None = None

    @property
    def channel_label(self) -> str:
        """``HOP`` while channel hopping, otherwise the locked channel."""
        if self.hopping:
            return f"HOP {self.current_channel}" if self.current_channel else "HOP"
        return self.current_channel or "--"


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """One immutable frame of dashboard state handed from the poller to the renderer.

    ``generated_at`` is wall-clock (for display) and ``monotonic_at`` is the basis for
    staleness, so a system clock step cannot make a fresh snapshot look stale.
    """

    generated_at: float = field(default_factory=time.time)
    monotonic_at: float = field(default_factory=time.monotonic)
    capture: CaptureStatus = field(default_factory=CaptureStatus)
    host: HostHealth = field(default_factory=HostHealth)
    devices: tuple[DeviceSummary, ...] = ()
    channels: tuple[ChannelSummary, ...] = ()
    alerts: tuple[AlertSummary, ...] = ()
    datasources: tuple[DatasourceSummary, ...] = ()
    new_device_count: int = 0
    source_name: str = "unknown"
    warnings: tuple[str, ...] = ()

    def age_seconds(self, *, now_monotonic: float | None = None) -> float:
        """Seconds elapsed since this snapshot was produced."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return max(0.0, now - self.monotonic_at)

    def is_stale(self, threshold_seconds: float, *, now_monotonic: float | None = None) -> bool:
        """True when the snapshot is older than ``threshold_seconds``."""
        return self.age_seconds(now_monotonic=now_monotonic) > threshold_seconds

    @property
    def access_point_count(self) -> int:
        """Number of observed access points in this snapshot's device window."""
        return sum(1 for d in self.devices if d.device_type is DeviceType.ACCESS_POINT)

    @property
    def client_count(self) -> int:
        """Number of observed client stations in this snapshot's device window."""
        return sum(1 for d in self.devices if d.device_type is DeviceType.CLIENT)

    def band_count(self, band: Band) -> int:
        """Number of observed devices on ``band``."""
        return sum(1 for d in self.devices if d.band is band)

    @property
    def highest_severity(self) -> Severity | None:
        """Most severe alert in this snapshot, or ``None`` when there are no alerts."""
        if not self.alerts:
            return None
        return max((a.severity for a in self.alerts), key=lambda s: s.rank)
