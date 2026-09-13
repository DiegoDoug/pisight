"""The persistent chrome: the 24px status bar and the 40px navigation bar.

Both are drawn every frame regardless of which screen is active, so the operator always has
capture state, channel, time, temperature and data freshness in the same place.
"""

from __future__ import annotations

import time

import pygame

from ..screens.base import RenderContext
from . import theme
from .layout import NAV_BUTTON_COUNT, STATUS_RECT, Rect, nav_button_rect
from .navigation import ScreenId
from .widgets import draw_record_indicator, draw_text

__all__ = ["draw_navigation_bar", "draw_status_bar"]


def draw_status_bar(surface: pygame.Surface, ctx: RenderContext) -> None:
    """Draw the top status bar.

    Left to right: product name, capture-state dot with its ``REC``/``IDLE`` label, the
    current channel or hop indicator, then clock and CPU temperature on the right. A
    stale or offline condition replaces the channel with a prominent banner, because at
    that point the channel is no longer trustworthy information.
    """
    fonts = ctx.fonts
    rect = STATUS_RECT
    pygame.draw.rect(surface, theme.STATUS_BACKGROUND, rect.as_tuple())
    pygame.draw.line(surface, theme.DIVIDER, (0, rect.bottom - 1), (rect.right, rect.bottom - 1))

    snapshot = ctx.snapshot
    capture = snapshot.capture if snapshot is not None else None
    capturing = bool(
        snapshot
        and capture
        and capture.kismet_online
        and any(source.running for source in snapshot.datasources)
        and not ctx.is_offline
        and not ctx.is_stale
    )

    text_y = (rect.height - fonts.small.get_height()) // 2

    draw_text(surface, fonts, fonts.small, "PiSight", 4, text_y, theme.ACCENT, max_width=48)

    dot_x = 54
    draw_record_indicator(surface, dot_x, rect.center_y - 4, active=capturing, size=8)
    state_label = "REC" if capturing else "IDLE"
    draw_text(
        surface,
        fonts,
        fonts.tiny,
        state_label,
        dot_x + 10,
        (rect.height - fonts.tiny.get_height()) // 2,
        theme.DANGER if capturing else theme.TEXT_MUTED,
        max_width=26,
    )

    # --- Middle: channel, or a freshness banner when data cannot be trusted ---------
    middle_x = 96
    middle_width = 118
    if ctx.is_offline:
        draw_text(
            surface,
            fonts,
            fonts.small,
            "OFFLINE",
            middle_x,
            text_y,
            theme.DANGER,
            max_width=middle_width,
        )
    elif ctx.is_stale:
        age = ctx.snapshot_age or 0.0
        draw_text(
            surface,
            fonts,
            fonts.small,
            f"STALE {int(age)}s",
            middle_x,
            text_y,
            theme.WARN,
            max_width=middle_width,
        )
    elif capture is not None:
        draw_text(
            surface,
            fonts,
            fonts.small,
            f"CH {capture.channel_label}",
            middle_x,
            text_y,
            theme.WARN if capture.hopping else theme.ACCENT,
            max_width=middle_width,
        )
    else:
        draw_text(
            surface,
            fonts,
            fonts.small,
            "CONNECTING",
            middle_x,
            text_y,
            theme.TEXT_MUTED,
            max_width=middle_width,
        )

    # --- Right: CPU temperature then clock -----------------------------------------
    temp_c = snapshot.host.cpu_temp_c if snapshot is not None else None
    clock_text = time.strftime("%H:%M:%S", time.localtime(ctx.now_wall))
    clock_width = fonts.measure(fonts.small, "00:00:00") + 2
    draw_text(
        surface,
        fonts,
        fonts.small,
        clock_text,
        rect.right - 4,
        text_y,
        theme.TEXT,
        max_width=clock_width,
        align="right",
    )

    if temp_c is not None:
        temp_color = (
            theme.DANGER if temp_c >= 80 else (theme.WARN if temp_c >= 70 else theme.TEXT_MUTED)
        )
        draw_text(
            surface,
            fonts,
            fonts.small,
            f"{temp_c:.0f}°C",
            rect.right - clock_width - 8,
            text_y,
            temp_color,
            max_width=34,
            align="right",
        )


def draw_navigation_bar(surface: pygame.Surface, ctx: RenderContext, active: ScreenId) -> None:
    """Draw the four navigation buttons.

    Each button is 80x40, comfortably above the 40x40 minimum touch target, and the active
    one is filled rather than merely tinted so the selection is obvious without relying on
    hue discrimination.
    """
    fonts = ctx.fonts
    for index in range(NAV_BUTTON_COUNT):
        screen = ScreenId(index)
        button = nav_button_rect(index)
        is_active = screen is active

        pygame.draw.rect(
            surface,
            theme.NAV_ACTIVE if is_active else theme.NAV_BACKGROUND,
            button.as_tuple(),
        )
        if not is_active:
            pygame.draw.line(surface, theme.DIVIDER, (button.x, button.y), (button.right, button.y))
        if index > 0:
            pygame.draw.line(
                surface, theme.DIVIDER, (button.x, button.y + 1), (button.x, button.bottom)
            )

        color = theme.NAV_ACTIVE_TEXT if is_active else theme.NAV_INACTIVE_TEXT
        draw_text(
            surface,
            fonts,
            fonts.body,
            screen.label,
            button.center_x,
            button.y + (button.height - fonts.body.get_height()) // 2 - 3,
            color,
            max_width=button.width - 4,
            align="center",
        )
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            str(index + 1),
            button.center_x,
            button.bottom - fonts.tiny.get_height() - 2,
            color if is_active else theme.DIVIDER,
            max_width=button.width - 4,
            align="center",
        )


def content_rect_for(_screen: ScreenId) -> Rect:
    """Content rectangle every screen draws into. Constant, but named for readability."""
    from .layout import CONTENT_RECT

    return CONTENT_RECT
