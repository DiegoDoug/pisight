"""Tolerant parsers for Kismet REST responses.

Kismet returns namespaced fields such as ``kismet.device.base.macaddr``. Depending on the
endpoint and the ``fields`` simplification used, those may arrive as flat dotted keys or
as nested objects, and optional fields may be missing entirely. Every function here:

* tolerates absent optional fields;
* handles unknown device types and severities;
* normalizes channels, frequencies and signal values;
* rejects fundamentally malformed responses by raising :class:`MalformedResponse`;
* never raises ``KeyError``, ``TypeError`` or ``IndexError`` on odd-but-plausible input.

Callers turn :class:`MalformedResponse` into a retained previous snapshot rather than a
crash, so a single bad response degrades the display instead of ending the session.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..models import (
    AlertSummary,
    Band,
    ChannelSummary,
    DatasourceSummary,
    DeviceSummary,
    DeviceType,
    Severity,
)
from ..sanitize import sanitize_text

__all__ = [
    "MalformedResponse",
    "channel_for_frequency_khz",
    "field_value",
    "normalize_band",
    "normalize_channel",
    "normalize_device_type",
    "normalize_frequency_khz",
    "normalize_signal_dbm",
    "parse_alerts",
    "parse_channels",
    "parse_datasources",
    "parse_devices",
    "parse_packet_rate",
    "parse_system_status",
    "parse_timestamp",
    "rrd_last_value",
]

#: Plausible Wi-Fi signal range, in dBm. Values outside this are treated as absent.
_MIN_SIGNAL_DBM = -120
_MAX_SIGNAL_DBM = 0

#: Longest device name PiSight keeps. Longer names are attacker-controlled bloat.
_MAX_NAME_CHARS = 64
_MAX_ALERT_TEXT_CHARS = 160


class MalformedResponse(ValueError):
    """Raised when a Kismet response is structurally unusable."""


# --------------------------------------------------------------------------------------
# Generic field access
# --------------------------------------------------------------------------------------


def field_value(record: Any, path: str, default: Any = None) -> Any:
    """Look up a namespaced Kismet field, flat-dotted or nested.

    ``field_value(rec, "kismet.device.base.macaddr")`` finds the value whether the record
    is ``{"kismet.device.base.macaddr": ...}`` or
    ``{"kismet": {"device": {"base": {"macaddr": ...}}}}``. Kismet's ``fields``
    simplification can also rename a field to a short alias, so an exact key match is
    tried first.
    """
    if not isinstance(record, Mapping):
        return default
    if path in record:
        return record[path]

    # Progressive nesting: some responses nest only part of the namespace.
    parts = path.split(".")
    for split in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:split])
        if head in record:
            nested = record[head]
            if isinstance(nested, Mapping):
                found = field_value(nested, ".".join(parts[split:]), _MISSING)
                if found is not _MISSING:
                    return found
    return default


class _Missing:
    """Sentinel distinguishing "absent" from a legitimate ``None`` value."""

    __slots__ = ()


_MISSING = _Missing()


def _first_value(record: Mapping[str, Any], paths: Sequence[str], default: Any = None) -> Any:
    """Return the first non-``None`` value among several candidate field paths."""
    for path in paths:
        value = field_value(record, path, None)
        if value is not None:
            return value
    return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    """Best-effort integer coercion that never raises."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and abs(value) != float("inf") else default
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            try:
                return int(float(value.strip()))
            except ValueError:
                return default
    return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    """Best-effort float coercion that never raises and rejects NaN/inf."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return default
    else:
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce Kismet's mixed boolean encodings (bool, 0/1, "true") to a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def rrd_last_value(value: Any) -> float | None:
    """Extract the most recent sample from a Kismet RRD structure.

    Accepts a bare number, an RRD object carrying ``kismet.common.rrd.last_value``, or an
    RRD whose only usable data is its minute vector, in which case the last entry wins.
    """
    number = _as_float(value)
    if number is not None:
        return number
    if not isinstance(value, Mapping):
        return None

    last = _as_float(_first_value(value, ("kismet.common.rrd.last_value", "last_value")))
    if last is not None:
        return last

    for key in ("kismet.common.rrd.minute_vec", "minute_vec", "kismet.common.rrd.serial_time"):
        vector = field_value(value, key)
        if isinstance(vector, Sequence) and not isinstance(vector, (str, bytes)) and vector:
            tail = _as_float(vector[-1])
            if tail is not None:
                return tail
    return None


# --------------------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------------------


def normalize_frequency_khz(value: Any) -> int | None:
    """Normalize a frequency to kHz.

    Kismet reports frequencies in kHz (``2412000``). Values that plainly arrived in MHz
    (``2412``) or GHz (``2.412``) are converted rather than discarded.
    """
    number = _as_float(value)
    if number is None or number <= 0:
        return None
    if number < 100:  # GHz, e.g. 2.412
        khz = number * 1_000_000
    elif number < 100_000:  # MHz, e.g. 2412
        khz = number * 1_000
    else:  # already kHz
        khz = number
    if not 1_000_000 <= khz <= 100_000_000:
        return None
    return round(khz)


def channel_for_frequency_khz(frequency_khz: int | None) -> str | None:
    """Convert a kHz frequency into an 802.11 channel number.

    Covers 2.4 GHz (1-14), 5 GHz (36-177) and 6 GHz (1-233, 6E). Returns ``None`` for
    frequencies that fall outside every Wi-Fi band.
    """
    if frequency_khz is None:
        return None
    mhz = frequency_khz / 1000.0

    if abs(mhz - 2484) < 1:
        return "14"
    if 2401 <= mhz <= 2495:
        channel = round((mhz - 2407) / 5)
        return str(channel) if 1 <= channel <= 13 else None
    if 5150 <= mhz <= 5895:
        channel = round((mhz - 5000) / 5)
        return str(channel) if 1 <= channel <= 200 else None
    if 5925 <= mhz <= 7125:
        channel = round((mhz - 5950) / 5)
        return str(channel) if 1 <= channel <= 233 else None
    return None


def normalize_channel(value: Any, frequency_khz: int | None = None) -> str | None:
    """Normalize a channel label, falling back to the frequency when needed.

    Kismet may report a channel as an integer, a plain string, or a width-qualified label
    such as ``"36HT80"``. Width suffixes are preserved because they are meaningful on the
    Channels screen, but empty and zero values fall back to the frequency.
    """
    text = sanitize_text(value, max_length=12).strip()
    if text and text not in ("0", "-1", "unknown", "Unknown"):
        return text
    return channel_for_frequency_khz(frequency_khz)


def normalize_band(channel: str | None, frequency_khz: int | None) -> Band:
    """Classify a channel/frequency pair into a band.

    Frequency wins when available; a bare channel number is ambiguous between 2.4 GHz
    and 6 GHz, so low channel numbers without a frequency are read as 2.4 GHz.
    """
    if frequency_khz is not None:
        mhz = frequency_khz / 1000.0
        if 2400 <= mhz <= 2500:
            return Band.BAND_2GHZ
        if 5150 <= mhz <= 5900:
            return Band.BAND_5GHZ
        if 5925 <= mhz <= 7125:
            return Band.BAND_6GHZ
        return Band.UNKNOWN

    if not channel:
        return Band.UNKNOWN
    digits = ""
    for char in channel:
        if char.isdigit():
            digits += char
        else:
            break
    number = _as_int(digits)
    if number is None:
        return Band.UNKNOWN
    if 1 <= number <= 14:
        return Band.BAND_2GHZ
    if 32 <= number <= 200:
        return Band.BAND_5GHZ
    return Band.UNKNOWN


def normalize_signal_dbm(value: Any) -> int | None:
    """Normalize a signal reading to dBm, rejecting implausible values.

    Kismet reports ``0`` when no signal has been recorded, and RSSI-style datasources can
    report small positive numbers; both are treated as "no reading" rather than as a
    remarkably strong signal.
    """
    number = _as_float(value)
    if number is None:
        return None
    dbm = round(number)
    if dbm == 0 or not _MIN_SIGNAL_DBM <= dbm <= _MAX_SIGNAL_DBM:
        return None
    return dbm


#: Substring matches applied in order; the first hit wins.
_TYPE_PATTERNS: tuple[tuple[str, DeviceType], ...] = (
    ("bridged", DeviceType.BRIDGED),
    ("ad-hoc", DeviceType.ADHOC),
    ("adhoc", DeviceType.ADHOC),
    ("ibss", DeviceType.ADHOC),
    ("wds", DeviceType.WDS),
    ("access point", DeviceType.ACCESS_POINT),
    ("ap", DeviceType.ACCESS_POINT),
    ("client", DeviceType.CLIENT),
    ("station", DeviceType.CLIENT),
)


def normalize_device_type(value: Any) -> DeviceType:
    """Map a Kismet device-type string onto :class:`DeviceType`.

    Unknown strings -- including phy types PiSight has never seen -- become
    :attr:`DeviceType.UNKNOWN` instead of raising.
    """
    text = sanitize_text(value).strip().lower()
    if not text:
        return DeviceType.UNKNOWN
    for needle, device_type in _TYPE_PATTERNS:
        if needle == "ap":
            # Guard the two-letter pattern against matching inside other words.
            if text == "ap" or text.endswith(" ap") or " ap " in f" {text} ":
                return device_type
            continue
        if needle in text:
            return device_type
    return DeviceType.UNKNOWN


def _normalize_severity(value: Any) -> Severity:
    """Map Kismet's numeric (0/5/10/15/20) or textual severity onto :class:`Severity`."""
    number = _as_int(value)
    if number is not None:
        if number >= 20:
            return Severity.CRITICAL
        if number >= 15:
            return Severity.HIGH
        if number >= 10:
            return Severity.MEDIUM
        if number >= 5:
            return Severity.LOW
        return Severity.INFO

    text = sanitize_text(value).strip().upper()
    for severity in Severity:
        if severity.value == text:
            return severity
    return Severity.INFO


