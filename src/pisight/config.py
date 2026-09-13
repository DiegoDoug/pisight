"""Configuration loading: packaged defaults, TOML file, then environment overrides.

Search order for the TOML file (first hit wins):

1. the path passed to ``--config``;
2. ``$PISIGHT_CONFIG``;
3. ``/etc/pisight/config.toml``;
4. packaged safe defaults (no file at all).

The Kismet API token is deliberately *not* part of :class:`AppConfig`. It is read from
``PISIGHT_KISMET_API_TOKEN`` at the moment of use by :func:`get_api_token`, so it cannot
be written to a config dump, a repr, a log line or a crash report.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "API_TOKEN_ENV",
    "AppConfig",
    "ConfigError",
    "DisplayConfig",
    "HttpConfig",
    "KismetConfig",
    "KismetEndpoints",
    "LoggingConfig",
    "Mode",
    "PollingConfig",
    "PrivacyConfig",
    "StorageConfig",
    "get_api_token",
    "load_config",
    "resolve_config_path",
]

Mode = Literal["mock", "live"]

#: Environment variable holding the read-only Kismet API token.
API_TOKEN_ENV = "PISIGHT_KISMET_API_TOKEN"  # noqa: S105 - variable name, not a secret

#: System-wide configuration path consulted when nothing more specific is given.
SYSTEM_CONFIG_PATH = Path("/etc/pisight/config.toml")

#: Environment variable naming an alternate configuration file.
CONFIG_PATH_ENV = "PISIGHT_CONFIG"


class ConfigError(ValueError):
    """Raised when configuration is present but unusable."""


@dataclass(frozen=True, slots=True)
class DisplayConfig:
    """Physical/logical display settings. The logical canvas is always 320x240."""

    width: int = 320
    height: int = 240
    fullscreen: bool = False
    fps: int = 30
    window_scale: int = 3
    sdl_video_driver: str | None = None


@dataclass(frozen=True, slots=True)
class KismetEndpoints:
    """Kismet REST paths, kept configurable so upstream changes need no code change.

    Every path here is read-only. PiSight never POSTs to a configuration or control
    endpoint; the two POST paths below are Kismet's documented *query* endpoints that
    accept a JSON body purely to bound and simplify the response.
    """

    system_status: str = "/system/status.json"
    system_timestamp: str = "/system/timestamp.json"
    packet_stats: str = "/packetchain/packet_stats.json"
    channels: str = "/channels/channels.json"
    datasources: str = "/datasource/all_sources.json"
    #: ``{view}`` and ``{timestamp}`` are substituted at request time.
    devices_last_time: str = "/devices/views/{view}/last-time/{timestamp}/devices.json"
    alerts_last_time: str = "/alerts/last-time/{timestamp}/alerts.json"
    login_check: str = "/session/check_login"


@dataclass(frozen=True, slots=True)
class KismetConfig:
    """Connection settings for the local Kismet REST API."""

    base_url: str = "http://127.0.0.1:2501"
    device_view: str = "all"
    #: Seconds of history requested from Kismet's ``last-time`` views.
    device_window_seconds: int = 300
    alert_window_seconds: int = 900
    #: Hard client-side cap; protects the UI and memory from a very busy environment.
    max_devices: int = 50
    max_alerts: int = 25
    max_channels: int = 64
    endpoints: KismetEndpoints = field(default_factory=KismetEndpoints)


@dataclass(frozen=True, slots=True)
class PollingConfig:
    """Background polling cadence and staleness thresholds."""

    interval_seconds: float = 2.0
    host_health_interval_seconds: float = 10.0
    stale_after_seconds: float = 10.0
    offline_after_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class HttpConfig:
    """HTTP timeouts and reconnect backoff."""

    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 4.0
    backoff_initial_seconds: float = 1.0
    backoff_factor: float = 2.0
    backoff_max_seconds: float = 30.0
    backoff_jitter: float = 0.25


@dataclass(frozen=True, slots=True)
class PrivacyConfig:
    """Privacy defaults. All of these start in the most conservative position."""

    show_full_mac: bool = False
    show_ssid: bool = True
    enable_gps: bool = False
    allow_uploads: bool = False


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Where Kismet writes its logs, used for the free-space display only."""

    kismet_log_path: str = "/var/log/kismet"
    low_space_warn_pct: float = 15.0


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Standard-library logging configuration."""

    level: str = "INFO"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete, immutable application configuration."""

    mode: Mode = "mock"
    display: DisplayConfig = field(default_factory=DisplayConfig)
    kismet: KismetConfig = field(default_factory=KismetConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    #: Path the configuration actually came from; ``None`` means packaged defaults.
    source_path: Path | None = None


# --------------------------------------------------------------------------------------
# Coercion helpers
# --------------------------------------------------------------------------------------


def _as_bool(value: object, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    raise ConfigError(f"{key}: expected a boolean, got {value!r}")


def _as_int(value: object, key: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{key}: expected an integer, got a boolean")
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key}: expected an integer, got {value!r}") from exc
    raise ConfigError(f"{key}: expected an integer, got {value!r}")


def _as_float(value: object, key: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{key}: expected a number, got a boolean")
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key}: expected a number, got {value!r}") from exc
    raise ConfigError(f"{key}: expected a number, got {value!r}")


def _as_str(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key}: expected a string, got {value!r}")
    return value


def _as_optional_str(value: object, key: str) -> str | None:
    if value is None:
        return None
    text = _as_str(value, key).strip()
    return text or None


def _as_mode(value: object, key: str) -> Mode:
    text = _as_str(value, key).strip().lower()
    if text not in ("mock", "live"):
        raise ConfigError(f"{key}: expected 'mock' or 'live', got {value!r}")
    return text  # type: ignore[return-value]


def _as_log_level(value: object, key: str) -> str:
    text = _as_str(value, key).strip().upper()
    valid = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
    if text not in valid:
        raise ConfigError(f"{key}: expected one of {sorted(valid)}, got {value!r}")
    return text


#: section -> field -> coercion function. Anything absent here is rejected, which turns a
#: typo in a config file into a clear error instead of a silently ignored setting.
_SCHEMA: dict[str, dict[str, Callable[[object, str], Any]]] = {
    "display": {
        "width": _as_int,
        "height": _as_int,
        "fullscreen": _as_bool,
        "fps": _as_int,
        "window_scale": _as_int,
        "sdl_video_driver": _as_optional_str,
    },
    "kismet": {
        "base_url": _as_str,
        "device_view": _as_str,
        "device_window_seconds": _as_int,
        "alert_window_seconds": _as_int,
        "max_devices": _as_int,
        "max_alerts": _as_int,
        "max_channels": _as_int,
    },
    "kismet.endpoints": {
        "system_status": _as_str,
        "system_timestamp": _as_str,
        "packet_stats": _as_str,
        "channels": _as_str,
        "datasources": _as_str,
        "devices_last_time": _as_str,
        "alerts_last_time": _as_str,
        "login_check": _as_str,
    },
    "polling": {
        "interval_seconds": _as_float,
        "host_health_interval_seconds": _as_float,
        "stale_after_seconds": _as_float,
        "offline_after_seconds": _as_float,
    },
    "http": {
        "connect_timeout_seconds": _as_float,
        "read_timeout_seconds": _as_float,
        "backoff_initial_seconds": _as_float,
        "backoff_factor": _as_float,
        "backoff_max_seconds": _as_float,
        "backoff_jitter": _as_float,
    },
    "privacy": {
        "show_full_mac": _as_bool,
        "show_ssid": _as_bool,
        "enable_gps": _as_bool,
        "allow_uploads": _as_bool,
    },
    "storage": {
        "kismet_log_path": _as_str,
        "low_space_warn_pct": _as_float,
    },
    "logging": {
        "level": _as_log_level,
    },
}

#: Environment variable -> (section, field). ``""`` as the section means top level.
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "PISIGHT_MODE": ("", "mode"),
    "PISIGHT_DISPLAY_WIDTH": ("display", "width"),
    "PISIGHT_DISPLAY_HEIGHT": ("display", "height"),
    "PISIGHT_FULLSCREEN": ("display", "fullscreen"),
    "PISIGHT_FPS": ("display", "fps"),
    "PISIGHT_WINDOW_SCALE": ("display", "window_scale"),
    "PISIGHT_SDL_VIDEODRIVER": ("display", "sdl_video_driver"),
    "PISIGHT_KISMET_BASE_URL": ("kismet", "base_url"),
    "PISIGHT_KISMET_DEVICE_VIEW": ("kismet", "device_view"),
    "PISIGHT_KISMET_MAX_DEVICES": ("kismet", "max_devices"),
    "PISIGHT_POLL_INTERVAL": ("polling", "interval_seconds"),
    "PISIGHT_STALE_AFTER": ("polling", "stale_after_seconds"),
    "PISIGHT_CONNECT_TIMEOUT": ("http", "connect_timeout_seconds"),
    "PISIGHT_READ_TIMEOUT": ("http", "read_timeout_seconds"),
    "PISIGHT_SHOW_FULL_MAC": ("privacy", "show_full_mac"),
    "PISIGHT_STORAGE_PATH": ("storage", "kismet_log_path"),
    "PISIGHT_LOG_LEVEL": ("logging", "level"),
}


def resolve_config_path(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    system_path: Path = SYSTEM_CONFIG_PATH,
) -> Path | None:
    """Return the configuration file to load, or ``None`` for packaged defaults.

    An explicit ``--config`` path that does not exist is an error: silently falling back
    would hide an operator typo behind apparently working defaults.
    """
    environ = os.environ if env is None else env

    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"configuration file not found: {path}")
        return path

    from_env = environ.get(CONFIG_PATH_ENV, "").strip()
    if from_env:
        path = Path(from_env)
        if not path.is_file():
            raise ConfigError(f"{CONFIG_PATH_ENV} points at a missing file: {path}")
        return path

    if system_path.is_file():
        return system_path

    return None


