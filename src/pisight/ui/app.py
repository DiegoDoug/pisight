"""The Pygame application: event loop, scaling and frame composition.

Rendering always targets a 320x240 logical surface. That surface is then scaled to the
window with :func:`pygame.transform.scale`, which is nearest-neighbour, so a 3x development
window shows exactly the pixels the TFT will show -- no resampling blur, no sub-pixel
drift, and no separate layout path to maintain for the desktop.

The loop never performs I/O. It reads the newest snapshot from the store, draws, and waits
on the clock. All network and filesystem work happens on the polling thread.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping

import pygame

from ..config import AppConfig
from ..polling.coordinator import PollingCoordinator
from ..screens.alerts_system import AlertsSystemScreen
from ..screens.base import RenderContext, Screen
from ..screens.channels import ChannelsScreen
from ..screens.devices import DevicesScreen
from ..screens.overview import OverviewScreen
from . import theme
from .chrome import draw_navigation_bar, draw_status_bar
from .fonts import FontSet, load_fonts
from .layout import CANVAS_HEIGHT, CANVAS_WIDTH, CONTENT_RECT
from .navigation import InputRouter, Navigator, PointerEvent, ScreenId

__all__ = ["PiSightApp", "build_render_context", "build_screens"]

logger = logging.getLogger(__name__)


def build_screens() -> dict[ScreenId, Screen]:
    """Instantiate the four screens, keyed by navigation identity."""
    return {
        ScreenId.OVERVIEW: OverviewScreen(),
        ScreenId.CHANNELS: ChannelsScreen(),
        ScreenId.DEVICES: DevicesScreen(),
        ScreenId.ALERTS: AlertsSystemScreen(),
    }


def build_render_context(
    coordinator: PollingCoordinator,
    config: AppConfig,
    fonts: FontSet,
    *,
    uptime_seconds: float,
    now_wall: float | None = None,
    now_monotonic: float | None = None,
) -> RenderContext:
    """Assemble the per-frame context from the store's current state.

    "Offline" is deliberately stricter than "stale": stale means the data has aged past the
    threshold; offline means either the source told us it is down, or nothing has arrived
    for long enough that we should stop implying the display is live.
    """
    state = coordinator.store.get()
    snapshot = state.snapshot
    monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    wall = time.time() if now_wall is None else now_wall

    age = snapshot.age_seconds(now_monotonic=monotonic) if snapshot is not None else None
    is_stale = bool(
        snapshot is not None and age is not None and age > config.polling.stale_after_seconds
    )
    is_offline = (
        (snapshot is None and state.consecutive_failures > 0)
        or (snapshot is not None and not snapshot.capture.kismet_online)
        or (age is not None and age > config.polling.offline_after_seconds)
    )

    return RenderContext(
        snapshot=snapshot,
        state=state,
        fonts=fonts,
        config=config,
        uptime_seconds=uptime_seconds,
        snapshot_age=age,
        is_stale=is_stale,
        is_offline=is_offline,
        now_wall=wall,
    )


def apply_sdl_environment(config: AppConfig, env: Mapping[str, str] | None = None) -> None:
    """Set ``SDL_VIDEODRIVER`` from configuration when the environment has not already.

    An explicit environment variable always wins: on the Pi the systemd unit sets it, and
    on a desktop a developer may need to override it for a specific session.
    """
    environ = os.environ if env is None else env
    driver = config.display.sdl_video_driver
    if driver and not environ.get("SDL_VIDEODRIVER"):
        os.environ["SDL_VIDEODRIVER"] = driver
        logger.info("SDL_VIDEODRIVER set to %s from configuration", driver)


class PiSightApp:
    """Owns the window, the event loop and the frame composition."""

    def __init__(
        self,
        config: AppConfig,
        coordinator: PollingCoordinator,
        *,
        process_start: float | None = None,
    ) -> None:
        self._config = config
        self._coordinator = coordinator
        self._process_start = time.monotonic() if process_start is None else process_start

        self._navigator = Navigator()
        self._router = InputRouter(self._navigator)
        self._screens = build_screens()

        self._canvas: pygame.Surface | None = None
        self._window: pygame.Surface | None = None
        self._clock: pygame.time.Clock | None = None
        self._fonts: FontSet | None = None
        self._scale = max(1, config.display.window_scale)
        self._offset = (0, 0)
        self._running = False
        self.frames_rendered = 0

    # ---------------------------------------------------------------------------------
    # Setup and teardown
    # ---------------------------------------------------------------------------------

    def setup(self) -> None:
        """Initialise pygame, the window and the fonts."""
        apply_sdl_environment(self._config)
        pygame.init()
        pygame.display.set_caption("PiSight")

        flags = pygame.FULLSCREEN if self._config.display.fullscreen else 0
        if self._config.display.fullscreen:
            window_size = (self._config.display.width, self._config.display.height)
        else:
            window_size = (CANVAS_WIDTH * self._scale, CANVAS_HEIGHT * self._scale)

        self._window = pygame.display.set_mode(window_size, flags)
        self._canvas = pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))
        self._clock = pygame.time.Clock()
        self._fonts = load_fonts()
        self._recompute_scale()

        try:
            pygame.mouse.set_visible(not self._config.display.fullscreen)
        except pygame.error:  # pragma: no cover - dummy driver may not support the cursor
            logger.debug("cursor visibility not supported by this SDL driver")

        logger.info(
            "display ready: window=%sx%s scale=%s font=%s",
            window_size[0],
            window_size[1],
            self._scale,
            self._fonts.family,
        )

    def _recompute_scale(self) -> None:
        """Pick the largest integer scale that fits, and centre the canvas in the window.

        Integer-only scaling is what keeps the 1px dividers and the tiny type crisp; a
        fractional scale would smear them into grey.
        """
        window = self._window
        if window is None:
            return
        width, height = window.get_size()
        scale = max(1, min(width // CANVAS_WIDTH, height // CANVAS_HEIGHT))
        self._scale = scale
        self._offset = (
            (width - CANVAS_WIDTH * scale) // 2,
            (height - CANVAS_HEIGHT * scale) // 2,
        )

    def teardown(self) -> None:
        """Shut the display down. Safe to call more than once."""
        self._running = False
        try:
            pygame.display.quit()
            pygame.quit()
        except pygame.error:  # pragma: no cover - already torn down
            logger.debug("pygame already shut down")

    # ---------------------------------------------------------------------------------
    # Input
    # ---------------------------------------------------------------------------------

    def window_to_canvas(self, x: int, y: int) -> tuple[int, int]:
        """Translate a window pixel coordinate into logical canvas coordinates."""
        canvas_x = (x - self._offset[0]) // self._scale
        canvas_y = (y - self._offset[1]) // self._scale
        return (int(canvas_x), int(canvas_y))

    def handle_event(self, event: pygame.event.Event, now_ms: float) -> bool:
        """Handle one SDL event. Returns False when the application should exit."""
        if event.type == pygame.QUIT:
            return False

        if event.type == pygame.KEYDOWN:
            return self._handle_key(event)

        if event.type == pygame.MOUSEBUTTONDOWN and getattr(event, "button", 1) == 1:
            x, y = self.window_to_canvas(*event.pos)
            self._router.handle_pointer(PointerEvent(x, y, source="mouse"), now_ms)
            return True

        if event.type == pygame.FINGERDOWN:
            # SDL reports touch positions normalised to 0..1 of the window.
            window = self._window
            if window is not None:
                width, height = window.get_size()
                x, y = self.window_to_canvas(int(event.x * width), int(event.y * height))
                self._router.handle_pointer(PointerEvent(x, y, source="touch"), now_ms)
            return True

        if event.type == pygame.VIDEORESIZE:
            self._recompute_scale()
            return True

        return True

    def _handle_key(self, event: pygame.event.Event) -> bool:
        """Keyboard navigation. Returns False only for the development-mode exit key."""
        key = event.key
        if key == pygame.K_ESCAPE:
            if self._config.display.fullscreen:
                # On the Pi the app is the session; Escape must not strand a blank screen.
                logger.info("Escape ignored in fullscreen mode")
                return True
            return False
        if key in (pygame.K_q,) and (event.mod & pygame.KMOD_CTRL):
            return False

        digit_map = {
            pygame.K_1: ScreenId.OVERVIEW,
            pygame.K_2: ScreenId.CHANNELS,
            pygame.K_3: ScreenId.DEVICES,
            pygame.K_4: ScreenId.ALERTS,
            pygame.K_KP1: ScreenId.OVERVIEW,
            pygame.K_KP2: ScreenId.CHANNELS,
            pygame.K_KP3: ScreenId.DEVICES,
            pygame.K_KP4: ScreenId.ALERTS,
        }
        if key in digit_map:
            self._navigator.select(digit_map[key])
        elif key == pygame.K_RIGHT:
            self._navigator.next()
        elif key == pygame.K_LEFT:
            self._navigator.previous()
        return True

    # ---------------------------------------------------------------------------------
    # Rendering
    # ---------------------------------------------------------------------------------

    @property
    def uptime_seconds(self) -> float:
        """Seconds since the application started."""
        return max(0.0, time.monotonic() - self._process_start)

    def render_frame(self, *, now_wall: float | None = None) -> pygame.Surface:
        """Compose one frame onto the logical canvas and return it."""
        canvas = self._canvas
        fonts = self._fonts
        if canvas is None or fonts is None:
            raise RuntimeError("PiSightApp.setup() must be called before rendering")

        ctx = build_render_context(
            self._coordinator,
            self._config,
            fonts,
            uptime_seconds=self.uptime_seconds,
            now_wall=now_wall,
        )

        canvas.fill(theme.BACKGROUND)
        draw_status_bar(canvas, ctx)
        self._screens[self._navigator.current].draw(canvas, CONTENT_RECT, ctx)
        draw_navigation_bar(canvas, ctx, self._navigator.current)
        self.frames_rendered += 1
        return canvas

    def present(self) -> None:
        """Scale the canvas into the window and flip."""
        window = self._window
        canvas = self._canvas
        if window is None or canvas is None:
            return
        window.fill((0, 0, 0))
        if self._scale == 1 and self._offset == (0, 0):
            window.blit(canvas, (0, 0))
        else:
            scaled = pygame.transform.scale(
                canvas, (CANVAS_WIDTH * self._scale, CANVAS_HEIGHT * self._scale)
            )
            window.blit(scaled, self._offset)
        pygame.display.flip()

    # ---------------------------------------------------------------------------------
    # Main loop
    # ---------------------------------------------------------------------------------

    def run(self, *, smoke_seconds: float | None = None) -> int:
        """Run the event loop until exit, or for ``smoke_seconds`` then exit cleanly.

        Returns a process exit code. The smoke path is what CI uses: it exercises the real
        loop -- events, polling thread, rendering, teardown -- and then returns 0.
        """
        self.setup()
        self._running = True
        deadline = None if smoke_seconds is None else time.monotonic() + smoke_seconds
        fps = self._config.display.fps
        clock = self._clock
        assert clock is not None

        try:
            while self._running:
                now_ms = pygame.time.get_ticks()
                for event in pygame.event.get():
                    if not self.handle_event(event, now_ms):
                        self._running = False
                        break

                if not self._running:
                    break

                self.render_frame()
                self.present()

                if deadline is not None and time.monotonic() >= deadline:
                    logger.info("smoke run complete: %d frames rendered", self.frames_rendered)
                    break

                clock.tick(fps)
        except KeyboardInterrupt:
            logger.info("interrupted; shutting down")
        finally:
            self.teardown()
        return 0
