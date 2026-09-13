"""PiSight: a passive Wi-Fi reconnaissance dashboard for small Raspberry Pi displays.

PiSight is a *display and triage client*. Kismet remains the capture engine and the
source of truth. PiSight never captures packets, never transmits, and only ever reads
from the Kismet REST API.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
