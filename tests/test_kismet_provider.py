"""Live Kismet provider: transport failures, auth, bounded queries and token hygiene.

Every test here runs against an `httpx.MockTransport`; no network is touched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from helpers import load_fixture
from pisight.config import AppConfig, KismetConfig
from pisight.health.host import HostHealthProvider
from pisight.providers.base import ProviderAuthError, ProviderUnavailable
from pisight.providers.kismet import (
    DEVICE_FIELDS,
    KISMET_TOKEN_COOKIE,
    KismetDashboardProvider,
    build_device_query,
    degrade,
)
from pisight.providers.parsers import MalformedResponse

TOKEN = "synthetic-readonly-token-value"

#: Default routing table: path fragment -> fixture filename.
_ROUTES: dict[str, str] = {
    "/system/status.json": "system_status.json",
    "/system/timestamp.json": "system_timestamp.json",
    "/packetchain/packet_stats.json": "packet_stats.json",
    "/channels/channels.json": "channels.json",
    "/datasource/all_sources.json": "datasources.json",
    "/devices/views/": "devices_view.json",
    "/alerts/": "alerts.json",
}


def make_provider(
    handler: Any,
    *,
    config: AppConfig | None = None,
    token: str | None = TOKEN,
) -> KismetDashboardProvider:
    """Build a provider wired to a mock transport."""
    cfg = config or AppConfig()
    cookies = httpx.Cookies()
    if token:
        cookies.set(KISMET_TOKEN_COOKIE, token)
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=cfg.kismet.base_url,
        cookies=cookies,
    )
    return KismetDashboardProvider(
        cfg,
        api_token=token,
        host_health=HostHealthProvider(".", cache_seconds=0.0),
        client=client,
    )


def happy_handler(request: httpx.Request) -> httpx.Response:
    """Serve the synthetic fixture matching the requested path."""
    for fragment, fixture in _ROUTES.items():
        if fragment in request.url.path:
            return httpx.Response(200, json=load_fixture(fixture))
    return httpx.Response(404, json={"error": "unrouted path"})


# --- Happy path ------------------------------------------------------------------------


def test_fetch_builds_a_complete_snapshot() -> None:
    provider = make_provider(happy_handler)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    assert snapshot.capture.kismet_online is True
    assert snapshot.capture.total_devices == 412
    assert snapshot.capture.packets_per_second == pytest.approx(143.0)
    assert snapshot.capture.current_channel == "6"
    assert snapshot.capture.hopping is True
    assert len(snapshot.devices) == 4
    assert len(snapshot.channels) == 4
    assert len(snapshot.datasources) == 2
    assert len(snapshot.alerts) == 4
    assert snapshot.source_name == "kismet"
    assert snapshot.warnings == ()


def test_first_fetch_marks_every_device_new_and_the_second_does_not() -> None:
    provider = make_provider(happy_handler)
    try:
        first = provider.fetch()
        second = provider.fetch()
    finally:
        provider.close()

    assert first.new_device_count == 4
    assert second.new_device_count == 0
    assert all(not device.is_new for device in second.devices)


# --- Request shape ---------------------------------------------------------------------


def test_device_query_is_bounded_and_field_simplified() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/devices/views/" in request.url.path:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            seen["method"] = request.method
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        provider.fetch()
    finally:
        provider.close()

    # A negative last-time value is Kismet's "this many seconds ago", which is the bound.
    assert "last-time/-300/" in seen["path"]
    assert seen["method"] == "POST"
    assert seen["body"]["fields"] == list(DEVICE_FIELDS)
    # Never the unbounded all-devices dump.
    assert "/devices/all_devices" not in seen["path"]


def test_alert_query_is_bounded() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/alerts/" in request.url.path:
            seen["path"] = request.url.path
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        provider.fetch()
    finally:
        provider.close()

    assert "last-time/-900/" in seen["path"]
    assert "all_alerts" not in seen["path"]


def test_only_read_methods_are_used() -> None:
    """The provider must never issue a write to Kismet."""
    methods: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        provider.fetch()
    finally:
        provider.close()

    assert methods, "no requests were issued"
    for method, path in methods:
        assert method in ("GET", "POST")
        if method == "POST":
            # The only POSTs are Kismet's documented query endpoints.
            assert "/devices/views/" in path
    # No configuration, control or lifecycle endpoint is ever touched.
    forbidden = ("/datasource/add", "set_channel", "/config", "open_source", "close_source")
    for _, path in methods:
        assert not any(token in path for token in forbidden)


def test_token_is_sent_as_a_cookie_and_never_in_the_url() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        provider.fetch()
    finally:
        provider.close()

    for request in observed:
        assert TOKEN not in str(request.url)
        assert TOKEN not in request.url.query.decode()
        assert request.headers.get("cookie", "").startswith(f"{KISMET_TOKEN_COOKIE}=")


def test_build_device_query_is_a_plain_field_list() -> None:
    assert build_device_query(("a", "b")) == {"fields": ["a", "b"]}


# --- Failure handling ------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_raise_a_dedicated_error_without_the_token(status: int) -> None:
    provider = make_provider(lambda request: httpx.Response(status, json={}))
    try:
        with pytest.raises(ProviderAuthError) as caught:
            provider.fetch()
    finally:
        provider.close()

    message = str(caught.value)
    assert TOKEN not in message
    assert "PISIGHT_KISMET_API_TOKEN" in message


@pytest.mark.parametrize("status", [404, 500, 503])
def test_server_errors_are_reported_as_unavailable(status: int) -> None:
    provider = make_provider(lambda request: httpx.Response(status, json={}))
    try:
        with pytest.raises(ProviderUnavailable, match=str(status)):
            provider.fetch()
    finally:
        provider.close()


def test_connection_error_is_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = make_provider(handler)
    try:
        with pytest.raises(ProviderUnavailable, match="ConnectError"):
            provider.fetch()
    finally:
        provider.close()


def test_timeout_is_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    provider = make_provider(handler)
    try:
        with pytest.raises(ProviderUnavailable, match="timed out"):
            provider.fetch()
    finally:
        provider.close()


def test_invalid_json_on_a_critical_endpoint_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/system/status.json" in request.url.path:
            return httpx.Response(200, content=b"<html>not json</html>")
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        with pytest.raises(MalformedResponse):
            provider.fetch()
    finally:
        provider.close()


def test_malformed_device_response_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/devices/views/" in request.url.path:
            return httpx.Response(200, json=load_fixture("devices_malformed.json"))
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        with pytest.raises(MalformedResponse):
            provider.fetch()
    finally:
        provider.close()


def test_optional_endpoint_failure_degrades_with_a_visible_warning() -> None:
    """Channels going away must not blank the whole dashboard."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/channels/" in request.url.path:
            return httpx.Response(500, json={})
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    assert snapshot.capture.kismet_online is True
    assert snapshot.channels == ()
    assert len(snapshot.devices) == 4  # everything else survived
    assert any("channels" in warning for warning in snapshot.warnings)