def _apply_section(
    section_name: str,
    data: Mapping[str, Any],
    current: Any,
) -> Any:
    """Validate and apply one TOML section onto a frozen dataclass instance."""
    schema = _SCHEMA[section_name]
    updates: dict[str, Any] = {}
    for key, raw in data.items():
        if key not in schema:
            raise ConfigError(f"unknown configuration key: [{section_name}] {key}")
        updates[key] = schema[key](raw, f"[{section_name}] {key}")
    return replace(current, **updates)


def _apply_toml(config: AppConfig, data: Mapping[str, Any]) -> AppConfig:
    result = config
    for section, value in data.items():
        if section == "mode":
            result = replace(result, mode=_as_mode(value, "mode"))
            continue
        if section not in _SCHEMA and section != "kismet":
            raise ConfigError(f"unknown configuration section: [{section}]")
        if not isinstance(value, Mapping):
            raise ConfigError(f"[{section}]: expected a table")

        if section == "kismet":
            endpoint_data = value.get("endpoints", {})
            if not isinstance(endpoint_data, Mapping):
                raise ConfigError("[kismet.endpoints]: expected a table")
            plain = {k: v for k, v in value.items() if k != "endpoints"}
            kismet = _apply_section("kismet", plain, result.kismet)
            if endpoint_data:
                endpoints = _apply_section(
                    "kismet.endpoints", endpoint_data, result.kismet.endpoints
                )
                kismet = replace(kismet, endpoints=endpoints)
            result = replace(result, kismet=kismet)
            continue

        current = getattr(result, section)
        result = replace(result, **{section: _apply_section(section, value, current)})
    return result


