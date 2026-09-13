"""Screen 3: Devices -- the five most recently active observations.

Five rows is a deliberate limit, not a pagination stub: at 176 logical pixels a sixth row
would force type below the size that stays legible at arm's length. Devices are sorted most
recently active first, so the five that matter are the five on screen.

MAC addresses are masked to their last four hexadecimal characters unless the operator has
explicitly opted into full display. That keeps a photograph of the screen, or a committed
screenshot, from identifying anyone's hardware.
"""

from __future__ import annotations

import pygame

from ..models import Band, DeviceSummary
from ..sanitize import mask_mac
from ..ui import theme
from ..ui.layout import PADDING, Rect
from ..ui.widgets import draw_new_badge, draw_panel, draw_signal_bars, draw_text
from .base import RenderContext
from .common import draw_empty_state, relative_time

__all__ = ["MAX_ROWS", "DevicesScreen"]

#: Hard cap on visible device rows.
MAX_ROWS = 5


class DevicesScreen:
    """Renders the Devices screen."""

    title = "Devices"

    def draw(self, surface: pygame.Surface, rect: Rect, ctx: RenderContext) -> None:
        """Render up to five device rows, or an explicit empty state."""
        snapshot = ctx.snapshot
        if snapshot is None:
            draw_empty_state(surface, ctx, rect, "Waiting for data", "No snapshot received yet")
            return
        if not snapshot.devices:
            draw_empty_state(
                surface,
                ctx,
                rect,
                "No devices observed",
                "Nothing seen in the recent window",
            )
            return

        inner = rect.inset(PADDING)
        fonts = ctx.fonts

        header_height = fonts.tiny.get_height() + 1
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            "RECENT DEVICES",
            inner.x,
            inner.y,
            theme.TEXT_MUTED,
            max_width=inner.width // 2,
        )
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            f"{len(snapshot.devices)} in window",
            inner.right,
            inner.y,
            theme.TEXT_MUTED,
            max_width=inner.width // 2,
            align="right",
        )

        rows = snapshot.devices[:MAX_ROWS]
        body = Rect(inner.x, inner.y + header_height, inner.width, inner.height - header_height)
        row_height = body.height // MAX_ROWS

        now = ctx.now_wall
        for index, device in enumerate(rows):
            row_rect = Rect(body.x, body.y + index * row_height, body.width, row_height - 1)
            self._draw_row(surface, ctx, row_rect, device, now)

    # ---------------------------------------------------------------------------------

    def _draw_row(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        rect: Rect,
        device: DeviceSummary,
        now: float,
    ) -> None:
        """Draw one device row: name/MAC and badge on top, attributes underneath."""
        fonts = ctx.fonts
        draw_panel(surface, rect, color=theme.PANEL)
        inner = rect.inset(2)

        # --- Top line: identity, new badge, signal ---------------------------------
        signal_width = 14
        signal_text = device.signal_label
        signal_text_width = fonts.measure(fonts.small, "-100dBm") + 2

        badge_width = 0
        if device.is_new:
            badge_width = draw_new_badge(surface, fonts, inner.x, inner.y) + 3

        name_x = inner.x + badge_width
        name_width = max(10, inner.width - badge_width - signal_width - signal_text_width - 6)
        display = device.display_name or mask_mac(device.mac, show_full=ctx.show_full_mac)
        draw_text(
            surface,
            fonts,
            fonts.small,
            display,
            name_x,
            inner.y,
            theme.TEXT,
            max_width=name_width,
        )

        draw_text(
            surface,
            fonts,
            fonts.small,
            signal_text,
            inner.right - signal_width - 2,
            inner.y,
            theme.signal_color(device.signal_dbm),
            max_width=signal_text_width,
            align="right",
        )
        glyph_rect = Rect(
            inner.right - signal_width,
            inner.y + 1,
            signal_width,
            fonts.small.get_height() - 2,
        )
        draw_signal_bars(surface, glyph_rect, device.signal_dbm)

        # --- Bottom line: type, MAC, channel, band, security, last seen -------------
        detail_y = inner.y + fonts.small.get_height()
        band = device.band.short_label if device.band is not Band.UNKNOWN else "?"
        channel = f"ch{device.channel}" if device.channel else "ch--"
        security = device.crypt or ("open" if device.device_type.name == "ACCESS_POINT" else "--")

        left_parts = [
            device.device_type.short_label,
            mask_mac(device.mac, show_full=ctx.show_full_mac),
            channel,
            band,
            security,
        ]
        seen_text = relative_time(device.last_seen, now=now)
        seen_width = fonts.measure(fonts.tiny, "9999d99h") + 2

        draw_text(
            surface,
            fonts,
            fonts.tiny,
            " · ".join(left_parts),
            inner.x,
            detail_y,
            theme.TEXT_MUTED,
            max_width=max(10, inner.width - seen_width - 4),
        )
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            seen_text,
            inner.right,
            detail_y,
            theme.TEXT_MUTED,
            max_width=seen_width,
            align="right",
        )
