"""Reusable drawing primitives shared by the four screens.

All icons are drawn with Pygame primitives -- rectangles, circles, polygons and lines. There
are no image files and nothing is fetched from the network, so the package works offline and
its footprint stays small on the Pi.

Every text helper clips or ellipsizes to the rectangle it is given. Nothing in PiSight
writes text without a width bound, which is what keeps long SSIDs from overflowing the
320-pixel canvas.
"""

from __future__ import annotations

import pygame

from . import theme
from .fonts import FontSet
from .layout import Rect

__all__ = [
    "draw_alert_icon",
    "draw_bar",
    "draw_kv",
    "draw_metric",
    "draw_new_badge",
    "draw_panel",
    "draw_record_indicator",
    "draw_signal_bars",
    "draw_text",
    "draw_wifi_icon",
]


def draw_text(
    surface: pygame.Surface,
    fonts: FontSet,
    font: pygame.font.Font,
    text: str,
    x: int,
    y: int,
    color: theme.RGB = theme.TEXT,
    *,
    max_width: int | None = None,
    align: str = "left",
) -> pygame.Rect:
    """Draw ellipsized text and return the rectangle it occupied.

    ``align`` accepts ``"left"``, ``"center"`` or ``"right"``; ``x`` is interpreted as the
    corresponding edge or centre.
    """
    if max_width is not None:
        text = fonts.ellipsize(font, text, max_width)
    if not text:
        return pygame.Rect(x, y, 0, 0)

    rendered = font.render(text, True, color)
    rect = rendered.get_rect()
    if align == "center":
        rect.midtop = (x, y)
    elif align == "right":
        rect.topright = (x, y)
    else:
        rect.topleft = (x, y)
    surface.blit(rendered, rect)
    return rect


def draw_panel(
    surface: pygame.Surface,
    rect: Rect,
    *,
    color: theme.RGB = theme.PANEL,
    border: theme.RGB | None = None,
) -> None:
    """Fill a flat panel, optionally with a one-pixel border."""
    pygame.draw.rect(surface, color, rect.as_tuple())
    if border is not None:
        pygame.draw.rect(surface, border, rect.as_tuple(), width=1)


def draw_metric(
    surface: pygame.Surface,
    fonts: FontSet,
    rect: Rect,
    label: str,
    value: str,
    *,
    value_color: theme.RGB = theme.TEXT,
    label_color: theme.RGB = theme.TEXT_MUTED,
) -> None:
    """Draw a label-over-value metric tile, centred in ``rect``.

    The value uses the large face and the label the tiny one, so the number reads at arm's
    length while the label stays available on closer inspection.
    """
    draw_panel(surface, rect, color=theme.PANEL)
    inner = rect.inset(2)
    draw_text(
        surface,
        fonts,
        fonts.tiny,
        label.upper(),
        inner.center_x,
        inner.y + 1,
        label_color,
        max_width=inner.width,
        align="center",
    )
    value_font = fonts.large if len(value) <= 5 else fonts.body
    value_height = value_font.get_height()
    draw_text(
        surface,
        fonts,
        value_font,
        value,
        inner.center_x,
        inner.bottom - value_height - 1,
        value_color,
        max_width=inner.width,
        align="center",
    )


