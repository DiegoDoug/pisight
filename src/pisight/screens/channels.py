"""Screen 2: Channels -- per-channel activity, split by band.

Strictly read-only. PiSight displays what Kismet observed; it does not lock channels, set
hop rates or alter the datasource in any way. There is deliberately no control on this
screen that could change capture behaviour.

The 2.4 GHz and 5 GHz halves are laid out side by side so both bands are visible at once
without scrolling, which the specification rules out.
"""

from __future__ import annotations

import pygame

from ..models import Band, ChannelSummary, DashboardSnapshot
from ..ui import theme
from ..ui.layout import PADDING, Rect, split_columns
from ..ui.widgets import draw_bar, draw_panel, draw_text
from .base import RenderContext
from .common import draw_empty_state, format_count

__all__ = ["ChannelsScreen"]

#: Rows per band column. Five fits the 176px band with readable 11px type.
_ROWS_PER_BAND = 5


class ChannelsScreen:
    """Renders the Channels screen."""

    title = "Channels"

    def draw(self, surface: pygame.Surface, rect: Rect, ctx: RenderContext) -> None:
        """Render both band columns plus the capture-state footer."""
        snapshot = ctx.snapshot
        if snapshot is None:
            draw_empty_state(surface, ctx, rect, "Waiting for data", "No snapshot received yet")
            return

        if not snapshot.channels:
            draw_empty_state(
                surface,
                ctx,
                rect,
                "No channel activity",
                "Kismet has not reported channel data",
            )
            self._draw_footer(surface, ctx, snapshot, self._footer_rect(rect))
            return

        inner = rect.inset(PADDING)
        footer = self._footer_rect(rect)
        body = Rect(inner.x, inner.y, inner.width, footer.y - inner.y - 2)

        left, right = split_columns(body, 2, gap=PADDING)
        current = self._current_channel(snapshot)

        rows_2ghz = self._band_rows(snapshot, Band.BAND_2GHZ)
        rows_5ghz = self._band_rows(snapshot, Band.BAND_5GHZ)

        # One peak across both columns. Normalising each band against its own maximum would
        # draw a 1-device 5 GHz channel as long as a 4-device 2.4 GHz one, which reads as
        # equally busy at a glance and is exactly the wrong impression.
        peak = max((row.device_count for row in rows_2ghz + rows_5ghz), default=0)

        self._draw_band(surface, ctx, left, "2.4 GHz", rows_2ghz, theme.BAR_2GHZ, current, peak)
        self._draw_band(surface, ctx, right, "5 GHz", rows_5ghz, theme.BAR_5GHZ, current, peak)
        self._draw_footer(surface, ctx, snapshot, footer)

    # ---------------------------------------------------------------------------------

    @staticmethod
    def _footer_rect(rect: Rect) -> Rect:
        """Footer band holding the busiest-channel summary and capture state."""
        height = 26
        return Rect(rect.x + PADDING, rect.bottom - height - 2, rect.width - 2 * PADDING, height)

    @staticmethod
    def _current_channel(snapshot: DashboardSnapshot) -> str | None:
        """Channel the capture source is currently tuned to, if any."""
        for source in snapshot.datasources:
            if source.running and source.channel:
                return source.channel
        return snapshot.capture.current_channel

    @staticmethod
    def _band_rows(snapshot: DashboardSnapshot, band: Band) -> tuple[ChannelSummary, ...]:
        """Busiest channels in ``band``, newest counts first, bounded to the visible rows.

        6 GHz records fold into the 5 GHz column for the same reason the Overview tiles do:
        the PAU0B cannot observe 6 GHz, so any such record is an anomaly worth showing
        somewhere rather than hiding.
        """
        wanted = (Band.BAND_5GHZ, Band.BAND_6GHZ) if band is Band.BAND_5GHZ else (Band.BAND_2GHZ,)
        rows = [channel for channel in snapshot.channels if channel.band in wanted]
        rows.sort(key=lambda c: (-c.device_count, -c.packet_count, _channel_sort_key(c.channel)))
        return tuple(rows[:_ROWS_PER_BAND])

    def _draw_band(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        rect: Rect,
        title: str,
        rows: tuple[ChannelSummary, ...],
        color: theme.RGB,
        current_channel: str | None,
        peak: int,
    ) -> None:
        """Draw one band column: header, then a labelled bar per channel.

        ``peak`` is the busiest channel across *both* bands, so bar lengths are comparable
        between the two columns.
        """
        draw_panel(surface, rect, color=theme.PANEL)
        inner = rect.inset(3)
        fonts = ctx.fonts

        draw_text(
            surface,
            fonts,
            fonts.tiny,
            title.upper(),
            inner.x,
            inner.y,
            color,
            max_width=inner.width,
        )
        header_height = fonts.tiny.get_height() + 2

        if not rows:
            draw_text(
                surface,
                fonts,
                fonts.small,
                "none seen",
                inner.center_x,
                inner.y + header_height + 4,
                theme.TEXT_MUTED,
                max_width=inner.width,
                align="center",
            )
            return

        row_height = max(
            fonts.small.get_height() + 3, (inner.height - header_height) // _ROWS_PER_BAND
        )

        for index, row in enumerate(rows):
            y = inner.y + header_height + index * row_height
            if y + row_height > inner.bottom + 2:
                break
            is_current = current_channel is not None and row.channel == current_channel

            # Channel label, marked with a caret when the radio is tuned there right now.
            label = f">{row.channel}" if is_current else f" {row.channel}"
            label_color = theme.WARN if is_current else theme.TEXT
            label_width = fonts.measure(fonts.small, ">165") + 2
            draw_text(
                surface, fonts, fonts.small, label, inner.x, y, label_color, max_width=label_width
            )

            count_text = format_count(row.device_count)
            count_width = fonts.measure(fonts.small, "999") + 2
            bar_x = inner.x + label_width + 2
            bar_width = max(4, inner.width - label_width - count_width - 6)
            bar_y = y + (fonts.small.get_height() - 5) // 2
            fraction = (row.device_count / peak) if peak > 0 else 0.0
            draw_bar(
                surface,
                Rect(bar_x, bar_y, bar_width, 5),
                fraction,
                color=theme.WARN if is_current else color,
            )
            draw_text(
                surface,
                fonts,
                fonts.small,
                count_text,
                inner.right,
                y,
                theme.TEXT,
                max_width=count_width,
                align="right",
            )

    def _draw_footer(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """Footer: busiest observed channel, and where the radio is listening now."""
        draw_panel(surface, rect, color=theme.PANEL_ALT)
        inner = rect.inset(3)
        fonts = ctx.fonts

        busiest = max(
            snapshot.channels,
            key=lambda c: (c.device_count, c.packet_count),
            default=None,
        )
        busiest_text = f"ch{busiest.channel} ({busiest.device_count} dev)" if busiest else "none"
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            "BUSIEST",
            inner.x,
            inner.y,
            theme.TEXT_MUTED,
            max_width=inner.width // 2,
        )
        draw_text(
            surface,
            fonts,
            fonts.small,
            busiest_text,
            inner.x,
            inner.y + fonts.tiny.get_height(),
            theme.TEXT,
            max_width=inner.width // 2 - 4,
        )

        draw_text(
            surface,
            fonts,
            fonts.tiny,
            "LISTENING",
            inner.right,
            inner.y,
            theme.TEXT_MUTED,
            max_width=inner.width // 2,
            align="right",
        )
        draw_text(
            surface,
            fonts,
            fonts.small,
            snapshot.capture.channel_label,
            inner.right,
            inner.y + fonts.tiny.get_height(),
            theme.WARN if snapshot.capture.hopping else theme.ACCENT,
            max_width=inner.width // 2 - 4,
            align="right",
        )


def _channel_sort_key(channel: str) -> tuple[int, str]:
    """Sort channels numerically where possible, falling back to lexical order."""
    digits = ""
    for char in channel:
        if char.isdigit():
            digits += char
        else:
            break
    return (int(digits) if digits else 9999, channel)