def _records(payload: Any, *, container_keys: Sequence[str] = ()) -> list[Mapping[str, Any]]:
    """Coerce a payload into a list of record mappings.

    Handles a bare list and a list wrapped in a named container key such as Kismet's
    ``{"kismet.alert.list": [...]}``.

    An object that matches no container key is rejected rather than read as a single
    record. Kismet's list endpoints return lists; an object arriving instead is typically
    an error body, and quietly rendering that as "nothing observed" would tell the operator
    the air is quiet when in fact the query failed.
    """
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in container_keys:
            inner = field_value(payload, key)
            if isinstance(inner, Sequence) and not isinstance(inner, (str, bytes)):
                return [item for item in inner if isinstance(item, Mapping)]
        raise MalformedResponse(
            "expected a list of records, got an object with no recognised list field"
        )
    raise MalformedResponse(f"expected a list of records, got {type(payload).__name__}")


# --------------------------------------------------------------------------------------
# Endpoint parsers
# --------------------------------------------------------------------------------------


def parse_timestamp(payload: Any) -> float:
    """Parse ``/system/timestamp.json`` into a Unix timestamp."""
    if not isinstance(payload, Mapping):
        raise MalformedResponse("timestamp response is not an object")
    seconds = _as_float(_first_value(payload, ("kismet.system.timestamp.sec", "second", "sec")))
    if seconds is None:
        raise MalformedResponse("timestamp response has no usable seconds field")
    micro = _as_float(
        _first_value(payload, ("kismet.system.timestamp.usec", "microsecond", "usec")), 0.0
    )
    return seconds + (micro or 0.0) / 1_000_000.0


