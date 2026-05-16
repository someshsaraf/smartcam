"""
Face box detection for controller live preview (Phase 1).

- ``opencv``: Haar frontal face (bundled with OpenCV). No Hailo required.
- ``hailo``: reserved; falls back to OpenCV with a one-time log until a Hailo
  pipeline is integrated.

Output boxes are normalized to the input frame: x, y, w, h in [0, 1] with
(x, y) top-left in image coordinates.
"""

from __future__ import annotations

import logging
import os
from typing import Any, List

import cv2

logger = logging.getLogger(__name__)

_hailo_warned = False
_MAX_FACES = 32


def _parse_backend() -> str:
    v = os.environ.get("SMARTCAM_FACE_BACKEND", "opencv").strip().lower()
    if v in ("opencv", "hailo"):
        return v
    logger.warning("SMARTCAM_FACE_BACKEND invalid %r; using opencv", v)
    return "opencv"


def detect_faces_normalized(frame_bgr: np.ndarray) -> List[dict[str, Any]]:
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    h, w = frame_bgr.shape[:2]
    if w < 2 or h < 2:
        return []

    backend = _parse_backend()
    if backend == "hailo":
        global _hailo_warned
        if not _hailo_warned:
            logger.warning(
                "SMARTCAM_FACE_BACKEND=hailo is not integrated; using OpenCV Haar until "
                "Hailo runtime is wired (see smartcam/docs/PLAN.md Phase 1)."
            )
            _hailo_warned = True

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    cascade_path = os.path.join(
        cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
    )
    if not os.path.isfile(cascade_path):
        logger.error("Haar cascade missing: %s", cascade_path)
        return []
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        logger.error("Failed to load Haar cascade: %s", cascade_path)
        return []

    min_side = min(w, h)
    min_neighbors = 4
    try:
        min_neighbors = int(os.environ.get("SMARTCAM_FACE_HAAR_MIN_NEIGHBORS", "4"))
    except ValueError:
        min_neighbors = 4
    min_neighbors = max(1, min(12, min_neighbors))

    scale = 1.05
    try:
        scale = float(os.environ.get("SMARTCAM_FACE_HAAR_SCALE", "1.05"))
    except ValueError:
        scale = 1.05
    if scale < 1.01 or scale > 1.5:
        scale = 1.05

    min_size = (max(20, min_side // 20), max(20, min_side // 20))
    rects = []
    weights: list[float] = []

    if hasattr(cascade, "detectMultiScale3"):
        try:
            detected = cascade.detectMultiScale3(
                gray,
                scaleFactor=scale,
                minNeighbors=min_neighbors,
                minSize=min_size,
                outputRejectLevels=True,
            )
            if isinstance(detected, tuple) and len(detected) >= 3:
                rects = detected[0]
                level_weights = detected[2]
                if level_weights is not None and len(level_weights):
                    weights = [float(v) for v in level_weights.flatten()[: len(rects)]]
        except cv2.error:
            rects = []

    if len(rects) == 0:
        rects = cascade.detectMultiScale(
            gray,
            scaleFactor=scale,
            minNeighbors=min_neighbors,
            minSize=min_size,
        )

    max_weight = max(weights) if weights else 0.0
    out: List[dict[str, Any]] = []
    for i, (x, y, fw, fh) in enumerate(rects[:_MAX_FACES]):
        x1 = max(0, min(w - 1, int(x)))
        y1 = max(0, min(h - 1, int(y)))
        x2 = max(x1 + 1, min(w, int(x + fw)))
        y2 = max(y1 + 1, min(h, int(y + fh)))
        bw = x2 - x1
        bh = y2 - y1
        if i < len(weights) and max_weight > 0:
            score = max(0.0, min(1.0, weights[i] / max_weight))
        else:
            score = 1.0
        out.append(
            {
                "x": round(x1 / float(w), 6),
                "y": round(y1 / float(h), 6),
                "w": round(bw / float(w), 6),
                "h": round(bh / float(h), 6),
                "score": round(score, 4),
            }
        )
    return out