def draw_kv(
    surface: pygame.Surface,
    fonts: FontSet,
    rect: Rect,
    key: str,
    value: str,
    *,
    value_color: theme.RGB = theme.TEXT,
) -> None:
    """Draw a single left-key / right-value row inside ``rect``."""
    key_width = min(rect.width // 2, fonts.measure(fonts.small, key) + 4)
    draw_text(
        surface, fonts, fonts.small, key, rect.x, rect.y, theme.TEXT_MUTED, max_width=key_width
    )
    draw_text(
        surface,
        fonts,
        fonts.small,
        value,
        rect.right,
        rect.y,
        value_color,
        max_width=max(0, rect.width - key_width - 4),
        align="right",
    )


def draw_bar(
    surface: pygame.Surface,
    rect: Rect,
    fraction: float,
    *,
    color: theme.RGB = theme.ACCENT,
    background: theme.RGB = theme.BAR_EMPTY,
) -> None:
    """Draw a horizontal proportional bar. ``fraction`` is clamped to ``[0, 1]``.

    A non-zero fraction always paints at least one pixel, so a channel with a little
    traffic is visibly different from one with none.
    """
    pygame.draw.rect(surface, background, rect.as_tuple())
    clamped = max(0.0, min(1.0, fraction))
    if clamped <= 0.0:
        return
    width = max(1, round(rect.width * clamped))
    pygame.draw.rect(surface, color, (rect.x, rect.y, width, rect.height))


def draw_signal_bars(
    surface: pygame.Surface,
    rect: Rect,
    dbm: int | None,
    *,
    bars: int = 4,
) -> None:
    """Draw a stepped signal-strength glyph next to the numeric dBm value.

    The glyph is redundant with the printed number by design: it gives an at-a-glance read
    without making the number the only source of truth.
    """
    if rect.width <= 0 or rect.height <= 0:
        return
    filled = 0
    if dbm is not None:
        thresholds = (-85, -75, -65, -55)
        filled = sum(1 for threshold in thresholds if dbm >= threshold)

    color = theme.signal_color(dbm)
    bar_width = max(1, (rect.width - (bars - 1)) // bars)
    for index in range(bars):
        height = max(1, int(rect.height * (index + 1) / bars))
        x = rect.x + index * (bar_width + 1)
        y = rect.bottom - height
        fill = color if index < filled else theme.BAR_EMPTY
        pygame.draw.rect(surface, fill, (x, y, bar_width, height))


def draw_wifi_icon(
    surface: pygame.Surface, x: int, y: int, color: theme.RGB, *, size: int = 10
) -> None:
    """Draw a small concentric-arc Wi-Fi glyph anchored at its bottom-left corner."""
    center = (x + size // 2, y + size - 1)
    pygame.draw.circle(surface, color, center, 1)
    for radius in (size // 2 - 1, size - 2):
        if radius <= 1:
            continue
        rect = pygame.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
        pygame.draw.arc(surface, color, rect, 0.79, 2.36, 1)


def draw_record_indicator(
    surface: pygame.Surface, x: int, y: int, *, active: bool, size: int = 8
) -> None:
    """Draw the capture-state dot: filled when capturing, hollow when not.

    The Overview and status bar always print ``REC`` or ``IDLE`` alongside it, so the shape
    difference reinforces the label rather than replacing it.
    """
    center = (x + size // 2, y + size // 2)
    radius = size // 2
    if active:
        pygame.draw.circle(surface, theme.DANGER, center, radius)
    else:
        pygame.draw.circle(surface, theme.TEXT_MUTED, center, radius, 1)


def draw_alert_icon(
    surface: pygame.Surface, x: int, y: int, color: theme.RGB, *, size: int = 9
) -> None:
    """Draw a warning triangle with an exclamation notch."""
    points = [(x + size // 2, y), (x + size, y + size), (x, y + size)]
    pygame.draw.polygon(surface, color, points)
    pygame.draw.line(
        surface, theme.TEXT_INVERSE, (x + size // 2, y + 3), (x + size // 2, y + size - 3), 1
    )


def draw_new_badge(surface: pygame.Surface, fonts: FontSet, x: int, y: int) -> int:
    """Draw the ``NEW`` badge and return its width, so callers can lay out around it."""
    label = "NEW"
    width = fonts.measure(fonts.tiny, label) + 4
    height = fonts.tiny.get_height()
    pygame.draw.rect(surface, theme.ACCENT, (x, y, width, height))
    rendered = fonts.tiny.render(label, True, theme.TEXT_INVERSE)
    surface.blit(rendered, rendered.get_rect(center=(x + width // 2, y + height // 2)))
    return width