def _apply_env(config: AppConfig, env: Mapping[str, str]) -> AppConfig:
    result = config
    for var, (section, key) in _ENV_OVERRIDES.items():
        raw = env.get(var)
        if raw is None or raw.strip() == "":
            continue
        if section == "":
            result = replace(result, mode=_as_mode(raw, var))
            continue
        coerce = _SCHEMA[section][key]
        current = getattr(result, section)
        result = replace(result, **{section: replace(current, **{key: coerce(raw, var)})})
    return result


def _validate(config: AppConfig) -> None:
    display = config.display
    if display.width <= 0 or display.height <= 0:
        raise ConfigError("[display] width and height must be positive")
    if not 1 <= display.fps <= 120:
        raise ConfigError("[display] fps must be between 1 and 120")
    if not 1 <= display.window_scale <= 8:
        raise ConfigError("[display] window_scale must be between 1 and 8")

    kismet = config.kismet
    if not kismet.base_url.startswith(("http://", "https://")):
        raise ConfigError("[kismet] base_url must start with http:// or https://")
    if "@" in kismet.base_url.split("//", 1)[1].split("/", 1)[0]:
        raise ConfigError("[kismet] base_url must not embed credentials")
    for name, value in (
        ("max_devices", kismet.max_devices),
        ("max_alerts", kismet.max_alerts),
        ("max_channels", kismet.max_channels),
    ):
        if value <= 0:
            raise ConfigError(f"[kismet] {name} must be positive")
    if kismet.device_window_seconds <= 0 or kismet.alert_window_seconds <= 0:
        raise ConfigError("[kismet] history windows must be positive")

    polling = config.polling
    if polling.interval_seconds <= 0:
        raise ConfigError("[polling] interval_seconds must be positive")
    if polling.stale_after_seconds <= 0:
        raise ConfigError("[polling] stale_after_seconds must be positive")
    if polling.offline_after_seconds < polling.stale_after_seconds:
        raise ConfigError("[polling] offline_after_seconds must be >= stale_after_seconds")

    http = config.http
    if http.connect_timeout_seconds <= 0 or http.read_timeout_seconds <= 0:
        raise ConfigError("[http] timeouts must be positive")
    if http.backoff_initial_seconds <= 0:
        raise ConfigError("[http] backoff_initial_seconds must be positive")
    if http.backoff_factor < 1.0:
        raise ConfigError("[http] backoff_factor must be >= 1.0")
    if http.backoff_max_seconds < http.backoff_initial_seconds:
        raise ConfigError("[http] backoff_max_seconds must be >= backoff_initial_seconds")
    if not 0.0 <= http.backoff_jitter <= 1.0:
        raise ConfigError("[http] backoff_jitter must be between 0.0 and 1.0")

    if config.privacy.allow_uploads:
        raise ConfigError(
            "[privacy] allow_uploads must stay false: PiSight v1 never uploads observations"
        )
    if config.privacy.enable_gps:
        raise ConfigError("[privacy] enable_gps must stay false: PiSight v1 records no location")


def load_config(
    explicit_path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    system_path: Path = SYSTEM_CONFIG_PATH,
    mode_override: Mode | None = None,
) -> AppConfig:
    """Build an :class:`AppConfig` from defaults, an optional TOML file and the environment.

    Precedence, lowest to highest: packaged defaults, TOML file, environment variables,
    then ``mode_override`` (the ``--mode`` command-line flag).
    """
    environ = os.environ if env is None else env
    config = AppConfig()

    path = resolve_config_path(explicit_path, env=environ, system_path=system_path)
    if path is not None:
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"{path}: cannot read configuration: {exc}") from exc
        config = _apply_toml(config, data)
        config = replace(config, source_path=path)

    config = _apply_env(config, environ)

    if mode_override is not None:
        config = replace(config, mode=mode_override)

    _validate(config)
    return config


def get_api_token(env: Mapping[str, str] | None = None) -> str | None:
    """Return the read-only Kismet API token, or ``None`` when it is not set.

    The value is never cached in a dataclass, logged, or included in a request URL.
    """
    environ = os.environ if env is None else env
    token = environ.get(API_TOKEN_ENV, "").strip()
    return token or None
