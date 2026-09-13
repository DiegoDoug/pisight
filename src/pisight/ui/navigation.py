"""Screen identity, navigation state and pointer input handling.

Deliberately free of pygame imports except for the event constants, so navigation and
debounce logic can be unit-tested without a video driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .layout import nav_button_index_at

__all__ = ["InputRouter", "Navigator", "ScreenId"]


class ScreenId(IntEnum):
    """The four screens, in navigation-bar order."""

    OVERVIEW = 0
    CHANNELS = 1
    DEVICES = 2
    ALERTS = 3

    @property
    def label(self) -> str:
        """Short label shown on the navigation button."""
        return {
            ScreenId.OVERVIEW: "OVER",
            ScreenId.CHANNELS: "CHAN",
            ScreenId.DEVICES: "DEV",
            ScreenId.ALERTS: "SYS",
        }[self]

    @property
    def title(self) -> str:
        """Full title shown in the status bar area of the screen."""
        return {
            ScreenId.OVERVIEW: "Overview",
            ScreenId.CHANNELS: "Channels",
            ScreenId.DEVICES: "Devices",
            ScreenId.ALERTS: "Alerts / System",
        }[self]


class Navigator:
    """Tracks which screen is active and how the operator moves between them.

    Left/Right wrap around: with only four screens, wrapping is faster than hitting a stop,
    and there is no scroll position to lose.
    """

    def __init__(self, initial: ScreenId = ScreenId.OVERVIEW) -> None:
        self._current = initial

    @property
    def current(self) -> ScreenId:
        """The active screen."""
        return self._current

    def select(self, screen: ScreenId) -> bool:
        """Jump to ``screen``; returns True when the selection changed anything."""
        if screen is self._current:
            return False
        self._current = screen
        return True

    def select_index(self, index: int) -> bool:
        """Select by navigation-bar index, ignoring out-of-range values."""
        if not 0 <= index < len(ScreenId):
            return False
        return self.select(ScreenId(index))

    def next(self) -> bool:
        """Advance one screen to the right, wrapping at the end."""
        return self.select(ScreenId((int(self._current) + 1) % len(ScreenId)))

    def previous(self) -> bool:
        """Move one screen to the left, wrapping at the start."""
        return self.select(ScreenId((int(self._current) - 1) % len(ScreenId)))


@dataclass(frozen=True, slots=True)
class PointerEvent:
    """A press at a logical canvas coordinate, from a mouse or a touchscreen."""

    x: int
    y: int
    source: str = "mouse"


class InputRouter:
    """Applies touch debounce and routes pointer presses to navigation.

    Two problems this solves:

    * A resistive XPT2046 panel bounces. One physical press can produce several SDL events
      within a few milliseconds, which without debounce would skip two screens instead of
      one.
    * SDL synthesises a mouse event for every touch event. Handling both would double every
      tap, so presses inside ``debounce_ms`` of the last accepted one are dropped
      regardless of which source they came from.
    """

    def __init__(self, navigator: Navigator, *, debounce_ms: int = 220) -> None:
        if debounce_ms < 0:
            raise ValueError("debounce_ms must not be negative")
        self._navigator = navigator
        self._debounce_ms = debounce_ms
        self._last_accept_ms: float | None = None

    @property
    def debounce_ms(self) -> int:
        """Minimum milliseconds between two accepted presses."""
        return self._debounce_ms

    def accepts(self, now_ms: float) -> bool:
        """True when a press at ``now_ms`` is outside the debounce window."""
        if self._last_accept_ms is None:
            return True
        return (now_ms - self._last_accept_ms) >= self._debounce_ms

    def handle_pointer(self, event: PointerEvent, now_ms: float) -> bool:
        """Process one press. Returns True when it changed the active screen.

        A press inside the debounce window is swallowed entirely -- it neither navigates
        nor resets the timer, so holding a finger down does not extend the lockout.
        """
        if not self.accepts(now_ms):
            return False
        self._last_accept_ms = now_ms

        index = nav_button_index_at(event.x, event.y)
        if index is None:
            return False
        return self._navigator.select_index(index)

    def reset(self) -> None:
        """Clear the debounce timer (used between tests and after a mode change)."""
        self._last_accept_ms = None
