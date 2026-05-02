"""Thin shim: MobileNet-SSD lives in ``surveillance_shared``."""

from __future__ import annotations

import os
from pathlib import Path

from . import _shared_path

_shared_path.ensure_shared_on_path()
os.environ.setdefault(
    "SURVEILLANCE_MODEL_DIR",
    str(Path(__file__).resolve().parent.parent / "models"),
)

from surveillance_shared.detector import Detector, get_detector_diagnostics

__all__ = ["Detector", "get_detector_diagnostics"]
