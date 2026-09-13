"""Fixed geometry of the 320x240 logical canvas.

Every coordinate in PiSight is expressed against this canvas, whatever the physical window
size. The scaler in :mod:`pisight.ui.app` maps window pixels back to these coordinates, so
a click at the same logical point means the same thing on a 320x240 TFT and on a 960x720
development window.

The three bands are fixed by the product specification:

    +--------------------------------------+  y=0
    |  status bar                     24px |
    +--------------------------------------+  y=24
    |                                      |
    |  content                       176px |
    |                                      |
    +--------------------------------------+  y=200
    |  navigation                     40px |
    +--------------------------------------+  y=240
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "CONTENT_HEIGHT",
    "CONTENT_RECT",
    "CONTENT_Y",
    "MIN_TOUCH_SIZE",
    "NAV_BUTTON_COUNT",
    "NAV_BUTTON_WIDTH",
    "NAV_HEIGHT",
    "NAV_RECT",
    "NAV_Y",
    "PADDING",
    "STATUS_HEIGHT",
    "STATUS_RECT",
    "Rect",
    "nav_button_index_at",
    "nav_button_rect",
]

CANVAS_WIDTH: Final[int] = 320
CANVAS_HEIGHT: Final[int] = 240

STATUS_HEIGHT: Final[int] = 24
CONTENT_HEIGHT: Final[int] = 176
NAV_HEIGHT: Final[int] = 40

CONTENT_Y: Final[int] = STATUS_HEIGHT
NAV_Y: Final[int] = STATUS_HEIGHT + CONTENT_HEIGHT

#: Standard inner padding, in logical pixels.
PADDING: Final[int] = 4

#: Accessibility floor for any interactive element.
MIN_TOUCH_SIZE: Final[int] = 40

NAV_BUTTON_COUNT: Final[int] = 4
NAV_BUTTON_WIDTH: Final[int] = CANVAS_WIDTH // NAV_BUTTON_COUNT  # 80px, above the 40px floor


@dataclass(frozen=True, slots=True)
class Rect:
    """A plain integer rectangle, independent of pygame so layout is testable headlessly."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        """X coordinate just past the right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Y coordinate just past the bottom edge."""
        return self.y + self.height

    @property
    def center_x(self) -> int:
        """Horizontal centre."""
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        """Vertical centre."""
        return self.y + self.height // 2

    def contains(self, x: int, y: int) -> bool:
        """True when the point lies inside the rectangle."""
        return self.x <= x < self.right and self.y <= y < self.bottom

    def inset(self, amount: int) -> Rect:
        """Shrink the rectangle by ``amount`` on every side, never below zero size."""
        return Rect(
            self.x + amount,
            self.y + amount,
            max(0, self.width - 2 * amount),
            max(0, self.height - 2 * amount),
        )

    def as_tuple(self) -> tuple[int, int, int, int]:
        """Pygame-compatible ``(x, y, w, h)`` tuple."""
        return (self.x, self.y, self.width, self.height)


STATUS_RECT: Final[Rect] = Rect(0, 0, CANVAS_WIDTH, STATUS_HEIGHT)
CONTENT_RECT: Final[Rect] = Rect(0, CONTENT_Y, CANVAS_WIDTH, CONTENT_HEIGHT)
NAV_RECT: Final[Rect] = Rect(0, NAV_Y, CANVAS_WIDTH, NAV_HEIGHT)


def nav_button_rect(index: int) -> Rect:
    """Rectangle of navigation button ``index`` (0-3).

    The final button absorbs any rounding remainder so the four buttons exactly tile the
    canvas width with no dead pixel column between them.
    """
    if not 0 <= index < NAV_BUTTON_COUNT:
        raise IndexError(f"navigation button index out of range: {index}")
    x = index * NAV_BUTTON_WIDTH
    width = NAV_BUTTON_WIDTH if index < NAV_BUTTON_COUNT - 1 else CANVAS_WIDTH - x
    return Rect(x, NAV_Y, width, NAV_HEIGHT)


def nav_button_index_at(x: int, y: int) -> int | None:
    """Return the navigation button index at a logical point, or ``None``.

    Points outside the navigation band return ``None`` so content-area taps never change
    screens by accident.
    """
    if not NAV_RECT.contains(x, y):
        return None
    for index in range(NAV_BUTTON_COUNT):
        if nav_button_rect(index).contains(x, y):
            return index
    return None


def split_columns(rect: Rect, count: int, gap: int = PADDING) -> tuple[Rect, ...]:
    """Divide ``rect`` into ``count`` equal-width columns separated by ``gap``.

    The last column absorbs rounding so the columns exactly fill ``rect``.
    """
    if count <= 0:
        raise ValueError("count must be positive")
    total_gap = gap * (count - 1)
    column_width = max(0, (rect.width - total_gap) // count)
    columns: list[Rect] = []
    for index in range(count):
        x = rect.x + index * (column_width + gap)
        width = column_width if index < count - 1 else max(0, rect.right - x)
        columns.append(Rect(x, rect.y, width, rect.height))
    return tuple(columns)


def split_rows(rect: Rect, count: int, gap: int = PADDING) -> tuple[Rect, ...]:
    """Divide ``rect`` into ``count`` equal-height rows separated by ``gap``."""
    if count <= 0:
        raise ValueError("count must be positive")
    total_gap = gap * (count - 1)
    row_height = max(0, (rect.height - total_gap) // count)
    rows: list[Rect] = []
    for index in range(count):
        y = rect.y + index * (row_height + gap)
        height = row_height if index < count - 1 else max(0, rect.bottom - y)
        rows.append(Rect(rect.x, y, rect.width, height))
    return tuple(rows)
