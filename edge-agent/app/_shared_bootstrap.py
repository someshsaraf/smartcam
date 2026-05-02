"""
Ensure ``surveillance_shared`` is importable before ``worker`` or other app modules load.

Imported for side effects from ``main`` and ``worker``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DONE = False


def ensure_surveillance_shared_on_path() -> None:
    global _DONE
    if _DONE:
        return

    edge_root = Path(__file__).resolve().parent.parent
    parent = edge_root.parent
    env_dir = os.environ.get("SURVEILLANCE_SHARED_PATH", "").strip()

    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            edge_root / "shared",
            parent / "controller" / "shared",
            parent / "shared",
        ]
    )

    for base in candidates:
        try:
            resolved = base.expanduser().resolve()
            pkg = resolved / "surveillance_shared"
            if not pkg.is_dir():
                continue
            if not (pkg / "detector.py").is_file():
                continue
            root_str = str(resolved)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
            _DONE = True
            return
        except OSError:
            continue

    raise ImportError(
        "surveillance_shared not found. Expected a directory containing "
        "surveillance_shared/detector.py. Tried SURVEILLANCE_SHARED_PATH, "
        "edge-agent/shared, ../controller/shared, ../shared. edge_root="
        + str(edge_root)
    )


ensure_surveillance_shared_on_path()
