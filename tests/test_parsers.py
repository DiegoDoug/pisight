"""Parser behaviour against realistic namespaced Kismet fixtures and hostile input."""

from __future__ import annotations

import pytest

from helpers import load_fixture
from pisight.models import Band, DeviceType, Severity
from pisight.providers.parsers import (
    MalformedResponse,
    channel_for_frequency_khz,
    field_value,
    normalize_band,
    normalize_channel,
    normalize_device_type,
    normalize_frequency_khz,
    normalize_signal_dbm,
    parse_alerts,
    parse_channels,
    parse_datasources,
    parse_devices,
    parse_packet_rate,
    parse_system_status,
    parse_timestamp,
    rrd_last_value,
)

# --- Field access ----------------------------------------------------------------------


def test_field_value_reads_flat_dotted_keys() -> None:
    record = {"kismet.device.base.macaddr": "02:00:5E:00:00:01"}
    assert field_value(record, "kismet.device.base.macaddr") == "02:00:5E:00:00:01"


def test_field_value_reads_nested_objects() -> None:
    record = {"kismet": {"device": {"base": {"macaddr": "02:00:5E:00:00:02"}}}}
    assert field_value(record, "kismet.device.base.macaddr") == "02:00:5E:00:00:02"


def test_field_value_reads_partially_nested_objects() -> None:
    record = {"kismet.device.base": {"macaddr": "02:00:5E:00:00:03"}}
    assert field_value(record, "kismet.device.base.macaddr") == "02:00:5E:00:00:03"


def test_field_value_returns_default_for_absent_and_non_mapping() -> None:
    assert field_value({}, "a.b", "fallback") == "fallback"
    assert field_value("not a mapping", "a.b", "fallback") == "fallback"
    assert field_value({"a": "scalar"}, "a.b", "fallback") == "fallback"


def test_field_value_distinguishes_explicit_none_from_absent() -> None:
    assert field_value({"a": {"b": None}}, "a.b", "fallback") is None


# --- Normalization ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2412000, 2412000),  # already kHz
        (2412, 2412000),  # MHz
        (2.412, 2412000),  # GHz
        (5745000, 5745000),
        ("5180000", 5180000),
        (0, None),
        (-1, None),
        (None, None),
        ("banana", None),
        (float("inf"), None),
        (float("nan"), None),
        (True, None),
    ],
)
def test_normalize_frequency_khz(raw: object, expected: int | None) -> None:
    assert normalize_frequency_khz(raw) == expected


@pytest.mark.parametrize(
    ("khz", "expected"),
    [
        (2412000, "1"),
        (2437000, "6"),
        (2462000, "11"),
        (2484000, "14"),
        (5180000, "36"),
        (5745000, "149"),
        (5955000, "1"),  # 6 GHz
        (1000000, None),  # not a Wi-Fi band
        (None, None),
    ],
)
def test_channel_for_frequency(khz: int | None, expected: str | None) -> None:
    assert channel_for_frequency_khz(khz) == expected


def test_normalize_channel_prefers_the_reported_label() -> None:
    assert normalize_channel("36HT80", 5180000) == "36HT80"


def test_normalize_channel_falls_back_to_frequency() -> None:
    for empty in ("", None, "0", "-1", "unknown"):
        assert normalize_channel(empty, 2437000) == "6"


def test_normalize_channel_returns_none_without_either() -> None:
    assert normalize_channel("", None) is None


@pytest.mark.parametrize(
    ("channel", "khz", "expected"),
    [
        ("6", 2437000, Band.BAND_2GHZ),
        ("36", 5180000, Band.BAND_5GHZ),
        ("1", 5955000, Band.BAND_6GHZ),
        ("11", None, Band.BAND_2GHZ),
        ("149", None, Band.BAND_5GHZ),
        ("36HT80", None, Band.BAND_5GHZ),
        (None, None, Band.UNKNOWN),
        ("weird", None, Band.UNKNOWN),
        ("6", 1000000, Band.UNKNOWN),
    ],
)
def test_normalize_band(channel: str | None, khz: int | None, expected: Band) -> None:
    assert normalize_band(channel, khz) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-52, -52),
        (-52.4, -52),
        ("-71", -71),
        (0, None),  # Kismet's "never measured" value
        (5, None),  # implausible positive
        (-500, None),  # implausible negative
        (None, None),
        ("", None),
    ],
)
def test_normalize_signal_dbm(raw: object, expected: int | None) -> None:
    assert normalize_signal_dbm(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Wi-Fi AP", DeviceType.ACCESS_POINT),
        ("Wi-Fi Access Point", DeviceType.ACCESS_POINT),
        ("Wi-Fi Client", DeviceType.CLIENT),
        ("Wi-Fi Bridged", DeviceType.BRIDGED),
        ("Wi-Fi Ad-Hoc", DeviceType.ADHOC),
        ("Wi-Fi WDS", DeviceType.WDS),
        ("Wi-Fi Sasquatch", DeviceType.UNKNOWN),
        ("", DeviceType.UNKNOWN),
        (None, DeviceType.UNKNOWN),
        (12345, DeviceType.UNKNOWN),
    ],
)
def test_normalize_device_type(raw: object, expected: DeviceType) -> None:
    assert normalize_device_type(raw) is expected


