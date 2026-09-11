"""Rendering tests: exact canvas size, layout invariants, and every state renders.

These are deliberately *not* golden-image comparisons. A full-image hash breaks on any font
or antialiasing difference between machines and teaches you nothing when it fails. Instead
these assert the things that actually matter: the canvas is exactly 320x240, content stays
inside its band, nothing is drawn over the chrome, and no state -- empty, offline, stale,
hostile -- raises while drawing.
"""

from __future__ import annotations

from dataclasses import replace

import pygame
import pytest

from pisight.config import AppConfig
from pisight.models import (
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
from pisight.polling.store import SnapshotState
from pisight.providers.mock import screenshot_snapshot
from pisight.screens.base import RenderContext
from pisight.ui.app import build_screens
from pisight.ui.chrome import draw_navigation_bar, draw_status_bar
from pisight.ui.fonts import FontSet
from pisight.ui.layout import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CONTENT_RECT,
    NAV_Y,
    STATUS_HEIGHT,
)
from pisight.ui.navigation import ScreenId
from pisight.ui.theme import BACKGROUND

MARKER = (255, 0, 255)
"""A colour the palette never uses, so overdraw is unambiguous."""


def make_context(
    fonts: FontSet,
    snapshot: DashboardSnapshot | None,
    *,
    config: AppConfig | None = None,
    is_stale: bool = False,
    is_offline: bool = False,
    failures: int = 0,
) -> RenderContext:
    state = SnapshotState(
        snapshot=snapshot,
        sequence=1 if snapshot else 0,
        consecutive_failures=failures,
        last_error="connection refused" if failures else None,
        dropped=0,
    )
    return RenderContext(
        snapshot=snapshot,
        state=state,
        fonts=fonts,
        config=config or AppConfig(),
        uptime_seconds=3600.0,
        snapshot_age=1.0 if snapshot else None,
        is_stale=is_stale,
        is_offline=is_offline,
        now_wall=1_717_243_800.0,
    )


def render_full(fonts: FontSet, ctx: RenderContext, screen_id: ScreenId) -> pygame.Surface:
    """Draw a complete frame: background, status bar, screen and navigation."""
    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    surface.fill(BACKGROUND)
    draw_status_bar(surface, ctx)
    build_screens()[screen_id].draw(surface, CONTENT_RECT, ctx)
    draw_navigation_bar(surface, ctx, screen_id)
    return surface


ALL_SCREENS = list(ScreenId)


# --- Canvas size -----------------------------------------------------------------------


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_every_screen_renders_at_exactly_320x240(
    fonts: FontSet, snapshot: DashboardSnapshot, screen_id: ScreenId
) -> None:
    surface = render_full(fonts, make_context(fonts, snapshot), screen_id)
    assert surface.get_size() == (320, 240)


# --- Layout containment ----------------------------------------------------------------


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_a_screen_never_draws_outside_the_content_band(
    fonts: FontSet, snapshot: DashboardSnapshot, screen_id: ScreenId
) -> None:
    """Fill the chrome with a marker colour; a screen that overdraws it will erase it."""
    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    surface.fill(MARKER)
    build_screens()[screen_id].draw(surface, CONTENT_RECT, make_context(fonts, snapshot))

    # Status bar: every row above the content band is untouched.
    for y in range(STATUS_HEIGHT):
        for x in range(0, CANVAS_WIDTH, 7):
            assert surface.get_at((x, y))[:3] == MARKER, (
                f"content drew into the status bar at {x},{y}"
            )

    # Navigation bar: every row below the content band is untouched.
    for y in range(NAV_Y, CANVAS_HEIGHT):
        for x in range(0, CANVAS_WIDTH, 7):
            assert surface.get_at((x, y))[:3] == MARKER, f"content drew into the nav bar at {x},{y}"


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_content_actually_fills_its_band(
    fonts: FontSet, snapshot: DashboardSnapshot, screen_id: ScreenId
) -> None:
    """A screen that silently drew nothing would pass the containment test; this catches it."""
    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    surface.fill(MARKER)
    build_screens()[screen_id].draw(surface, CONTENT_RECT, make_context(fonts, snapshot))

    painted = sum(
        1
        for y in range(CONTENT_RECT.y, CONTENT_RECT.bottom, 3)
        for x in range(0, CANVAS_WIDTH, 3)
        if surface.get_at((x, y))[:3] != MARKER
    )
    assert painted > 200, f"{screen_id.name} barely drew anything"


