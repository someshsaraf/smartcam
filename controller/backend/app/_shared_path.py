"""Put repo ``shared/`` on ``sys.path`` so ``surveillance_shared`` imports work."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_shared_on_path() -> None:
    root = Path(__file__).resolve().parents[2]
    s = str(root / "shared")
    if s not in sys.path:
        sys.path.insert(0, s)