def test_rrd_last_value_handles_every_documented_shape() -> None:
    assert rrd_last_value(42) == 42.0
    assert rrd_last_value({"kismet.common.rrd.last_value": 17}) == 17.0
    assert rrd_last_value({"kismet.common.rrd.minute_vec": [1, 2, 9]}) == 9.0
    assert rrd_last_value({}) is None
    assert rrd_last_value("nonsense") is None
    assert rrd_last_value(None) is None


# --- Endpoint parsers ------------------------------------------------------------------


def test_parse_timestamp() -> None:
    assert parse_timestamp(load_fixture("system_timestamp.json")) == pytest.approx(1717243800.5)


def test_parse_timestamp_rejects_malformed() -> None:
    with pytest.raises(MalformedResponse):
        parse_timestamp(["not", "an", "object"])
    with pytest.raises(MalformedResponse):
        parse_timestamp({"unrelated": 1})


def test_parse_system_status() -> None:
    status = parse_system_status(load_fixture("system_status.json"))
    assert status["total_devices"] == 412
    assert status["cpu_temp_c"] == pytest.approx(51.25)
    assert status["version"] == "2024-07-R1"


def test_parse_system_status_tolerates_missing_optional_fields() -> None:
    status = parse_system_status({})
    assert status["total_devices"] == 0
    assert status["cpu_temp_c"] is None
    assert status["version"] is None


def test_parse_system_status_rejects_a_list() -> None:
    with pytest.raises(MalformedResponse):
        parse_system_status([1, 2, 3])


def test_parse_packet_rate() -> None:
    assert parse_packet_rate(load_fixture("packet_stats.json")) == pytest.approx(143.0)


def test_parse_packet_rate_defaults_to_zero_when_absent() -> None:
    assert parse_packet_rate({"unrelated": True}) == 0.0


def test_parse_channels() -> None:
    channels = parse_channels(load_fixture("channels.json"))
    by_channel = {channel.channel: channel for channel in channels}

    assert by_channel["6"].device_count == 12
    assert by_channel["6"].packet_count == 980
    assert by_channel["6"].band is Band.BAND_2GHZ
    assert by_channel["36"].band is Band.BAND_5GHZ
    assert by_channel["149"].device_count == 0
    # Records that are not objects, or whose frequency is unusable, are skipped silently.
    assert len(channels) == 4
    # Busiest first.
    assert channels[0].channel == "6"


def test_parse_channels_respects_the_limit() -> None:
    assert len(parse_channels(load_fixture("channels.json"), limit=2)) == 2


def test_parse_channels_rejects_malformed() -> None:
    with pytest.raises(MalformedResponse):
        parse_channels(["not", "an", "object"])
    with pytest.raises(MalformedResponse):
        parse_channels({"kismet.channeltracker.frequency_map": "not a map"})


def test_parse_datasources() -> None:
    sources = parse_datasources(load_fixture("datasources.json"))
    assert len(sources) == 2

    active, failed = sources
    assert active.interface == "wlan1"
    assert active.running is True
    assert active.hopping is True
    assert active.channel == "6"
    assert active.packets == 128394
    assert active.error is None
    assert active.state_label == "HOP"

    assert failed.running is False
    assert failed.error == "adapter removed"
    assert failed.state_label == "ERROR"


def test_parse_datasources_tolerates_empty_list() -> None:
    assert parse_datasources([]) == ()


