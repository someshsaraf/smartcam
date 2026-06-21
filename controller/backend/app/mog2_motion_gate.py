"""
MOG2 foreground gate — skip YOLO inference when the scene is static.

One ``Mog2MotionGate`` per camera thread (not thread-safe across threads).
"""

from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np


def _min_area_fraction() -> float:
    raw = os.environ.get("SMARTCAM_MOG2_MIN_AREA_FRACTION", "0.003").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 0.003
    return max(0.0005, min(v, 0.25))


def _warmup_frames() -> int:
    raw = os.environ.get("SMARTCAM_MOG2_WARMUP_FRAMES", "8").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 30
    return max(0, min(n, 300))


class Mog2MotionGate:
    """Returns True when MOG2 sees a large enough foreground blob."""

    def __init__(self) -> None:
        self._mog: Optional[cv2.BackgroundSubtractorMOG2] = None
        self._warmup_remaining = _warmup_frames()
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    def has_motion(self, frame_bgr: np.ndarray) -> bool:
        if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            return False
        if self._mog is None:
            self._mog = cv2.createBackgroundSubtractorMOG2(
                history=500,
                varThreshold=40,
                detectShadows=False,
            )
        mask = self._mog.apply(frame_bgr)
        if self._warmup_remaining > 0:
            self._warmup_remaining -= 1
            return False
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = frame_bgr.shape[:2]
        min_a = (w * h) * _min_area_fraction()
        for c in contours:
            if cv2.contourArea(c) >= min_a:
                return True
        return False
