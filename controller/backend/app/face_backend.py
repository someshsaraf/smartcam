"""Detection backend for controller live preview overlays.

Backends:
- opencv: original Haar frontal face detector over the full frame.
- hailo / hailo_person_face / hybrid: Hailo YOLOv8n person detection plus
  optional OpenCV face detection inside person boxes.

The function name remains detect_faces_normalized for backwards compatibility.
Returned boxes may be labelled "person" or "face".
"""
from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)
_MAX_FACES = 32
_HAAR: Optional[cv2.CascadeClassifier] = None
_HAAR_ERROR_LOGGED = False
_HAILO_FALLBACK_WARNED = False


def _parse_backend() -> str:
    v = os.environ.get("SMARTCAM_FACE_BACKEND", "opencv").strip().lower()
    aliases = {"opencv": "opencv", "haar": "opencv", "hailo": "hailo_person_face", "hybrid": "hailo_person_face", "hailo_person_face": "hailo_person_face", "person_face": "hailo_person_face"}
    if v in aliases:
        return aliases[v]
    logger.warning("SMARTCAM_FACE_BACKEND invalid %r; using opencv", v)
    return "opencv"


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _load_haar() -> Optional[cv2.CascadeClassifier]:
    global _HAAR, _HAAR_ERROR_LOGGED
    if _HAAR is not None:
        return _HAAR
    cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    if not os.path.isfile(cascade_path):
        if not _HAAR_ERROR_LOGGED:
            logger.error("Haar cascade missing: %s", cascade_path)
            _HAAR_ERROR_LOGGED = True
        return None
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        if not _HAAR_ERROR_LOGGED:
            logger.error("Failed to load Haar cascade: %s", cascade_path)
            _HAAR_ERROR_LOGGED = True
        return None
    _HAAR = cascade
    return _HAAR


def _detect_haar_in_region(frame_bgr: np.ndarray, *, roi: Optional[tuple[int, int, int, int]] = None, max_faces: int = _MAX_FACES) -> List[dict[str, Any]]:
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    h, w = frame_bgr.shape[:2]
    if w < 2 or h < 2:
        return []
    if roi is None:
        x0, y0, x1, y1 = 0, 0, w, h
    else:
        x0, y0, x1, y1 = roi
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 20 or y1 - y0 < 20:
            return []
    crop = frame_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    cascade = _load_haar()
    if cascade is None:
        return []
    min_neighbors = _env_int("SMARTCAM_FACE_HAAR_MIN_NEIGHBORS", 6, 1, 12)
    scale = _env_float("SMARTCAM_FACE_HAAR_SCALE", 1.08, 1.01, 1.5)
    min_face_px = _env_int("SMARTCAM_FACE_MIN_BOX_PX", 32, 12, 4096)
    rects = []
    weights: list[float] = []
    if hasattr(cascade, "detectMultiScale3"):
        try:
            detected = cascade.detectMultiScale3(gray, scaleFactor=scale, minNeighbors=min_neighbors, minSize=(min_face_px, min_face_px), outputRejectLevels=True)
            if isinstance(detected, tuple) and len(detected) >= 3:
                rects = detected[0]
                level_weights = detected[2]
                if level_weights is not None and len(level_weights):
                    weights = [float(v) for v in level_weights.flatten()[: len(rects)]]
        except cv2.error:
            rects = []
    if len(rects) == 0:
        rects = cascade.detectMultiScale(gray, scaleFactor=scale, minNeighbors=min_neighbors, minSize=(min_face_px, min_face_px))
    max_weight = max(weights) if weights else 0.0
    min_aspect = _env_float("SMARTCAM_FACE_MIN_ASPECT", 0.70, 0.1, 3.0)
    max_aspect = _env_float("SMARTCAM_FACE_MAX_ASPECT", 1.45, 0.2, 5.0)
    out: List[dict[str, Any]] = []
    for i, (x, y, fw, fh) in enumerate(rects[:max_faces]):
        if fw <= 0 or fh <= 0:
            continue
        aspect = float(fw) / float(fh)
        if aspect < min_aspect or aspect > max_aspect:
            continue
        ax1 = max(0, min(w - 1, int(x0 + x)))
        ay1 = max(0, min(h - 1, int(y0 + y)))
        ax2 = max(ax1 + 1, min(w, int(x0 + x + fw)))
        ay2 = max(ay1 + 1, min(h, int(y0 + y + fh)))
        bw, bh = ax2 - ax1, ay2 - ay1
        if bw < min_face_px or bh < min_face_px:
            continue
        score = max(0.0, min(1.0, weights[i] / max_weight)) if i < len(weights) and max_weight > 0 else 1.0
        out.append({"x": round(ax1 / float(w), 6), "y": round(ay1 / float(h), 6), "w": round(bw / float(w), 6), "h": round(bh / float(h), 6), "score": round(score, 4), "label": "face", "source": "opencv_haar"})
    return out


