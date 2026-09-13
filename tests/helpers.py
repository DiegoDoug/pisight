"""Test helpers shared across modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Synthetic Kismet fixtures. Nothing here is a real observation.
FIXTURE_DIR = Path(__file__).parent.parent / "mock" / "fixtures"


def load_fixture(name: str) -> Any:
    """Read a synthetic Kismet fixture by filename."""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
