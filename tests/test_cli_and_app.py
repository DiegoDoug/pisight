"""End-to-end CLI behaviour, the smoke run, screenshot generation and the doctor command."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pygame
import pytest

from pisight.cli import build_parser, build_provider, main
from pisight.config import API_TOKEN_ENV, AppConfig, ConfigError
from pisight.doctor import DoctorCheck, DoctorReport, format_report, run_doctor
from pisight.polling.coordinator import PollingCoordinator
from pisight.providers.mock import MockDashboardProvider
from pisight.ui.app import PiSightApp, build_render_context, build_screens
from pisight.ui.fonts import FontSet
from pisight.ui.layout import CANVAS_HEIGHT, CANVAS_WIDTH, nav_button_rect
from pisight.ui.navigation import ScreenId
from pisight.ui.screenshots import SCREENSHOT_FILENAMES, generate_screenshots

# --- Parser ----------------------------------------------------------------------------


def test_parser_defaults_to_the_run_command() -> None:
    args = build_parser().parse_args([])
    assert args.command is None
    assert args.mode is None


def test_parser_accepts_every_subcommand() -> None:
    parser = build_parser()
    assert parser.parse_args(["doctor", "--json"]).json is True
    assert parser.parse_args(["screenshots", "--output", "x"]).output == "x"
    assert parser.parse_args(["config"]).command == "config"
    assert parser.parse_args(["--mode", "live"]).mode == "live"
    assert parser.parse_args(["--smoke-seconds", "2.5"]).smoke_seconds == 2.5


def test_parser_rejects_an_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--mode", "attack"])


# --- Provider selection ----------------------------------------------------------------


def test_mock_mode_builds_the_mock_provider() -> None:
    provider = build_provider(AppConfig())
    try:
        assert provider.name == "mock"
    finally:
        provider.close()


def test_live_mode_without_a_token_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    config = replace(AppConfig(), mode="live")

    with pytest.raises(ConfigError, match=API_TOKEN_ENV):
        build_provider(config)


def test_live_mode_never_silently_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Showing synthetic data to someone who asked for live data would be dangerous."""
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    config = replace(AppConfig(), mode="live")

    with pytest.raises(ConfigError):
        provider = build_provider(config)
        provider.close()


def test_live_mode_with_a_token_builds_the_kismet_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "synthetic-token-value")
    provider = build_provider(replace(AppConfig(), mode="live"))
    try:
        assert provider.name == "kismet"
    finally:
        provider.close()


# --- CLI commands ----------------------------------------------------------------------


def test_config_command_prints_settings_without_the_token(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "super-secret-token-value")
    monkeypatch.delenv("PISIGHT_CONFIG", raising=False)

    assert main(["config"]) == 0
    output = capsys.readouterr().out

    assert "super-secret-token-value" not in output
    assert "api_token_present" in output
    assert "[display]" in output


def test_bad_config_path_exits_with_a_usage_code(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["--config", str(tmp_path / "absent.toml"), "config"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_version_flag_exits_cleanly() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0


def test_doctor_command_runs_off_pi_without_crashing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["doctor", "--no-network"])
    output = capsys.readouterr().out

    assert code in (0, 1)
    assert "PiSight doctor" in output
    assert "changes nothing on the system" in output


def test_doctor_json_output_is_valid_and_token_free(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "super-secret-token-value")

    main(["doctor", "--json", "--no-network"])
    payload = json.loads(capsys.readouterr().out)

    assert "checks" in payload
    assert isinstance(payload["checks"], list)
    assert payload["checks"]
    assert "super-secret-token-value" not in json.dumps(payload)


def test_doctor_reports_token_presence_without_revealing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "super-secret-token-value")
    report = run_doctor(AppConfig(), check_kismet=False)
    rendered = format_report(report)
    assert "super-secret-token-value" not in rendered


