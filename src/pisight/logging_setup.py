"""Logging configuration with secret redaction and control-character sanitization.

Two hazards this guards against:

* **Token leakage.** Nothing in PiSight logs the API token deliberately, but an httpx
  exception repr or a future refactor could. :class:`RedactingFilter` scrubs the live token
  value out of every record as a backstop.
* **Log injection.** Device names and alert text come from the air. A crafted SSID
  containing newlines can forge log lines that a downstream parser or a human reading
  ``journalctl`` would take at face value. Every record's formatted message is flattened to
  a single line.

Output goes to stderr, which systemd captures into the journal. PiSight writes no log files
of its own, so it cannot fill the capture volume.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

from .config import get_api_token
from .sanitize import sanitize_text

__all__ = ["RedactingFilter", "configure_logging"]

#: What a redacted secret looks like in the log.
REDACTED = "[redacted]"


class RedactingFilter(logging.Filter):
    """Removes the API token from log records and flattens them to a single line."""

    def __init__(self, token: str | None = None) -> None:
        super().__init__()
        self._token = token or None

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record in place; always returns True so nothing is dropped."""
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # pragma: no cover - malformed format arguments
            message = str(record.msg)

        if self._token:
            message = message.replace(self._token, REDACTED)

        record.msg = sanitize_text(message, max_length=1000)
        record.args = ()
        return True


class SafeFormatter(logging.Formatter):
    """Redact the final formatted line, including chained exceptions and stack traces."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        token = get_api_token()
        if token:
            text = text.replace(token, REDACTED)
        return sanitize_text(text, max_length=8000)


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Configure root logging for the application.

    Called once at startup. Re-calling replaces PiSight's handler rather than stacking a
    second one, so a repeated call cannot double every line.
    """
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)

    for handler in list(root.handlers):
        if getattr(handler, "_pisight", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(get_api_token()))
    handler._pisight = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # httpx logs the full request URL at INFO. PiSight keeps the token out of URLs, but
    # quieting the library keeps the journal readable at two polls a second regardless.
    logging.getLogger("httpx").setLevel(max(numeric, logging.WARNING))
    logging.getLogger("httpcore").setLevel(logging.WARNING)