def test_the_status_bar_stays_inside_its_24_pixels(fonts: FontSet) -> None:
    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    surface.fill(MARKER)
    draw_status_bar(surface, make_context(fonts, screenshot_snapshot()))

    for y in range(STATUS_HEIGHT, CANVAS_HEIGHT):
        for x in range(0, CANVAS_WIDTH, 11):
            assert surface.get_at((x, y))[:3] == MARKER


def test_the_nav_bar_stays_inside_its_40_pixels(fonts: FontSet) -> None:
    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    surface.fill(MARKER)
    draw_navigation_bar(surface, make_context(fonts, screenshot_snapshot()), ScreenId.OVERVIEW)

    for y in range(0, NAV_Y):
        for x in range(0, CANVAS_WIDTH, 11):
            assert surface.get_at((x, y))[:3] == MARKER


# --- Every state renders ---------------------------------------------------------------


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_screens_render_with_no_snapshot_at_all(fonts: FontSet, screen_id: ScreenId) -> None:
    """Before the first poll returns, every screen must show something, not crash."""
    surface = render_full(fonts, make_context(fonts, None, failures=1), screen_id)
    assert surface.get_size() == (320, 240)


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_screens_render_with_a_completely_empty_snapshot(
    fonts: FontSet, screen_id: ScreenId
) -> None:
    empty = DashboardSnapshot(source_name="test")
    surface = render_full(fonts, make_context(fonts, empty), screen_id)
    assert surface.get_size() == (320, 240)


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_screens_render_while_stale(
    fonts: FontSet, snapshot: DashboardSnapshot, screen_id: ScreenId
) -> None:
    ctx = make_context(fonts, snapshot, is_stale=True)
    assert render_full(fonts, ctx, screen_id).get_size() == (320, 240)


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_screens_render_while_offline(
    fonts: FontSet, snapshot: DashboardSnapshot, screen_id: ScreenId
) -> None:
    offline = replace(
        snapshot,
        capture=replace(snapshot.capture, kismet_online=False, last_error="connection refused"),
    )
    ctx = make_context(fonts, offline, is_offline=True, failures=4)
    assert render_full(fonts, ctx, screen_id).get_size() == (320, 240)


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_screens_render_hostile_content(fonts: FontSet, screen_id: ScreenId) -> None:
    """Extreme values and adversarial names must not break layout or raise."""
    nasty_name = "‮" + "W" * 300
    devices = tuple(
        DeviceSummary(
            key=f"k{i}",
            mac="02:00:5E:00:00:01",
            display_name=nasty_name,
            device_type=DeviceType.UNKNOWN,
            channel="9" * 20,
            band=Band.UNKNOWN,
            signal_dbm=None,
            last_seen=0.0,
            packets=999_999_999,
            crypt="X" * 60,
            is_new=True,
        )
        for i in range(60)
    )
    alerts = tuple(
        AlertSummary(
            timestamp=0.0,
            severity=Severity.CRITICAL,
            header="H" * 80,
            text="T" * 500,
            source_mac="02:00:5E:00:00:01",
        )
        for _ in range(40)
    )
    channels = tuple(
        ChannelSummary(channel="C" * 15, band=Band.UNKNOWN, device_count=10**9) for _ in range(80)
    )
    snapshot = DashboardSnapshot(
        capture=CaptureStatus(
            kismet_online=True,
            packets_per_second=10**9,
            total_devices=10**9,
            current_channel="Z" * 40,
            hopping=True,
        ),
        host=HostHealth(cpu_temp_c=999.0, disk_total_bytes=1, disk_free_bytes=0),
        devices=devices,
        channels=channels,
        alerts=alerts,
        datasources=(
            DatasourceSummary(uuid="u" * 60, name="n" * 60, interface="i" * 60, error="e" * 200),
        ),
        source_name="test",
        warnings=("w" * 300,),
    )

    surface = render_full(fonts, make_context(fonts, snapshot), screen_id)
    assert surface.get_size() == (320, 240)