def test_doctor_checks_cover_the_required_areas() -> None:
    report = run_doctor(AppConfig(), check_kismet=False)
    categories = {check.category for check in report.checks}
    for required in ("platform", "display", "hardware", "storage"):
        assert required in categories

    names = {check.name for check in report.checks}
    assert {"operating system", "architecture", "python", "wireless interfaces"} <= names


def test_doctor_report_roll_up() -> None:
    report = DoctorReport(
        (
            DoctorCheck("a", "ok", ""),
            DoctorCheck("b", "warn", ""),
            DoctorCheck("c", "fail", ""),
            DoctorCheck("d", "skip", ""),
        )
    )
    assert report.failures == 1
    assert report.warnings == 1
    assert report.exit_code == 1
    assert DoctorReport((DoctorCheck("a", "warn", ""),)).exit_code == 0


# --- Smoke run -------------------------------------------------------------------------


def test_smoke_run_starts_renders_and_exits_cleanly() -> None:
    assert main(["--mode", "mock", "--smoke-seconds", "0.4"]) == 0


def test_smoke_run_actually_renders_frames() -> None:
    provider = MockDashboardProvider(seed=1, evolving=False)
    coordinator = PollingCoordinator(provider, AppConfig())
    coordinator.start()
    try:
        app = PiSightApp(AppConfig(), coordinator)
        assert app.run(smoke_seconds=0.3) == 0
        assert app.frames_rendered > 0
    finally:
        coordinator.stop(timeout=3.0)


def test_the_app_tears_down_cleanly_and_can_start_again() -> None:
    """A failed teardown would leave SDL initialised and break the next run."""
    for _ in range(2):
        assert main(["--mode", "mock", "--smoke-seconds", "0.2"]) == 0


# --- Render context --------------------------------------------------------------------


def test_render_context_before_any_snapshot(fonts: FontSet) -> None:
    coordinator = PollingCoordinator(MockDashboardProvider(evolving=False), AppConfig())
    ctx = build_render_context(coordinator, AppConfig(), fonts, uptime_seconds=1.0)

    assert ctx.snapshot is None
    assert ctx.snapshot_age is None
    assert ctx.is_stale is False
    assert ctx.has_data is False


def test_render_context_flags_staleness_and_offline(fonts: FontSet) -> None:
    config = AppConfig()
    provider = MockDashboardProvider(seed=1, evolving=False)
    coordinator = PollingCoordinator(provider, config)
    coordinator.poll_once()

    snapshot = coordinator.store.get().snapshot
    assert snapshot is not None

    fresh = build_render_context(
        coordinator,
        config,
        fonts,
        uptime_seconds=1.0,
        now_monotonic=snapshot.monotonic_at + 1.0,
    )
    assert fresh.is_stale is False
    assert fresh.is_offline is False

    stale = build_render_context(
        coordinator,
        config,
        fonts,
        uptime_seconds=1.0,
        now_monotonic=snapshot.monotonic_at + config.polling.stale_after_seconds + 1,
    )
    assert stale.is_stale is True

    offline = build_render_context(
        coordinator,
        config,
        fonts,
        uptime_seconds=1.0,
        now_monotonic=snapshot.monotonic_at + config.polling.offline_after_seconds + 1,
    )
    assert offline.is_offline is True
    provider.close()


def test_render_context_marks_offline_when_the_snapshot_says_so(fonts: FontSet) -> None:
    from pisight.providers.base import ProviderUnavailable

    class Failing:
        name = "failing"

        def __init__(self) -> None:
            self._first = True

        def fetch(self):  # type: ignore[no-untyped-def]
            if self._first:
                self._first = False
                return MockDashboardProvider(seed=1, evolving=False).fetch()
            raise ProviderUnavailable("down")

        def close(self) -> None:
            return None

    coordinator = PollingCoordinator(Failing(), AppConfig())
    coordinator.poll_once()
    coordinator.poll_once()

    ctx = build_render_context(coordinator, AppConfig(), fonts, uptime_seconds=1.0)
    assert ctx.is_offline is True
    assert ctx.snapshot is not None  # observations retained