def _person_roi(frame_shape: tuple[int, ...], p: dict[str, Any]) -> tuple[int, int, int, int]:
    h, w = frame_shape[:2]
    x, y, bw, bh = float(p.get("x", 0.0)), float(p.get("y", 0.0)), float(p.get("w", 0.0)), float(p.get("h", 0.0))
    mx, my = 0.08 * bw, 0.08 * bh
    return int((x - mx) * w), int((y - my) * h), int((x + bw + mx) * w), int((y + min(bh, bh * 0.62) + my) * h)


def _detect_hailo_person_face(frame_bgr: np.ndarray) -> List[dict[str, Any]]:
    global _HAILO_FALLBACK_WARNED
    try:
        from .hailo_yolov8_backend import detect_people_normalized, get_detector
        people = detect_people_normalized(frame_bgr)
        detector = get_detector()
        if detector.error and not _HAILO_FALLBACK_WARNED:
            logger.warning("Hailo backend unavailable (%s); person detection disabled", detector.error)
            _HAILO_FALLBACK_WARNED = True
            return []
    except Exception as e:
        if not _HAILO_FALLBACK_WARNED:
            logger.exception("Hailo backend failed; person detection disabled: %s", e)
            _HAILO_FALLBACK_WARNED = True
        return []

    out: List[dict[str, Any]] = []
    include_people = os.environ.get("SMARTCAM_SHOW_PERSON_BOXES", "1").strip().lower() not in ("0", "false", "no", "off")
    if include_people:
        out.extend(people)
    face_stage = os.environ.get("SMARTCAM_HAILO_FACE_SECOND_STAGE", "none").strip().lower()
    if face_stage in ("0", "false", "off", "none", "disabled"):
        return out
    max_faces_per_person = _env_int("SMARTCAM_FACE_MAX_PER_PERSON", 3, 0, 16)
    for p in people:
        out.extend(_detect_haar_in_region(frame_bgr, roi=_person_roi(frame_bgr.shape, p), max_faces=max_faces_per_person))
    return out


def inference_debug_status() -> dict[str, Any]:
    """Diagnostics for UI / WebSocket (independent of recording mode)."""
    backend = _parse_backend()
    out: dict[str, Any] = {
        "backend": backend,
        "hailo_ready": False,
        "hailo_error": None,
    }
    if backend != "hailo_person_face":
        return out
    try:
        from .hailo_yolov8_backend import get_detector

        det = get_detector()
        out["hailo_error"] = det.error
        out["hailo_ready"] = det.error is None
    except Exception as e:
        out["hailo_error"] = str(e)
    return out


def detect_faces_normalized(frame_bgr: np.ndarray) -> List[dict[str, Any]]:
    if _parse_backend() == "hailo_person_face":
        return _detect_hailo_person_face(frame_bgr)
    return _detect_haar_in_region(frame_bgr)