def parse_system_status(payload: Any) -> dict[str, Any]:
    """Parse ``/system/status.json`` into the handful of values PiSight displays."""
    if not isinstance(payload, Mapping):
        raise MalformedResponse("system status response is not an object")

    devices = _as_int(
        _first_value(payload, ("kismet.system.devices.count", "kismet.device.count")), 0
    )
    temp = _as_float(
        _first_value(payload, ("kismet.system.sensors.temp", "kismet.system.temperature"))
    )
    version = sanitize_text(
        _first_value(payload, ("kismet.system.version", "kismet.system.server_version")),
        max_length=24,
    )
    start_time = _as_float(
        _first_value(payload, ("kismet.system.timestamp.start_sec", "kismet.system.starttime"))
    )
    return {
        "total_devices": devices or 0,
        "cpu_temp_c": temp,
        "version": version or None,
        "server_start_sec": start_time,
    }


def parse_packet_rate(payload: Any) -> float:
    """Parse ``/packetchain/packet_stats.json`` into packets per second.

    Kismet exposes packet counters as RRDs; the most recent per-second sample of the
    processed-packet RRD is the rate PiSight shows.
    """
    if not isinstance(payload, Mapping):
        raise MalformedResponse("packet stats response is not an object")

    for key in (
        "kismet.packetchain.processed",
        "kismet.packetchain.peak_packets_rrd",
        "kismet.packetchain.packets_rrd",
        "packets_rrd",
    ):
        rate = rrd_last_value(field_value(payload, key))
        if rate is not None and rate >= 0:
            return rate
    return 0.0


