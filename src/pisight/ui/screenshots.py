"""Deterministic screenshot generation for documentation and CI artifacts.

Each screen is rendered to a 320x240 surface from a fixed-seed mock snapshot with a fixed
wall-clock time, so re-running the command produces identical images. That determinism is
what makes the screenshots useful in review: a visual diff means the UI actually changed.

This never opens a real window -- it forces the SDL dummy driver -- so it runs in CI and
over SSH without a display attached.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pygame

from ..config import AppConfig
from ..models import DashboardSnapshot
from ..polling.store import SnapshotState
from ..providers.mock import DETERMINISTIC_WALL_TIME, screenshot_snapshot
from ..screens.base import RenderContext, Screen
from . import theme
from .chrome import draw_navigation_bar, draw_status_bar
from .fonts import load_fonts
from .layout import CANVAS_HEIGHT, CANVAS_WIDTH, CONTENT_RECT
from .navigation import ScreenId

__all__ = ["SCREENSHOT_FILENAMES", "generate_screenshots", "render_screen_surface"]

logger = logging.getLogger(__name__)

#: Output filename for each screen, as required by the product specification.
SCREENSHOT_FILENAMES: dict[ScreenId, str] = {
    ScreenId.OVERVIEW: "overview.png",
    ScreenId.CHANNELS: "channels.png",
    ScreenId.DEVICES: "devices.png",
    ScreenId.ALERTS: "alerts-system.png",
}

#: Fixed wall-clock time so the status-bar clock is identical in every run. Shared with the
#: mock provider's deterministic clock so observation ages render realistically.
_FIXED_WALL_TIME = DETERMINISTIC_WALL_TIME
_FIXED_UPTIME = 3_930.0  # 1h05m


def _context(snapshot: DashboardSnapshot, config: AppConfig, fonts: object) -> RenderContext:
    """Build a deterministic render context for screenshot rendering."""
    from .fonts import FontSet

    assert isinstance(fonts, FontSet)
    state = SnapshotState(
        snapshot=snapshot,
        sequence=1,
        consecutive_failures=0,
        last_error=None,
        dropped=0,
    )
    return RenderContext(
        snapshot=snapshot,
        state=state,
        fonts=fonts,
        config=config,
        uptime_seconds=_FIXED_UPTIME,
        snapshot_age=1.2,
        is_stale=False,
        is_offline=False,
        now_wall=_FIXED_WALL_TIME,
    )


def render_screen_surface(
    screen_id: ScreenId,
    screen: Screen,
    snapshot: DashboardSnapshot,
    config: AppConfig,
) -> pygame.Surface:
    """Render one complete 320x240 frame -- chrome included -- for ``screen_id``."""
    fonts = load_fonts()
    ctx = _context(snapshot, config, fonts)

    surface = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
    surface.fill(theme.BACKGROUND)
    draw_status_bar(surface, ctx)
    screen.draw(surface, CONTENT_RECT, ctx)
    draw_navigation_bar(surface, ctx, screen_id)
    return surface


def generate_screenshots(output_dir: str | Path, config: AppConfig) -> list[Path]:
    """Render all four screens into ``output_dir`` and return the written paths."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    from .app import build_screens

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    initialised_here = not pygame.get_init()
    if initialised_here:
        pygame.init()
    if pygame.display.get_surface() is None:
        # A display surface must exist before Surface.convert()/font rendering on some
        # SDL builds; the dummy driver makes this a no-op window.
        pygame.display.set_mode((CANVAS_WIDTH, CANVAS_HEIGHT))

    snapshot = screenshot_snapshot()
    screens = build_screens()
    written: list[Path] = []

    try:
        for screen_id, filename in SCREENSHOT_FILENAMES.items():
            surface = render_screen_surface(screen_id, screens[screen_id], snapshot, config)
            if surface.get_size() != (CANVAS_WIDTH, CANVAS_HEIGHT):
                raise RuntimeError(
                    f"{filename}: expected {CANVAS_WIDTH}x{CANVAS_HEIGHT}, got {surface.get_size()}"
                )
            path = destination / filename
            pygame.image.save(surface, str(path))
            written.append(path)
            logger.info("wrote %s", path)
    finally:
        if initialised_here:
            pygame.display.quit()
            pygame.quit()

    return written
