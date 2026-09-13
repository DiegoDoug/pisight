"""Shared rendering context and the screen interface.

A screen receives a :class:`RenderContext` and draws into the content band. It never
touches the network, never reads the filesystem and never mutates state: given the same
context it produces the same pixels, which is what makes the layout tests meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pygame

from ..config import AppConfig
from ..models import DashboardSnapshot
from ..polling.store import SnapshotState
from ..ui.fonts import FontSet
from ..ui.layout import Rect

__all__ = ["RenderContext", "Screen"]


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Everything a screen needs for one frame."""

    snapshot: DashboardSnapshot | None
    state: SnapshotState
    fonts: FontSet
    config: AppConfig
    #: Seconds since the PiSight process started.
    uptime_seconds: float
    #: Snapshot age in seconds, or ``None`` when no snapshot has arrived yet.
    snapshot_age: float | None
    #: True once the snapshot is older than the configured staleness threshold.
    is_stale: bool
    #: True when the data source is known to be unreachable.
    is_offline: bool
    #: Wall-clock time used for the status bar, injectable for deterministic screenshots.
    now_wall: float

    @property
    def has_data(self) -> bool:
        """True when there is a snapshot to draw."""
        return self.snapshot is not None

    @property
    def show_full_mac(self) -> bool:
        """Whether the operator opted into full MAC display."""
        return self.config.privacy.show_full_mac


class Screen(Protocol):
    """A drawable dashboard screen."""

    title: str

    def draw(self, surface: pygame.Surface, rect: Rect, ctx: RenderContext) -> None:
        """Render this screen into ``rect`` on ``surface``."""
        ...
