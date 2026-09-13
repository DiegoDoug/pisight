"""Screen 4: Alerts / System -- recent alerts over system and capture health.

The top half lists the most recent alerts with a severity label *and* an icon, so severity
is never carried by colour alone. The bottom half answers the questions an operator asks
when something looks wrong: is Kismet up, is the radio capturing, is the disk filling, how
hot is the Pi, how long has this been running and how old is what I am looking at.

Raw packet content is never displayed. Only Kismet's own alert description text reaches the
screen, and that has already been sanitized of control characters.
"""

from __future__ import annotations

import pygame

from ..models import AlertSummary, DashboardSnapshot
from ..sanitize import mask_mac
from ..ui import theme
from ..ui.layout import PADDING, Rect, split_columns
from ..ui.widgets import draw_alert_icon, draw_panel, draw_text
from .base import RenderContext
from .common import draw_empty_state, format_age, format_duration, relative_time

__all__ = ["MAX_ALERT_ROWS", "AlertsSystemScreen"]

#: Alert rows that fit above the system panel.
MAX_ALERT_ROWS = 3


class AlertsSystemScreen:
    """Renders the combined Alerts and System screen."""

    title = "Alerts / System"

    def draw(self, surface: pygame.Surface, rect: Rect, ctx: RenderContext) -> None:
        """Render the alert list and the system panel."""
        snapshot = ctx.snapshot
        if snapshot is None:
            draw_empty_state(surface, ctx, rect, "Waiting for data", "No snapshot received yet")
            return

        inner = rect.inset(PADDING)
        system_height = 74
        alerts_rect = Rect(inner.x, inner.y, inner.width, inner.height - system_height - 3)
        system_rect = Rect(inner.x, alerts_rect.bottom + 3, inner.width, system_height)

        self._draw_alerts(surface, ctx, snapshot, alerts_rect)
        self._draw_system(surface, ctx, snapshot, system_rect)

    # ---------------------------------------------------------------------------------

    def _draw_alerts(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """Alert list, most recent first."""
        fonts = ctx.fonts
        highest = snapshot.highest_severity
        header_color = (
            theme.severity_color(highest.value) if highest is not None else theme.TEXT_MUTED
        )
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            "RECENT ALERTS",
            rect.x,
            rect.y,
            header_color,
            max_width=rect.width // 2,
        )
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            f"{len(snapshot.alerts)} in window",
            rect.right,
            rect.y,
            theme.TEXT_MUTED,
            max_width=rect.width // 2,
            align="right",
        )

        header_height = fonts.tiny.get_height() + 1
        body = Rect(rect.x, rect.y + header_height, rect.width, rect.height - header_height)

        if not snapshot.alerts:
            draw_panel(surface, body, color=theme.PANEL)
            draw_text(
                surface,
                fonts,
                fonts.small,
                "No alerts in window",
                body.center_x,
                body.center_y - fonts.small.get_height() // 2,
                theme.TEXT_MUTED,
                max_width=body.width - 6,
                align="center",
            )
            return

        row_height = body.height // MAX_ALERT_ROWS
        for index, alert in enumerate(snapshot.alerts[:MAX_ALERT_ROWS]):
            row = Rect(body.x, body.y + index * row_height, body.width, row_height - 1)
            self._draw_alert_row(surface, ctx, row, alert)

    def _draw_alert_row(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        rect: Rect,
        alert: AlertSummary,
    ) -> None:
        """One alert: icon, severity word, age, then the description."""
        fonts = ctx.fonts
        draw_panel(surface, rect, color=theme.PANEL)
        inner = rect.inset(2)
        color = theme.severity_color(alert.severity.value)

        icon_size = 9
        draw_alert_icon(surface, inner.x, inner.y + 1, color, size=icon_size)

        severity_x = inner.x + icon_size + 3
        severity_width = fonts.measure(fonts.tiny, "CRITICAL") + 2
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            alert.severity.value,
            severity_x,
            inner.y,
            color,
            max_width=severity_width,
        )

        age_width = fonts.measure(fonts.tiny, "9999d99h") + 2
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            relative_time(alert.timestamp, now=ctx.now_wall),
            inner.right,
            inner.y,
            theme.TEXT_MUTED,
            max_width=age_width,
            align="right",
        )

        header_x = severity_x + severity_width + 3
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            alert.header,
            header_x,
            inner.y,
            theme.TEXT_MUTED,
            max_width=max(6, inner.right - header_x - age_width - 4),
        )

        detail = alert.text
        if alert.source_mac:
            detail = f"{mask_mac(alert.source_mac, show_full=ctx.show_full_mac)} {detail}"
        draw_text(
            surface,
            fonts,
            fonts.small,
            detail,
            inner.x,
            inner.y + fonts.tiny.get_height(),
            theme.TEXT,
            max_width=inner.width,
        )

    def _draw_system(
        self,
        surface: pygame.Surface,
        ctx: RenderContext,
        snapshot: DashboardSnapshot,
        rect: Rect,
    ) -> None:
        """System panel: source health on the left, host health on the right."""
        fonts = ctx.fonts
        draw_panel(surface, rect, color=theme.PANEL_ALT)
        inner = rect.inset(3)
        left, right = split_columns(inner, 2, gap=6)

        line_height = fonts.tiny.get_height() + 1

        # --- Left column: Kismet link and datasource -------------------------------
        kismet_online = snapshot.capture.kismet_online and not ctx.is_offline
        self._draw_row(
            surface,
            ctx,
            left,
            0,
            line_height,
            "KISMET",
            "ONLINE" if kismet_online else "OFFLINE",
            theme.ACCENT if kismet_online else theme.DANGER,
        )

        source = snapshot.datasources[0] if snapshot.datasources else None
        source_text = f"{source.interface} {source.state_label}" if source else "none"
        source_color = theme.TEXT_MUTED
        if source is not None:
            source_color = (
                theme.DANGER if source.error else (theme.ACCENT if source.running else theme.WARN)
            )
        self._draw_row(surface, ctx, left, 1, line_height, "SOURCE", source_text, source_color)

        self._draw_row(
            surface, ctx, left, 2, line_height, "MODE", ctx.config.mode.upper(), theme.TEXT_MUTED
        )

        warning = (
            snapshot.warnings[0] if snapshot.warnings else (snapshot.capture.last_error or "none")
        )
        self._draw_row(
            surface,
            ctx,
            left,
            3,
            line_height,
            "NOTE",
            warning,
            theme.WARN if (snapshot.warnings or snapshot.capture.last_error) else theme.TEXT_MUTED,
        )

        # --- Right column: host health ---------------------------------------------
        host = snapshot.host
        free_pct = host.disk_free_pct
        free_text = "--" if free_pct is None else f"{free_pct:.0f}% free"
        free_color = theme.WARN if host.storage_is_low else theme.TEXT
        self._draw_row(surface, ctx, right, 0, line_height, "STORAGE", free_text, free_color)

        temp_text = "--" if host.cpu_temp_c is None else f"{host.cpu_temp_c:.0f}°C"
        temp_color = theme.TEXT
        if host.cpu_temp_c is not None:
            temp_color = (
                theme.DANGER
                if host.cpu_temp_c >= 80
                else (theme.WARN if host.cpu_temp_c >= 70 else theme.TEXT)
            )
        self._draw_row(surface, ctx, right, 1, line_height, "TEMP", temp_text, temp_color)

        self._draw_row(
            surface,
            ctx,
            right,
            2,
            line_height,
            "UPTIME",
            format_duration(ctx.uptime_seconds),
            theme.TEXT,
        )

        age_color = theme.WARN if ctx.is_stale else theme.TEXT
        self._draw_row(
            surface,
            ctx,
            right,
            3,
            line_height,
            "SNAPSHOT",
            f"{format_age(ctx.snapshot_age)} old" + (" STALE" if ctx.is_stale else ""),
            age_color,
        )

    @staticmethod
    def _draw_row(
        surface: pygame.Surface,
        ctx: RenderContext,
        column: Rect,
        index: int,
        line_height: int,
        label: str,
        value: str,
        color: theme.RGB,
    ) -> None:
        """Draw one ``LABEL   value`` line inside a system column."""
        fonts = ctx.fonts
        y = column.y + index * line_height
        # Sized against the longest label this panel actually draws, so none is ellipsized.
        label_width = fonts.measure(fonts.tiny, "SNAPSHOT") + 3
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            label,
            column.x,
            y,
            theme.TEXT_MUTED,
            max_width=label_width,
        )
        draw_text(
            surface,
            fonts,
            fonts.tiny,
            value,
            column.right,
            y,
            color,
            max_width=max(6, column.width - label_width - 2),
            align="right",
        )