def test_parse_devices_from_a_realistic_view() -> None:
    devices = parse_devices(load_fixture("devices_view.json"))
    by_key = {device.key: device for device in devices}

    ap = by_key["SYNTH_0001"]
    assert ap.device_type is DeviceType.ACCESS_POINT
    assert ap.display_name == "synthetic-lab-2g"
    assert ap.channel == "6"
    assert ap.band is Band.BAND_2GHZ
    assert ap.signal_dbm == -52
    assert ap.crypt == "WPA2"

    client = by_key["SYNTH_0002"]
    assert client.device_type is DeviceType.CLIENT
    assert client.band is Band.BAND_5GHZ
    assert client.signal_dbm == -71

    bridged = by_key["SYNTH_0003"]
    assert bridged.device_type is DeviceType.BRIDGED
    # A reported signal of 0 means "never measured", not "extremely strong".
    assert bridged.signal_dbm is None
    # No channel was reported, so it is derived from the frequency.
    assert bridged.channel == "1"

    unknown = by_key["SYNTH_0004"]
    assert unknown.device_type is DeviceType.UNKNOWN
    # Control characters in an SSID are neutralised before they reach the UI.
    assert "\n" not in unknown.display_name
    assert "\t" not in unknown.display_name


def test_parse_devices_sorts_most_recent_first_and_bounds_the_result() -> None:
    devices = parse_devices(load_fixture("devices_view.json"))
    assert [d.last_seen for d in devices] == sorted((d.last_seen for d in devices), reverse=True)
    assert len(parse_devices(load_fixture("devices_view.json"), limit=2)) == 2


def test_parse_devices_can_suppress_ssids() -> None:
    devices = parse_devices(load_fixture("devices_view.json"), show_ssid=False)
    ap = next(d for d in devices if d.key == "SYNTH_0001")
    assert ap.display_name != "synthetic-lab-2g"


def test_parse_devices_tolerates_partial_records() -> None:
    devices = parse_devices(load_fixture("devices_partial.json"))
    # The record with no identity at all is dropped; the other two survive.
    assert len(devices) == 2
    for device in devices:
        assert device.packets >= 0
        assert device.signal_dbm is None


def test_parse_devices_rejects_a_malformed_response() -> None:
    with pytest.raises(MalformedResponse):
        parse_devices("this is not a device list")


def test_parse_devices_accepts_an_object_wrapped_list() -> None:
    payload = {"devices": load_fixture("devices_view.json")}
    assert len(parse_devices(payload)) == 4


def test_parse_alerts_maps_severity_and_bounds_the_result() -> None:
    alerts = parse_alerts(load_fixture("alerts.json"))
    by_header = {alert.header: alert for alert in alerts}

    assert by_header["APSPOOF"].severity is Severity.CRITICAL
    assert by_header["PROBENOJOIN"].severity is Severity.LOW
    assert by_header["DISASSOCTRAFFIC"].severity is Severity.MEDIUM
    # A severity above the documented scale clamps to CRITICAL rather than crashing.
    assert by_header["UNKNOWNSCALE"].severity is Severity.CRITICAL
    # The record with neither header nor text is dropped.
    assert len(alerts) == 4
    # Newest first.
    assert alerts[0].header == "PROBENOJOIN"


def test_parse_alerts_handles_the_wrapped_form() -> None:
    alerts = parse_alerts(load_fixture("alerts_wrapped.json"))
    assert len(alerts) == 1
    assert alerts[0].severity is Severity.HIGH


def test_parse_alerts_respects_the_limit() -> None:
    assert len(parse_alerts(load_fixture("alerts.json"), limit=2)) == 2


def test_parse_alerts_rejects_malformed() -> None:
    with pytest.raises(MalformedResponse):
        parse_alerts(42)


def test_severity_ranking_is_ordered() -> None:
    ranks = [severity.rank for severity in Severity]
    assert ranks == sorted(ranks)
    assert Severity.CRITICAL.is_elevated
    assert Severity.HIGH.is_elevated
    assert not Severity.MEDIUM.is_elevated


def test_no_parser_raises_key_error_on_empty_input() -> None:
    """A blanket guarantee: empty containers degrade, they never raise KeyError."""
    assert parse_devices([]) == ()
    assert parse_alerts([]) == ()
    assert parse_datasources([]) == ()
    assert parse_channels({"kismet.channeltracker.frequency_map": {}}) == ()
    assert parse_packet_rate({}) == 0.0
    assert parse_system_status({})["total_devices"] == 0
