"""Font loading with a guaranteed fallback.

DejaVu Sans ships with Raspberry Pi OS and is the intended face. If it is missing -- on a
minimal image, or on a desktop without it -- PiSight walks a list of common alternatives
and finally falls back to Pygame's built-in font. Rendering must never fail for want of a
font file, and no font is ever downloaded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pygame

from .theme import (
    FONT_CANDIDATES,
    FONT_SIZE_BODY,
    FONT_SIZE_HUGE,
    FONT_SIZE_LARGE,
    FONT_SIZE_SMALL,
    FONT_SIZE_TINY,
)

__all__ = ["FontSet", "load_fonts", "resolve_font_name"]

logger = logging.getLogger(__name__)


def resolve_font_name(candidates: tuple[str, ...] = FONT_CANDIDATES) -> str | None:
    """Return the first installed font from ``candidates``, or ``None`` for the default."""
    try:
        available = set(pygame.font.get_fonts())
    except (pygame.error, AttributeError):  # pragma: no cover - depends on SDL build
        return None
    for name in candidates:
        if name in available:
            return name
    return None


@dataclass(frozen=True, slots=True)
class FontSet:
    """The five type sizes every screen draws with."""

    tiny: pygame.font.Font
    small: pygame.font.Font
    body: pygame.font.Font
    large: pygame.font.Font
    huge: pygame.font.Font
    family: str

    def measure(self, font: pygame.font.Font, text: str) -> int:
        """Width of ``text`` in logical pixels."""
        return int(font.size(text)[0])

    def ellipsize(self, font: pygame.font.Font, text: str, max_width: int) -> str:
        """Shorten ``text`` with a trailing ellipsis until it fits ``max_width``.

        Measured against the actual font rather than by character count, because a long
        SSID of narrow characters may fit where a short one of wide characters does not.
        Returns an empty string when not even an ellipsis fits.
        """
        if max_width <= 0:
            return ""
        if font.size(text)[0] <= max_width:
            return text

        ellipsis = "…"
        if font.size(ellipsis)[0] > max_width:
            return ""

        low, high = 0, len(text)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle].rstrip() + ellipsis
            if font.size(candidate)[0] <= max_width:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best or ellipsis


def load_fonts() -> FontSet:
    """Initialise the font module if needed and build the :class:`FontSet`."""
    if not pygame.font.get_init():
        pygame.font.init()

    name = resolve_font_name()
    if name is None:
        logger.info("DejaVu Sans not found; using the built-in Pygame font")

    def build(size: int) -> pygame.font.Font:
        if name is None:
            return pygame.font.Font(None, size + 2)
        return pygame.font.SysFont(name, size)

    return FontSet(
        tiny=build(FONT_SIZE_TINY),
        small=build(FONT_SIZE_SMALL),
        body=build(FONT_SIZE_BODY),
        large=build(FONT_SIZE_LARGE),
        huge=build(FONT_SIZE_HUGE),
        family=name or "pygame-default",
    )
