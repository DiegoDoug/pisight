"""Text sanitization for untrusted strings (SSIDs, device names, alert text).

Every string that originates outside PiSight -- Kismet responses in particular -- passes
through :func:`sanitize_text` before it is rendered or logged. Device names are attacker
controlled: an SSID may contain newlines, ANSI escapes, C0/C1 control characters or
bidirectional overrides that would corrupt a log file or scramble the display.
"""

from __future__ import annotations

import unicodedata

#: Bidirectional formatting characters that can visually reorder surrounding text.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏؜")

#: Replacement rendered in place of a stripped control character.
CONTROL_PLACEHOLDER = "."

#: Character appended when a string is shortened.
ELLIPSIS = "…"


def sanitize_text(value: object, *, max_length: int | None = None) -> str:
    """Return a single-line, control-character-free rendering of ``value``.

    Newlines and tabs collapse to single spaces, other control characters and bidi
    overrides become ``.``, and runs of whitespace collapse. ``None`` and non-string
    values become an empty string rather than ``"None"``.
    """
    if value is None or isinstance(value, bool):
        return ""
    if not isinstance(value, str):
        if isinstance(value, (int, float)):
            return str(value)
        return ""

    out: list[str] = []
    for char in value:
        if char in ("\n", "\r", "\t", "\v", "\f"):
            out.append(" ")
            continue
        if char in _BIDI_CONTROLS:
            out.append(CONTROL_PLACEHOLDER)
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Cf", "Cs", "Co", "Cn"):
            out.append(CONTROL_PLACEHOLDER)
            continue
        out.append(char)

    cleaned = " ".join("".join(out).split())
    if max_length is not None and max_length >= 0:
        cleaned = truncate(cleaned, max_length)
    return cleaned


def truncate(value: str, max_length: int) -> str:
    """Shorten ``value`` to ``max_length`` characters, appending an ellipsis if cut."""
    if max_length <= 0:
        return ""
    if len(value) <= max_length:
        return value
    if max_length == 1:
        return ELLIPSIS
    return value[: max_length - 1] + ELLIPSIS


def mask_mac(mac: str | None, *, show_full: bool = False) -> str:
    """Mask a MAC address down to its final four hexadecimal characters.

    ``show_full`` is honoured only when the operator explicitly opts in through the
    privacy configuration. The default keeps observations non-identifying on screen and
    in screenshots.
    """
    text = sanitize_text(mac)
    if not text:
        return "??"
    if show_full:
        return text.upper()
    hex_chars = [c for c in text if c in "0123456789abcdefABCDEF"]
    if len(hex_chars) < 4:
        return "??"
    return "··" + "".join(hex_chars[-4:]).upper()


def sanitize_for_log(value: object, *, max_length: int = 200) -> str:
    """Sanitize a value for inclusion in a log record (log-injection defence)."""
    return sanitize_text(value, max_length=max_length)