@pytest.mark.parametrize("screen_id", ALL_SCREENS)
def test_screens_render_with_full_mac_display_enabled(
    fonts: FontSet, snapshot: DashboardSnapshot, screen_id: ScreenId
) -> None:
    config = AppConfig()
    config = replace(config, privacy=replace(config.privacy, show_full_mac=True))
    ctx = make_context(fonts, snapshot, config=config)
    assert render_full(fonts, ctx, screen_id).get_size() == (320, 240)


# --- Chrome states ---------------------------------------------------------------------


def test_status_bar_renders_in_every_freshness_state(fonts: FontSet) -> None:
    snapshot = screenshot_snapshot()
    for stale, offline in ((False, False), (True, False), (False, True), (True, True)):
        surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
        draw_status_bar(surface, make_context(fonts, snapshot, is_stale=stale, is_offline=offline))
    # Also with no snapshot at all.
    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    draw_status_bar(surface, make_context(fonts, None))


@pytest.mark.parametrize("active", ALL_SCREENS)
def test_the_active_nav_button_is_visually_distinct(fonts: FontSet, active: ScreenId) -> None:
    from pisight.ui.layout import nav_button_rect
    from pisight.ui.theme import NAV_ACTIVE

    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    draw_navigation_bar(surface, make_context(fonts, screenshot_snapshot()), active)

    for index in range(len(ScreenId)):
        button = nav_button_rect(index)
        # Sample a corner, away from the label glyphs.
        pixel = surface.get_at((button.x + 3, button.y + 3))[:3]
        if index == int(active):
            assert pixel == NAV_ACTIVE
        else:
            assert pixel != NAV_ACTIVE


# --- Ellipsis --------------------------------------------------------------------------


def test_ellipsize_shortens_to_fit_and_marks_the_cut(fonts: FontSet) -> None:
    text = "atrium-public-wifi-north-wing-guest-portal-registration-desk"
    result = fonts.ellipsize(fonts.small, text, 80)

    assert result != text
    assert result.endswith("…")
    assert fonts.measure(fonts.small, result) <= 80


def test_ellipsize_leaves_short_text_alone(fonts: FontSet) -> None:
    assert fonts.ellipsize(fonts.small, "ok", 200) == "ok"


def test_ellipsize_handles_a_width_of_zero(fonts: FontSet) -> None:
    assert fonts.ellipsize(fonts.small, "anything", 0) == ""


def test_ellipsize_handles_a_width_narrower_than_the_ellipsis(fonts: FontSet) -> None:
    assert fonts.ellipsize(fonts.small, "anything", 1) == ""


def test_a_font_is_always_available(fonts: FontSet) -> None:
    assert fonts.family
    for font in (fonts.tiny, fonts.small, fonts.body, fonts.large, fonts.huge):
        assert font.get_height() > 0


def test_font_sizes_are_ordered(fonts: FontSet) -> None:
    heights = [
        fonts.tiny.get_height(),
        fonts.small.get_height(),
        fonts.body.get_height(),
        fonts.large.get_height(),
        fonts.huge.get_height(),
    ]
    assert heights == sorted(heights)


# --- Devices screen row cap ------------------------------------------------------------


def test_devices_screen_shows_at_most_five_rows(fonts: FontSet) -> None:
    from pisight.screens.devices import MAX_ROWS

    assert MAX_ROWS == 5

    many = tuple(
        DeviceSummary(
            key=f"k{i}",
            mac="02:00:5E:00:00:01",
            display_name=f"device-{i}",
            last_seen=float(i),
        )
        for i in range(50)
    )
    snapshot = DashboardSnapshot(devices=many, source_name="test")
    surface = render_full(fonts, make_context(fonts, snapshot), ScreenId.DEVICES)
    assert surface.get_size() == (320, 240)