def parse_channels(payload: Any, *, limit: int = 64) -> tuple[ChannelSummary, ...]:
    """Parse ``/channels/channels.json`` into per-channel summaries.

    The response maps a frequency string to a channel record. Records are bounded to
    ``limit`` entries, keeping the busiest channels, so an unusually wide scan cannot
    grow PiSight's memory without limit.
    """
    if not isinstance(payload, Mapping):
        raise MalformedResponse("channels response is not an object")

    frequency_map = _first_value(
        payload,
        ("kismet.channeltracker.frequency_map", "frequency_map", "kismet.channeltracker.channels"),
    )
    if frequency_map is None:
        frequency_map = payload if all(not k.startswith("kismet.") for k in payload) else {}
    if not isinstance(frequency_map, Mapping):
        raise MalformedResponse("channels response has no usable frequency map")

    summaries: list[ChannelSummary] = []
    for raw_key, record in frequency_map.items():
        if not isinstance(record, Mapping):
            continue
        frequency_khz = normalize_frequency_khz(
            _first_value(record, ("kismet.channelrec.frequency", "frequency"), raw_key)
        )
        channel = normalize_channel(
            _first_value(record, ("kismet.channelrec.channel", "channel")), frequency_khz
        )
        if channel is None:
            continue

        devices = rrd_last_value(
            _first_value(record, ("kismet.channelrec.device_rrd", "device_rrd", "devices"))
        )
        packets = rrd_last_value(
            _first_value(record, ("kismet.channelrec.packets_rrd", "packets_rrd", "packets"))
        )
        summaries.append(
            ChannelSummary(
                channel=channel,
                band=normalize_band(channel, frequency_khz),
                frequency_khz=frequency_khz,
                device_count=max(0, int(devices or 0)),
                packet_count=max(0, int(packets or 0)),
            )
        )

    summaries.sort(key=lambda c: (-c.device_count, -c.packet_count, c.channel))
    return tuple(summaries[:limit])


def parse_datasources(payload: Any) -> tuple[DatasourceSummary, ...]:
    """Parse ``/datasource/all_sources.json`` into datasource summaries."""
    records = _records(payload, container_keys=("kismet.datasource.list",))
    sources: list[DatasourceSummary] = []
    for record in records:
        uuid = sanitize_text(
            _first_value(record, ("kismet.datasource.uuid", "uuid")), max_length=40
        )
        name = sanitize_text(
            _first_value(record, ("kismet.datasource.name", "name")), max_length=24
        )
        interface = sanitize_text(
            _first_value(record, ("kismet.datasource.interface", "interface")), max_length=24
        )
        if not (uuid or name or interface):
            continue

        frequency = normalize_frequency_khz(
            _first_value(record, ("kismet.datasource.hopping_frequency", "frequency"))
        )
        error_text = sanitize_text(
            _first_value(record, ("kismet.datasource.error_reason", "error_reason")),
            max_length=80,
        )
        has_error = _as_bool(_first_value(record, ("kismet.datasource.error", "error")))
        sources.append(
            DatasourceSummary(
                uuid=uuid or "unknown",
                name=name or interface or "source",
                interface=interface or "?",
                running=_as_bool(_first_value(record, ("kismet.datasource.running", "running"))),
                hopping=_as_bool(_first_value(record, ("kismet.datasource.hopping", "hopping"))),
                channel=normalize_channel(
                    _first_value(record, ("kismet.datasource.channel", "channel")), frequency
                ),
                packets=max(
                    0,
                    _as_int(
                        _first_value(record, ("kismet.datasource.num_packets", "num_packets")), 0
                    )
                    or 0,
                ),
                error=error_text or ("error" if has_error else None),
            )
        )
    return tuple(sources)


def _ssid_from_record(record: Mapping[str, Any]) -> str:
    """Pull a beaconed SSID out of the dot11 sub-record, tolerating every nesting form."""
    candidates = (
        "dot11.device/dot11.device.last_beaconed_ssid_record/dot11.advertisedssid.ssid",
        "dot11.device.last_beaconed_ssid",
        "dot11.advertisedssid.ssid",
    )
    for candidate in candidates:
        if "/" in candidate:
            current: Any = record
            for part in candidate.split("/"):
                if not isinstance(current, Mapping):
                    current = None
                    break
                current = field_value(current, part)
            value = current
        else:
            value = field_value(record, candidate)
        text = sanitize_text(value, max_length=_MAX_NAME_CHARS)
        if text:
            return text
    return ""