# --- Input translation -----------------------------------------------------------------


def test_window_coordinates_translate_to_canvas_coordinates() -> None:
    coordinator = PollingCoordinator(MockDashboardProvider(evolving=False), AppConfig())
    config = replace(AppConfig(), display=replace(AppConfig().display, window_scale=3))
    app = PiSightApp(config, coordinator)
    app.setup()
    try:
        # Centre of the window maps to the centre of the logical canvas.
        assert app.window_to_canvas(CANVAS_WIDTH * 3 // 2, CANVAS_HEIGHT * 3 // 2) == (160, 120)
        assert app.window_to_canvas(0, 0) == (0, 0)

        # A click on the third nav button selects the Devices screen.
        button = nav_button_rect(int(ScreenId.DEVICES))
        event = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            {"button": 1, "pos": (button.center_x * 3, button.center_y * 3)},
        )
        app.handle_event(event, now_ms=10_000)
        assert app._navigator.current is ScreenId.DEVICES
    finally:
        app.teardown()
        coordinator.stop()


def test_keyboard_navigation() -> None:
    coordinator = PollingCoordinator(MockDashboardProvider(evolving=False), AppConfig())
    app = PiSightApp(AppConfig(), coordinator)
    app.setup()
    try:
        navigator = app._navigator

        for key, expected in (
            (pygame.K_2, ScreenId.CHANNELS),
            (pygame.K_3, ScreenId.DEVICES),
            (pygame.K_4, ScreenId.ALERTS),
            (pygame.K_1, ScreenId.OVERVIEW),
        ):
            app.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": key, "mod": 0}), 0)
            assert navigator.current is expected

        app.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RIGHT, "mod": 0}), 0)
        assert navigator.current is ScreenId.CHANNELS
        app.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_LEFT, "mod": 0}), 0)
        assert navigator.current is ScreenId.OVERVIEW

        # Escape exits in windowed development mode.
        escape = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE, "mod": 0})
        assert app.handle_event(escape, 0) is False

        # QUIT always exits.
        assert app.handle_event(pygame.event.Event(pygame.QUIT), 0) is False
    finally:
        app.teardown()
        coordinator.stop()


def test_escape_does_not_exit_in_fullscreen() -> None:
    """On the Pi the app is the session; Escape must not leave a blank screen."""
    config = replace(AppConfig(), display=replace(AppConfig().display, fullscreen=True))
    coordinator = PollingCoordinator(MockDashboardProvider(evolving=False), AppConfig())
    app = PiSightApp(config, coordinator)
    app.setup()
    try:
        escape = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE, "mod": 0})
        assert app.handle_event(escape, 0) is True
    finally:
        app.teardown()
        coordinator.stop()


# --- Screenshots -----------------------------------------------------------------------


def test_screenshots_are_written_at_exactly_320x240(tmp_path: Path) -> None:
    paths = generate_screenshots(tmp_path, AppConfig())

    assert len(paths) == 4
    expected = set(SCREENSHOT_FILENAMES.values())
    assert {path.name for path in paths} == expected
    assert expected == {"overview.png", "channels.png", "devices.png", "alerts-system.png"}

    for path in paths:
        assert path.exists()
        assert path.stat().st_size > 0
        surface = pygame.image.load(str(path))
        assert surface.get_size() == (320, 240), f"{path.name} is not 320x240"


def test_screenshot_command_via_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["screenshots", "--output", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    for name in SCREENSHOT_FILENAMES.values():
        assert name in output


def test_screenshots_are_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    generate_screenshots(first, AppConfig())
    generate_screenshots(second, AppConfig())

    for name in SCREENSHOT_FILENAMES.values():
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_every_screen_has_a_screenshot_filename() -> None:
    assert set(SCREENSHOT_FILENAMES) == set(ScreenId)
    assert set(build_screens()) == set(ScreenId)
