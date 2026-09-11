"""Read-only Kismet REST provider.

Every request this module makes is a read. PiSight never mutates Kismet configuration,
never starts or stops a datasource, never locks a channel and never sends a control
command. The two ``POST`` calls below target Kismet's documented *query* endpoints, which
accept a JSON body solely to bound and simplify the response payload.

Authentication uses a ``readonly``-role API token supplied in the ``KISMET`` cookie, which
is the mechanism Kismet documents for API-token consumers. The token is never placed in a
URL, a log record, an exception message or a screenshot.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import httpx

from ..config import AppConfig
from ..health.host import HostHealthProvider
from ..models import (
    AlertSummary,
    CaptureStatus,
    ChannelSummary,
    DashboardSnapshot,
    DatasourceSummary,
)
from ..sanitize import sanitize_for_log
from .base import (
    NewDeviceTracker,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    finalize_snapshot,
)
from .parsers import (
    MalformedResponse,
    parse_alerts,
    parse_channels,
    parse_datasources,
    parse_devices,
    parse_packet_rate,
    parse_system_status,
    parse_timestamp,
)

__all__ = ["KISMET_TOKEN_COOKIE", "KismetDashboardProvider", "build_device_query"]

logger = logging.getLogger(__name__)

#: Cookie name Kismet accepts for API-token authentication.
KISMET_TOKEN_COOKIE = "KISMET"  # noqa: S105 - cookie name, not a secret

#: Field simplification sent with the device query. Requesting only what the four screens
#: render keeps responses small on a Pi and avoids pulling packet-level detail PiSight has
#: no business holding.
DEVICE_FIELDS: tuple[str, ...] = (
    "kismet.device.base.key",
    "kismet.device.base.macaddr",
    "kismet.device.base.name",
    "kismet.device.base.commonname",
    "kismet.device.base.type",
    "kismet.device.base.channel",
    "kismet.device.base.frequency",
    "kismet.device.base.first_time",
    "kismet.device.base.last_time",
    "kismet.device.base.packets",
    "kismet.device.base.crypt",
    "kismet.device.base.signal/kismet.common.signal.last_signal",
    "dot11.device/dot11.device.last_beaconed_ssid_record/dot11.advertisedssid.ssid",
)


def build_device_query(fields: tuple[str, ...] = DEVICE_FIELDS) -> dict[str, Any]:
    """Build the JSON body for a bounded, field-simplified device query."""
    return {"fields": list(fields)}


class KismetDashboardProvider:
    """Fetches dashboard state from a local Kismet instance over its REST API.

    A single :class:`httpx.Client` is held for the life of the provider so TCP connections
    and (on HTTPS deployments) TLS sessions are reused across polls.
    """

    name = "kismet"

    def __init__(
        self,
        config: AppConfig,
        *,
        api_token: str | None,
        host_health: HostHealthProvider,
        tracker: NewDeviceTracker | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._host_health = host_health
        self._tracker = tracker if tracker is not None else NewDeviceTracker()
        self._owns_client = client is None
        self._client = client if client is not None else self._build_client(config, api_token)

    @staticmethod
    def _build_client(config: AppConfig, api_token: str | None) -> httpx.Client:
        """Create the shared HTTP client, carrying the token only as a cookie."""
        timeout = httpx.Timeout(
            connect=config.http.connect_timeout_seconds,
            read=config.http.read_timeout_seconds,
            write=config.http.read_timeout_seconds,
            pool=config.http.connect_timeout_seconds,
        )
        cookies = httpx.Cookies()
        if api_token:
            cookies.set(KISMET_TOKEN_COOKIE, api_token)
        return httpx.Client(
            base_url=config.kismet.base_url.rstrip("/"),
            timeout=timeout,
            cookies=cookies,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "PiSight/0.1 (read-only)"},
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    # ---------------------------------------------------------------------------------
    # HTTP plumbing
    # ---------------------------------------------------------------------------------

    def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
        """Issue one request and decode JSON, translating every failure mode.

        Exception messages deliberately carry only the method, path and status. The token
        lives in a cookie the client manages, so it cannot leak through a formatted URL.
        """
        try:
            response = self._client.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(f"{method} {path}: timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"{method} {path}: {type(exc).__name__}") from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"{method} {path}: Kismet rejected the API token "
                f"(HTTP {response.status_code}); check that "
                "PISIGHT_KISMET_API_TOKEN holds a valid readonly token"
            )
        if response.status_code >= 400:
            raise ProviderUnavailable(f"{method} {path}: HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise MalformedResponse(f"{method} {path}: response was not valid JSON") from exc

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", path, json_body=body)

    def _optional(self, label: str, path: str, warnings: list[str]) -> Any | None:
        """Fetch a non-critical resource, degrading to ``None`` and a visible warning.

        Channels, datasources and alerts are supporting detail. If one of them fails while
        system status still responds, the dashboard stays up with that panel empty and a
        warning on the Alerts/System screen -- better than blanking the whole display.
        """
        try:
            return self._get(path)
        except ProviderAuthError:
            raise
        except (ProviderError, MalformedResponse) as exc:
            logger.warning("%s unavailable: %s", label, sanitize_for_log(str(exc)))
            warnings.append(f"{label} unavailable")
            return None

    # ---------------------------------------------------------------------------------
    # Snapshot assembly
    # ---------------------------------------------------------------------------------

    def _device_path(self) -> str:
        """Bounded device view path.

        Kismet reads a negative ``last-time`` value as "this many seconds ago", which is
        what bounds the response. Combined with field simplification and the client-side
        ``max_devices`` cap, PiSight never requests an unbounded all-devices dump.
        """
        endpoints = self._config.kismet.endpoints
        return endpoints.devices_last_time.format(
            view=self._config.kismet.device_view,
            timestamp=-abs(self._config.kismet.device_window_seconds),
        )

    def _alert_path(self) -> str:
        """Bounded alert path, using the same relative-timestamp convention."""
        return self._config.kismet.endpoints.alerts_last_time.format(
            timestamp=-abs(self._config.kismet.alert_window_seconds)
        )

    @staticmethod
    def _current_channel(sources: tuple[DatasourceSummary, ...]) -> tuple[str | None, bool]:
        """Derive the displayed channel/hopping state from the active datasources."""
        for source in sources:
            if source.running:
                return source.channel, source.hopping
        if sources:
            return sources[0].channel, sources[0].hopping
        return None, False

    def fetch(self) -> DashboardSnapshot:
        """Fetch one complete snapshot.

        System status and the device view are load-bearing: if either fails the whole
        attempt fails, the coordinator retains the previous snapshot and the display shows
        a stale/offline indicator.
        """
        config = self._config
        endpoints = config.kismet.endpoints
        warnings: list[str] = []

        status_payload = self._get(endpoints.system_status)
        status = parse_system_status(status_payload)

        device_payload = self._post(self._device_path(), build_device_query())
        devices = parse_devices(
            device_payload,
            limit=config.kismet.max_devices,
            show_ssid=config.privacy.show_ssid,
        )

        packet_payload = self._optional("packet stats", endpoints.packet_stats, warnings)
        packets_per_second = 0.0
        if packet_payload is not None:
            try:
                packets_per_second = parse_packet_rate(packet_payload)
            except MalformedResponse:
                warnings.append("packet stats unreadable")

        channels: tuple[ChannelSummary, ...] = ()
        channel_payload = self._optional("channels", endpoints.channels, warnings)
        if channel_payload is not None:
            try:
                channels = parse_channels(channel_payload, limit=config.kismet.max_channels)
            except MalformedResponse:
                warnings.append("channels unreadable")

        datasources: tuple[DatasourceSummary, ...] = ()
        datasource_payload = self._optional("datasources", endpoints.datasources, warnings)
        if datasource_payload is not None:
            try:
                datasources = parse_datasources(datasource_payload)
            except MalformedResponse:
                warnings.append("datasources unreadable")

        alerts: tuple[AlertSummary, ...] = ()
        alert_payload = self._optional("alerts", self._alert_path(), warnings)
        if alert_payload is not None:
            try:
                alerts = parse_alerts(alert_payload, limit=config.kismet.max_alerts)
            except MalformedResponse:
                warnings.append("alerts unreadable")

        channel, hopping = self._current_channel(datasources)
        total_packets = sum(source.packets for source in datasources)

        snapshot = DashboardSnapshot(
            capture=CaptureStatus(
                kismet_online=True,
                kismet_version=status["version"],
                packets_per_second=packets_per_second,
                total_packets=total_packets,
                total_devices=status["total_devices"],
                current_channel=channel,
                hopping=hopping,
            ),
            host=self._host_health.collect(),
            devices=devices,
            channels=channels,
            alerts=alerts,
            datasources=datasources,
            source_name=self.name,
            warnings=tuple(warnings),
        )
        return finalize_snapshot(snapshot, self._tracker)

    def check_login(self) -> bool:
        """Verify the API token without revealing it. Used by ``pisight doctor``."""
        try:
            self._request("GET", self._config.kismet.endpoints.login_check)
        except ProviderAuthError:
            return False
        return True

    def server_time(self) -> float:
        """Read Kismet's own clock; the cheapest available connectivity probe."""
        return parse_timestamp(self._get(self._config.kismet.endpoints.system_timestamp))

    def close(self) -> None:
        """Close the HTTP client if this provider created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> KismetDashboardProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def degrade(snapshot: DashboardSnapshot, error: str) -> DashboardSnapshot:
    """Return ``snapshot`` re-marked as offline, preserving its observations and age.

    ``monotonic_at`` is intentionally left untouched so the snapshot keeps ageing and the
    staleness indicator becomes true on schedule.
    """
    return replace(
        snapshot,
        capture=replace(
            snapshot.capture,
            kismet_online=False,
            packets_per_second=0.0,
            last_error=sanitize_for_log(error, max_length=80),
        ),
    )