def parse_devices(
    payload: Any,
    *,
    limit: int = 50,
    show_ssid: bool = True,
) -> tuple[DeviceSummary, ...]:
    """Parse a bounded device-view response into device summaries.

    Devices are sorted most-recently-active first and truncated to ``limit``, so the
    caller's memory footprint is bounded regardless of how busy the environment is.
    """
    records = _records(payload, container_keys=("devices", "kismet.device.list"))

    devices: list[DeviceSummary] = []
    for record in records:
        mac = sanitize_text(
            _first_value(record, ("kismet.device.base.macaddr", "macaddr")), max_length=24
        )
        key = sanitize_text(_first_value(record, ("kismet.device.base.key", "key")), max_length=48)
        if not mac and not key:
            continue

        frequency_khz = normalize_frequency_khz(
            _first_value(record, ("kismet.device.base.frequency", "frequency"))
        )
        channel = normalize_channel(
            _first_value(record, ("kismet.device.base.channel", "channel")), frequency_khz
        )

        name = ""
        if show_ssid:
            name = _ssid_from_record(record)
        if not name:
            name = sanitize_text(
                _first_value(
                    record,
                    ("kismet.device.base.commonname", "kismet.device.base.name", "commonname"),
                ),
                max_length=_MAX_NAME_CHARS,
            )

        signal = normalize_signal_dbm(_signal_from_record(record))

        devices.append(
            DeviceSummary(
                key=key or mac,
                mac=mac or key,
                display_name=name,
                device_type=normalize_device_type(
                    _first_value(record, ("kismet.device.base.type", "type"))
                ),
                channel=channel,
                frequency_khz=frequency_khz,
                band=normalize_band(channel, frequency_khz),
                signal_dbm=signal,
                first_seen=_as_float(
                    _first_value(record, ("kismet.device.base.first_time", "first_time")), 0.0
                )
                or 0.0,
                last_seen=_as_float(
                    _first_value(record, ("kismet.device.base.last_time", "last_time")), 0.0
                )
                or 0.0,
                packets=max(
                    0,
                    _as_int(_first_value(record, ("kismet.device.base.packets", "packets")), 0)
                    or 0,
                ),
                crypt=sanitize_text(
                    _first_value(record, ("kismet.device.base.crypt", "crypt")), max_length=16
                )
                or None,
            )
        )

    devices.sort(key=lambda d: d.last_seen, reverse=True)
    return tuple(devices[:limit])


def _signal_from_record(record: Mapping[str, Any]) -> Any:
    """Find a last-signal reading inside the nested common-signal sub-record."""
    container = _first_value(record, ("kismet.device.base.signal", "signal"))
    if isinstance(container, Mapping):
        value = _first_value(
            container, ("kismet.common.signal.last_signal", "last_signal", "signal_dbm")
        )
        if value is not None:
            return value
    return _first_value(record, ("kismet.common.signal.last_signal", "last_signal"))


def parse_alerts(payload: Any, *, limit: int = 25) -> tuple[AlertSummary, ...]:
    """Parse an alert response into newest-first, bounded alert summaries.

    Handles both the bare list form and the ``wrapped`` form that carries a server
    timestamp alongside the alert list.
    """
    if isinstance(payload, Mapping):
        wrapped = field_value(payload, "kismet.alert.list")
        if wrapped is not None:
            payload = wrapped
    records = _records(payload, container_keys=("kismet.alert.list", "alerts"))

    alerts: list[AlertSummary] = []
    for record in records:
        timestamp = _as_float(
            _first_value(
                record, ("kismet.alert.timestamp", "kismet.alert.timestamp.sec", "timestamp")
            ),
            0.0,
        )
        header = sanitize_text(
            _first_value(record, ("kismet.alert.header", "header", "kismet.alert.class")),
            max_length=24,
        )
        text = sanitize_text(
            _first_value(record, ("kismet.alert.text", "text")), max_length=_MAX_ALERT_TEXT_CHARS
        )
        if not header and not text:
            continue

        source = sanitize_text(
            _first_value(
                record, ("kismet.alert.source_mac", "kismet.alert.transmitter_mac", "source_mac")
            ),
            max_length=24,
        )
        alerts.append(
            AlertSummary(
                timestamp=timestamp or 0.0,
                severity=_normalize_severity(
                    _first_value(record, ("kismet.alert.severity", "severity"))
                ),
                header=header or "ALERT",
                text=text or header,
                source_mac=source or None,
            )
        )

    alerts.sort(key=lambda a: a.timestamp, reverse=True)
    return tuple(alerts[:limit])


def count_new_devices(devices: Iterable[DeviceSummary]) -> int:
    """Count devices flagged as first seen during this PiSight process."""
    return sum(1 for device in devices if device.is_new)
