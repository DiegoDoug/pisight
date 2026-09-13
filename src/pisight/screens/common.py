"""Helpers shared by more than one screen: formatting and empty states."""

from __future__ import annotations

import time

import pygame

from ..models import Band, DashboardSnapshot
from ..ui import theme
from ..ui.layout import Rect
from ..ui.widgets import draw_text
from .base import RenderContext

__all__ = [
    "draw_empty_state",
    "draw_section_header",
    "format_age",
    "format_bytes",
    "format_count",
    "format_duration",
    "format_rate",
    "relative_time",
]


def format_count(value: int) -> str:
    """Abbreviate large counts so they fit a metric tile (``1234`` -> ``1.2k``)."""
    if value < 0:
        return "0"
    if value < 1000:
        return str(value)
    if value < 100_000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    if value < 1_000_000:
        return f"{value // 1000}k"
    return f"{value / 1_000_000:.1f}M".replace(".0M", "M")


def format_rate(packets_per_second: float) -> str:
    """Format a packet rate for the Overview tile."""
    if packets_per_second <= 0:
        return "0"
    if packets_per_second < 10:
        return f"{packets_per_second:.1f}"
    return format_count(round(packets_per_second))


def format_bytes(value: int | None) -> str:
    """Format a byte count in binary units, or ``--`` when unavailable."""
    if value is None:
        return "--"
    size = float(value)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def format_duration(seconds: float) -> str:
    """Format an elapsed duration compactly (``2h14m``, ``45s``)."""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    hours, remainder = divmod(total, 3600)
    if hours < 24:
        return f"{hours}h{remainder // 60:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


def format_age(seconds: float | None) -> str:
    """Format a snapshot age, or ``--`` before the first snapshot arrives."""
    if seconds is None:
        return "--"
    if seconds < 1:
        return "<1s"
    return format_duration(seconds)


def relative_time(timestamp: float, *, now: float | None = None) -> str:
    """Render a Unix timestamp as an age relative to ``now``."""
    if timestamp <= 0:
        return "--"
    reference = time.time() if now is None else now
    delta = reference - timestamp
    if delta < 0:
        return "now"
    return format_duration(delta)


def band_totals(snapshot: DashboardSnapshot) -> tuple[int, int]:
    """Return ``(count_2ghz, count_5ghz)`` for the snapshot's device window.

    6 GHz observations fold into the 5 GHz figure: the PAU0B cannot tune 6 GHz, so a
    separate tile would always read zero and waste scarce canvas space.
    """
    two = snapshot.band_count(Band.BAND_2GHZ)
    five = snapshot.band_count(Band.BAND_5GHZ) + snapshot.band_count(Band.BAND_6GHZ)
    return two, five


def draw_section_header(
    surface: pygame.Surface,
    ctx: RenderContext,
    rect: Rect,
    text: str,
    *,
    color: theme.RGB = theme.TEXT_MUTED,
) -> int:
    """Draw a small uppercase section header and return its height."""
    draw_text(
        surface,
        ctx.fonts,
        ctx.fonts.tiny,
        text.upper(),
        rect.x,
        rect.y,
        color,
        max_width=rect.width,
    )
    return ctx.fonts.tiny.get_height() + 1


def draw_empty_state(
    surface: pygame.Surface,
    ctx: RenderContext,
    rect: Rect,
    headline: str,
    detail: str = "",
) -> None:
    """Draw a centred empty/placeholder state.

    Used for "no data yet", "Kismet offline" and "nothing observed": an explicit message is
    far better than a blank panel, which is indistinguishable from a crashed renderer.
    """
    fonts = ctx.fonts
    center_y = rect.center_y - fonts.body.get_height()
    draw_text(
        surface,
        fonts,
        fonts.body,
        headline,
        rect.center_x,
        center_y,
        theme.TEXT_MUTED,
        max_width=rect.width - 8,
        align="center",
    )
    if detail:
        draw_text(
            surface,
            fonts,
            fonts.small,
            detail,
            rect.center_x,
            center_y + fonts.body.get_height() + 2,
            theme.TEXT_MUTED,
            max_width=rect.width - 8,
            align="center",
        )
