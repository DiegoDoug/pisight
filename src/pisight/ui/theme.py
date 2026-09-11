"""Colour palette and typography for the compact field-instrument look.

Flat colour only: no gradients, no alpha compositing, no downloaded assets. On a 320x240
SPI TFT viewed at arm's length, contrast and glyph size carry the whole design.

Colour is never the only carrier of meaning. Every status shown in a colour also has a text
label or a drawn glyph, so the display stays readable for a colour-blind operator and in
direct sunlight where hue washes out.
"""

from __future__ import annotations

from typing import Final

RGB = tuple[int, int, int]

# --- Base surfaces ---------------------------------------------------------------------
BACKGROUND: Final[RGB] = (8, 10, 12)
PANEL: Final[RGB] = (20, 24, 29)
PANEL_ALT: Final[RGB] = (28, 33, 39)
DIVIDER: Final[RGB] = (48, 56, 64)

# --- Text ------------------------------------------------------------------------------
TEXT: Final[RGB] = (238, 243, 248)
TEXT_MUTED: Final[RGB] = (132, 145, 156)
TEXT_INVERSE: Final[RGB] = (8, 10, 12)

# --- Semantic accents ------------------------------------------------------------------
ACCENT: Final[RGB] = (0, 201, 222)
"""Cyan: normal activity, selection, primary data."""

WARN: Final[RGB] = (255, 176, 0)
"""Amber: warnings, stale data, medium-severity alerts."""

DANGER: Final[RGB] = (255, 74, 74)
"""Red: high and critical alerts, offline state, errors."""

OK: Final[RGB] = (0, 201, 222)
"""Healthy state shares the cyan accent; paired text always says which."""

# --- Chart / bar colours ---------------------------------------------------------------
BAR_2GHZ: Final[RGB] = (0, 201, 222)
BAR_5GHZ: Final[RGB] = (140, 120, 255)
BAR_EMPTY: Final[RGB] = (34, 40, 47)

# --- Navigation ------------------------------------------------------------------------
NAV_BACKGROUND: Final[RGB] = (16, 19, 23)
NAV_ACTIVE: Final[RGB] = (0, 201, 222)
NAV_ACTIVE_TEXT: Final[RGB] = (8, 10, 12)
NAV_INACTIVE_TEXT: Final[RGB] = (150, 162, 172)

# --- Status bar ------------------------------------------------------------------------
STATUS_BACKGROUND: Final[RGB] = (16, 19, 23)

#: Font sizes, in logical pixels, tuned so no label clips inside a 320x240 canvas.
FONT_SIZE_TINY: Final[int] = 9
FONT_SIZE_SMALL: Final[int] = 11
FONT_SIZE_BODY: Final[int] = 13
FONT_SIZE_LARGE: Final[int] = 18
FONT_SIZE_HUGE: Final[int] = 28

#: Font families tried in order. DejaVu Sans ships with Raspberry Pi OS; the remaining
#: entries cover desktop development hosts. Pygame's default font is the final fallback,
#: so PiSight never depends on a downloaded asset.
FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "dejavusans",
    "dejavusanscondensed",
    "liberationsans",
    "freesans",
    "arial",
    "helvetica",
)


def severity_color(severity_name: str) -> RGB:
    """Map a :class:`~pisight.models.Severity` name to its palette colour."""
    return {
        "INFO": TEXT_MUTED,
        "LOW": ACCENT,
        "MEDIUM": WARN,
        "HIGH": DANGER,
        "CRITICAL": DANGER,
    }.get(severity_name, TEXT_MUTED)


def signal_color(dbm: int | None) -> RGB:
    """Colour a signal reading by strength; the numeric value is always shown too."""
    if dbm is None:
        return TEXT_MUTED
    if dbm >= -60:
        return ACCENT
    if dbm >= -75:
        return WARN
    return DANGER
