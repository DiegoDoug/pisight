"""Screen 1: Overview -- the at-a-glance state of the observed environment.

Layout inside the 320x176 content band::

    +----------------------------------------------------------+
    | APs    | CLIENTS | DEVICES | NEW     |   metric tiles     |
    +----------------------------------------------------------+
    | PKT/S  | 2.4 GHz | 5 GHz   | ALERTS  |   metric tiles     |
    +----------------------------------------------------------+
    | CHANNEL ACTIVITY   [bars]                                 |
    +----------------------------------------------------------+
    | LATEST: <newest significant observation>                  |
    +----------------------------------------------------------+
"""

from __future__ import annotations

import pygame

from ..models import Band, DashboardSnapshot, DeviceSummary
from ..sanitize import mask_mac
from ..ui import theme
from ..ui.layout import PADDING, Rect, split_columns
from ..ui.widgets import draw_bar, draw_metric, draw_panel, draw_text
from .base import RenderContext
from .common import band_totals, draw_empty_state, draw_section_header, format_count, format_rate

__all__ = ["OverviewScreen"]

#: Channels shown in the compact activity strip, chosen to span both bands.
_STRIP_CHANNELS: tuple[str, ...] = ("1", "6", "11", "36", "44", "149", "157", "161")


class OverviewScreen:
    """Renders the Overview screen."""

    title = "Overview"

    def draw(self, surface: pygame.Surface, rect: Rect, ctx: RenderContext) -> None:
        """Render the overview into ``rect``."""
        snapshot = ctx.snapshot
        if snapshot is None:
            draw_empty_state(
                surface,
                ctx,
                rect,
                "Waiting for data",
                "No snapshot received yet",
            )
            return

        inner = rect.inset(PADDING)
        tile_height = 34
        gap = 3

        row_one = Rect(inner.x, inner.y, inner.width, tile_height)
        row_two = Rect(inner.x, row_one.bottom + gap, inner.width, tile_height)

        self._draw_counts(surface, ctx, snapshot, row_one)
        self._draw_rates(surface, ctx, snapshot, row_two)

        strip_y = row_two.bottom + gap + 1
        strip_height = 42
        self._draw_channel_strip(
            surface, ctx, snapshot, Rect(inner.x, strip_y, inner.width, strip_height)
        )

        latest_y = strip_y + strip_height + gap
        self._draw_latest(
            surface, ctx, snapshot, Rect(inner.x, latest_y, inner.width, inner.bottom - latest_y)
        )

    # ---------------------------------------------------------------------------------

    def _draw_counts(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """Top metric row: what is out there."""
        columns = split_columns(rect, 4, gap=3)
        draw_metric(
            surface,
            ctx.fonts,
            columns[0],
            "APs",
            format_count(snapshot.access_point_count),
            value_color=theme.ACCENT,
        )
        draw_metric(
            surface,
            ctx.fonts,
            columns[1],
            "Clients",
            format_count(snapshot.client_count),
            value_color=theme.TEXT,
        )
        draw_metric(
            surface,
            ctx.fonts,
            columns[2],
            "Recent",
            format_count(len(snapshot.devices)),
            value_color=theme.TEXT,
        )
        draw_metric(
            surface,
            ctx.fonts,
            columns[3],
            "New",
            format_count(snapshot.new_device_count),
            value_color=theme.ACCENT if snapshot.new_device_count else theme.TEXT_MUTED,
        )

    def _draw_rates(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """Second metric row: how busy it is, and whether anything is wrong."""
        columns = split_columns(rect, 4, gap=3)
        two_ghz, five_ghz = band_totals(snapshot)
        alert_count = len(snapshot.alerts)
        highest = snapshot.highest_severity

        draw_metric(
            surface,
            ctx.fonts,
            columns[0],
            "Pkt/s",
            format_rate(snapshot.capture.packets_per_second),
            value_color=theme.ACCENT if snapshot.capture.kismet_online else theme.TEXT_MUTED,
        )
        draw_metric(surface, ctx.fonts, columns[1], "2.4 GHz", format_count(two_ghz))
        draw_metric(surface, ctx.fonts, columns[2], "5 GHz", format_count(five_ghz))
        draw_metric(
            surface,
            ctx.fonts,
            columns[3],
            "Alerts",
            format_count(alert_count),
            value_color=(
                theme.severity_color(highest.value)
                if highest is not None and alert_count
                else theme.TEXT_MUTED
            ),
        )

    def _draw_channel_strip(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """Compact channel-activity visualisation across a fixed channel selection.

        A fixed channel list keeps bar positions stable between frames; a list that
        reordered itself by activity would make the strip flicker and become unreadable at
        a glance.
        """
        draw_panel(surface, rect, color=theme.PANEL)
        inner = rect.inset(3)
        header_height = draw_section_header(surface, ctx, inner, "Channel activity")

        by_channel = {channel.channel: channel for channel in snapshot.channels}
        counts = [
            (name, by_channel[name].device_count if name in by_channel else 0)
            for name in _STRIP_CHANNELS
        ]
        peak = max((count for _, count in counts), default=0)

        bar_area = Rect(inner.x, inner.y + header_height, inner.width, inner.height - header_height)
        label_height = ctx.fonts.tiny.get_height()
        bar_height = max(3, bar_area.height - label_height - 1)
        slot_width = max(1, bar_area.width // len(counts))
        # Roughly half the slot: wide enough to read, narrow enough that the gap between
        # channels is obvious. A near-full-slot bar reads as a solid block, not a chart.
        bar_width = max(3, slot_width // 2)

        for index, (name, count) in enumerate(counts):
            x = bar_area.x + index * slot_width
            fraction = (count / peak) if peak > 0 else 0.0
            filled = max(1, int(bar_height * fraction)) if count else 1
            color = theme.BAR_2GHZ if name in ("1", "6", "11") else theme.BAR_5GHZ
            if count == 0:
                color = theme.BAR_EMPTY
            draw_bar(
                surface,
                Rect(x, bar_area.y + (bar_height - filled), bar_width, filled),
                1.0,
                color=color,
            )
            draw_text(
                surface,
                ctx.fonts,
                ctx.fonts.tiny,
                name,
                x + bar_width // 2,
                bar_area.y + bar_height + 1,
                theme.TEXT_MUTED,
                max_width=slot_width,
                align="center",
            )

    def _draw_latest(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """Newest significant observation: the most severe recent alert, else the newest device."""
        draw_panel(surface, rect, color=theme.PANEL_ALT)
        inner = rect.inset(3)

        headline, detail, color = self._latest_observation(snapshot, ctx.show_full_mac)
        draw_text(
            surface,
            ctx.fonts,
            ctx.fonts.tiny,
            headline,
            inner.x,
            inner.y,
            color,
            max_width=inner.width,
        )
        draw_text(
            surface,
            ctx.fonts,
            ctx.fonts.small,
            detail,
            inner.x,
            inner.y + ctx.fonts.tiny.get_height(),
            theme.TEXT,
            max_width=inner.width,
        )

    @staticmethod
    def _latest_observation(
        snapshot: DashboardSnapshot, show_full_mac: bool
    ) -> tuple[str, str, theme.RGB]:
        """Choose what deserves the single 'latest' line.

        An elevated alert always outranks a device arrival: if something is actively wrong,
        that is what the operator needs to see on the first screen.
        """
        elevated = [alert for alert in snapshot.alerts if alert.severity.is_elevated]
        if elevated:
            alert = max(elevated, key=lambda a: (a.severity.rank, a.timestamp))
            return (
                f"LATEST · {alert.severity.value}",
                alert.text or alert.header,
                theme.severity_color(alert.severity.value),
            )

        new_devices = [device for device in snapshot.devices if device.is_new]
        if new_devices:
            device = max(new_devices, key=lambda d: d.last_seen)
            return ("LATEST · NEW DEVICE", _device_line(device, show_full_mac), theme.ACCENT)

        if snapshot.alerts:
            alert = snapshot.alerts[0]
            return (
                f"LATEST · {alert.severity.value}",
                alert.text or alert.header,
                theme.severity_color(alert.severity.value),
            )

        if snapshot.devices:
            device = snapshot.devices[0]
            return ("LATEST · ACTIVE", _device_line(device, show_full_mac), theme.TEXT_MUTED)

        return ("LATEST", "No observations yet", theme.TEXT_MUTED)


def _device_line(device: DeviceSummary, show_full_mac: bool) -> str:
    """One-line device description used by the 'latest' panel."""
    name = device.display_name or mask_mac(device.mac, show_full=show_full_mac)
    band = device.band.short_label if device.band is not Band.UNKNOWN else ""
    channel = f"ch{device.channel}" if device.channel else ""
    parts = [part for part in (name, device.device_type.short_label, channel, band) if part]
    return " · ".join(parts)