def test_auth_failure_on_an_optional_endpoint_still_propagates() -> None:
    """A revoked token must surface, not hide behind a degraded panel."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/channels/" in request.url.path:
            return httpx.Response(403, json={})
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        with pytest.raises(ProviderAuthError):
            provider.fetch()
    finally:
        provider.close()


def test_malformed_optional_endpoint_degrades() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/channels/" in request.url.path:
            return httpx.Response(200, json=["not", "a", "channel", "map"])
        return happy_handler(request)

    provider = make_provider(handler)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    assert snapshot.channels == ()
    assert any("channels" in warning for warning in snapshot.warnings)


# --- Auxiliary operations --------------------------------------------------------------


def test_check_login_reports_acceptance_and_rejection() -> None:
    ok = make_provider(lambda request: httpx.Response(200, json={}))
    try:
        assert ok.check_login() is True
    finally:
        ok.close()

    rejected = make_provider(lambda request: httpx.Response(401, json={}))
    try:
        assert rejected.check_login() is False
    finally:
        rejected.close()


def test_server_time_reads_the_timestamp_endpoint() -> None:
    provider = make_provider(happy_handler)
    try:
        assert provider.server_time() == pytest.approx(1717243800.5)
    finally:
        provider.close()


def test_endpoints_are_configurable_without_code_changes() -> None:
    from dataclasses import replace

    config = AppConfig()
    config = replace(
        config,
        kismet=replace(
            config.kismet,
            endpoints=replace(config.kismet.endpoints, system_timestamp="/custom/timestamp.json"),
        ),
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=load_fixture("system_timestamp.json"))

    provider = make_provider(handler, config=config)
    try:
        provider.server_time()
    finally:
        provider.close()

    assert seen == ["/custom/timestamp.json"]


def test_device_window_is_configurable() -> None:
    from dataclasses import replace

    config = AppConfig()
    config = replace(config, kismet=replace(config.kismet, device_window_seconds=60))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return happy_handler(request)

    provider = make_provider(handler, config=config)
    try:
        provider.fetch()
    finally:
        provider.close()

    assert any("last-time/-60/" in path for path in seen)


def test_max_devices_bounds_the_snapshot() -> None:
    from dataclasses import replace

    config = AppConfig()
    config = replace(config, kismet=replace(config.kismet, max_devices=2))
    provider = make_provider(happy_handler, config=config)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    assert len(snapshot.devices) == 2


def test_degrade_marks_offline_but_keeps_observations_and_age() -> None:
    provider = make_provider(happy_handler)
    try:
        snapshot = provider.fetch()
    finally:
        provider.close()

    degraded = degrade(snapshot, "connection refused")
    assert degraded.capture.kismet_online is False
    assert degraded.capture.packets_per_second == 0.0
    assert degraded.capture.last_error == "connection refused"
    # Observations and the snapshot's age basis are preserved.
    assert degraded.devices == snapshot.devices
    assert degraded.monotonic_at == snapshot.monotonic_at


def test_provider_without_a_token_sends_no_cookie() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return happy_handler(request)

    provider = make_provider(handler, token=None)
    try:
        provider.fetch()
    finally:
        provider.close()

    assert KISMET_TOKEN_COOKIE not in observed[0].headers.get("cookie", "")


def test_base_url_and_timeouts_are_taken_from_configuration() -> None:
    """The real client (not the mock transport) must honour configured timeouts."""
    config = AppConfig()
    provider = KismetDashboardProvider(
        config,
        api_token=TOKEN,
        host_health=HostHealthProvider(".", cache_seconds=0.0),
    )
    try:
        client = provider._client
        assert str(client.base_url) == config.kismet.base_url
        assert client.timeout.connect == config.http.connect_timeout_seconds
        assert client.timeout.read == config.http.read_timeout_seconds
        assert client.cookies.get(KISMET_TOKEN_COOKIE) == TOKEN
    finally:
        provider.close()


def test_kismet_config_defaults_are_bounded() -> None:
    kismet = KismetConfig()
    assert kismet.max_devices > 0
    assert kismet.max_alerts > 0
    assert kismet.device_window_seconds > 0
