"""Shared pytest fixtures.

The SDL dummy video driver is forced before pygame is imported anywhere, so the whole
suite -- including the rendering tests -- runs headlessly in CI and over SSH.

Pygame setup is per-test rather than per-session on purpose: the CLI tests run the real
application, which calls ``pygame.quit()`` on teardown. A session-scoped display (or font
set) would be left invalid for every test that ran afterwards.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from collections.abc import Iterator

import pygame
import pytest

from pisight.config import AppConfig
from pisight.models import DashboardSnapshot
from pisight.providers.mock import MockDashboardProvider, screenshot_snapshot
from pisight.ui.fonts import FontSet, load_fonts
from pisight.ui.layout import CANVAS_HEIGHT, CANVAS_WIDTH


@pytest.fixture(autouse=True)
def pygame_ready() -> Iterator[None]:
    """Guarantee an initialised pygame and a display surface for every test."""
    if not pygame.get_init():
        pygame.init()
    if not pygame.font.get_init():
        pygame.font.init()
    try:
        has_surface = pygame.display.get_surface() is not None
    except pygame.error:  # pragma: no cover - display subsystem torn down
        has_surface = False
    if not has_surface:
        pygame.display.set_mode((CANVAS_WIDTH, CANVAS_HEIGHT))
    yield


@pytest.fixture
def fonts(pygame_ready: None) -> FontSet:
    """A freshly loaded font set valid for the current pygame session."""
    return load_fonts()


@pytest.fixture
def canvas(pygame_ready: None) -> pygame.Surface:
    """A fresh 320x240 logical canvas."""
    return pygame.Surface((CANVAS_WIDTH, CANVAS_HEIGHT))


@pytest.fixture
def config() -> AppConfig:
    """Packaged default configuration."""
    return AppConfig()


@pytest.fixture
def snapshot() -> DashboardSnapshot:
    """The deterministic snapshot used by screenshots and layout tests."""
    return screenshot_snapshot()


@pytest.fixture
def deterministic_provider() -> Iterator[MockDashboardProvider]:
    """A seeded, non-evolving mock provider."""
    provider = MockDashboardProvider(seed=4242, evolving=False)
    yield provider
    provider.close()
