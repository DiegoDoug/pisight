"""Configuration precedence, validation and token handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from pisight.config import (
    API_TOKEN_ENV,
    AppConfig,
    ConfigError,
    get_api_token,
    load_config,
    resolve_config_path,
)


def write_config(tmp_path: Path, body: str, name: str = "config.toml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_defaults_match_the_product_specification() -> None:
    config = AppConfig()
    assert config.mode == "mock"
    assert (config.display.width, config.display.height) == (320, 240)
    assert config.display.fps == 30
    assert config.display.fullscreen is False
    assert config.kismet.base_url == "http://127.0.0.1:2501"
    assert config.privacy.show_full_mac is False
    assert config.privacy.enable_gps is False
    assert config.privacy.allow_uploads is False


def test_explicit_path_wins_over_env_and_system(tmp_path: Path) -> None:
    explicit = write_config(tmp_path, 'mode = "live"\n', "explicit.toml")
    from_env = write_config(tmp_path, 'mode = "mock"\n', "env.toml")

    config = load_config(explicit, env={"PISIGHT_CONFIG": str(from_env)})
    assert config.mode == "live"
    assert config.source_path == explicit


def test_env_path_used_when_no_explicit_path(tmp_path: Path) -> None:
    from_env = write_config(tmp_path, "[display]\nfps = 15\n", "env.toml")
    config = load_config(None, env={"PISIGHT_CONFIG": str(from_env)})
    assert config.display.fps == 15
    assert config.source_path == from_env


def test_system_path_used_when_nothing_else_is_given(tmp_path: Path) -> None:
    system = write_config(tmp_path, '[logging]\nlevel = "DEBUG"\n', "system.toml")
    config = load_config(None, env={}, system_path=system)
    assert config.logging.level == "DEBUG"


def test_packaged_defaults_when_no_file_exists(tmp_path: Path) -> None:
    config = load_config(None, env={}, system_path=tmp_path / "absent.toml")
    assert config.source_path is None
    assert config.mode == "mock"


def test_environment_overrides_the_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[display]\nfps = 10\n[polling]\ninterval_seconds = 9.0\n")
    config = load_config(path, env={"PISIGHT_FPS": "24", "PISIGHT_POLL_INTERVAL": "3.5"})
    assert config.display.fps == 24
    assert config.polling.interval_seconds == 3.5


def test_mode_override_beats_environment(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'mode = "mock"\n')
    config = load_config(path, env={"PISIGHT_MODE": "mock"}, mode_override="live")
    assert config.mode == "live"


def test_empty_environment_value_does_not_override() -> None:
    config = load_config(None, env={"PISIGHT_FPS": "   "}, system_path=Path("/nonexistent"))
    assert config.display.fps == 30


def test_missing_explicit_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml", env={})


def test_missing_env_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing file"):
        resolve_config_path(None, env={"PISIGHT_CONFIG": str(tmp_path / "missing.toml")})


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[display]\nwidth = 320\nnonsense = 1\n")
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(path, env={})


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[nonsense]\nvalue = 1\n")
    with pytest.raises(ConfigError, match="unknown configuration section"):
        load_config(path, env={})


def test_invalid_toml_is_reported_clearly(tmp_path: Path) -> None:
    path = write_config(tmp_path, "this is not = = toml\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path, env={})


def test_endpoints_are_configurable(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "[kismet.endpoints]\nsystem_status = '/v2/system/status.json'\n",
    )
    config = load_config(path, env={})
    assert config.kismet.endpoints.system_status == "/v2/system/status.json"
    # Untouched endpoints keep their defaults.
    assert config.kismet.endpoints.channels == "/channels/channels.json"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[display]\nfps = 0\n", "fps"),
        ("[display]\nwindow_scale = 99\n", "window_scale"),
        ("[kismet]\nbase_url = 'ftp://host'\n", "base_url"),
        ("[kismet]\nbase_url = 'http://user:pw@host:2501'\n", "credentials"),
        ("[kismet]\nmax_devices = 0\n", "max_devices"),
        ("[polling]\ninterval_seconds = 0\n", "interval_seconds"),
        ("[polling]\nstale_after_seconds = 30\noffline_after_seconds = 5\n", "offline_after"),
        ("[http]\nbackoff_factor = 0.5\n", "backoff_factor"),
        ("[http]\nbackoff_jitter = 2.0\n", "backoff_jitter"),
        ("[privacy]\nallow_uploads = true\n", "never uploads"),
        ("[privacy]\nenable_gps = true\n", "no location"),
        ("mode = 'offensive'\n", "mock"),
        ("[logging]\nlevel = 'CHATTY'\n", "level"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, body: str, message: str) -> None:
    path = write_config(tmp_path, body)
    with pytest.raises(ConfigError, match=message):
        load_config(path, env={})


def test_booleans_reject_non_boolean_values(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[display]\nfullscreen = 3\n")
    with pytest.raises(ConfigError, match="boolean"):
        load_config(path, env={})


# --- Token handling --------------------------------------------------------------------


def test_token_absent_returns_none() -> None:
    assert get_api_token(env={}) is None
    assert get_api_token(env={API_TOKEN_ENV: "   "}) is None


def test_token_is_read_from_the_environment() -> None:
    assert get_api_token(env={API_TOKEN_ENV: " secret-value "}) == "secret-value"


def test_token_is_not_part_of_the_config_object(tmp_path: Path) -> None:
    """The token must be unreachable from a config dump, repr or serialisation."""
    path = write_config(tmp_path, 'mode = "live"\n')
    config = load_config(path, env={API_TOKEN_ENV: "super-secret-token"})

    from dataclasses import asdict

    assert "super-secret-token" not in repr(config)
    assert "super-secret-token" not in str(asdict(config))
    assert not hasattr(config.kismet, "api_token")
