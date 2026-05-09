"""
Shared pytest fixtures for the edge-agent test suite.

Adds the project root to ``sys.path`` so ``from app.local_publisher import …``
works without installing the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EDGE_ROOT = Path(__file__).resolve().parent.parent
if str(_EDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_EDGE_ROOT))
