"""
MobileNet-SSD (PASCAL VOC) person-only boxes for live overlays.

Must run ``apply_rtsp_env()`` before ``import cv2`` in this process (see import order below).

Concurrency: one ``OpenCVPersonDetector`` instance per thread is recommended (``cv2.dnn.Net``
is used from the owning thread only).

Security: model paths come from environment / fixed defaults under ``backend/models/``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .rtsp_capture import apply_rtsp_env

apply_rtsp_env()

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_PERSON = 15  # PASCAL VOC person class for MobileNet-SSD deploy


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _model_dir() -> Path:
    raw = os.environ.get("SMARTCAM_MODEL_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _backend_dir() / "models"


def _model_paths() -> Tuple[str, str]:
    md = _model_dir()
    proto = os.environ.get(
        "SMARTCAM_SSD_PROTO",
        str(md / "MobileNetSSD_deploy.prototxt"),
    )
    weights = os.environ.get(
        "SMARTCAM_SSD_WEIGHTS",
        str(md / "mobilenet_iter_73000.caffemodel"),
    )
    return proto, weights


def _parse_float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    v = float(str(raw).strip())
    if v < lo or v > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


def ssd_model_files_present() -> bool:
    proto, weights = _model_paths()
    return Path(proto).is_file() and Path(weights).is_file()


def person_detector_diagnostics() -> Dict[str, Any]:
    proto, weights = _model_paths()
    p_proto = Path(proto)
    p_weights = Path(weights)
    present = p_proto.is_file() and p_weights.is_file()
    load_ok = False
    err: Optional[str] = None
    if present:
        try:
            cv2.dnn.readNetFromCaffe(str(p_proto), str(p_weights))
            load_ok = True
        except Exception as e:
            err = str(e)
    try:
        conf = _parse_float_env("SMARTCAM_PERSON_CONFIDENCE", 0.45, 0.0, 1.0)
        min_frac = _parse_float_env("SMARTCAM_PERSON_MIN_BOX_FRACTION", 0.0005, 0.0, 1.0)
        env_err = None
    except ValueError as e:
        conf, min_frac = 0.45, 0.0005
        env_err = str(e)
    return {
        "pipeline": "opencv_mobilenet_ssd_person",
        "model_proto": str(p_proto),
        "model_weights": str(p_weights),
        "model_files_present": present,
        "model_load_ok": load_ok,
        "model_load_error": err,
        "confidence_threshold": conf,
        "min_box_fraction": min_frac,
        "env_parse_error": env_err,
    }


class OpenCVPersonDetector:
    """Person class only; normalized boxes for ``App.jsx`` overlay."""

    def __init__(
        self,
        confidence: Optional[float] = None,
        min_box_fraction: Optional[float] = None,
    ) -> None:
        if confidence is not None:
            self._confidence = confidence
        else:
            try:
                self._confidence = _parse_float_env(
                    "SMARTCAM_PERSON_CONFIDENCE", 0.45, 0.0, 1.0
                )
            except ValueError:
                self._confidence = 0.45
        if min_box_fraction is not None:
            self._min_box_fraction = min_box_fraction
        else:
            try:
                self._min_box_fraction = _parse_float_env(
                    "SMARTCAM_PERSON_MIN_BOX_FRACTION", 0.0005, 0.0, 1.0
                )
            except ValueError:
                self._min_box_fraction = 0.0005
        self._net: Optional[cv2.dnn.Net] = None
        self._load_failed = False

    def available(self) -> bool:
        return self._ensure_net() is not None

    def _ensure_net(self) -> Optional[cv2.dnn.Net]:
        if self._load_failed:
            return None
        if self._net is not None:
            return self._net
        proto, weights = _model_paths()
        if not Path(proto).is_file() or not Path(weights).is_file():
            return None
        try:
            self._net = cv2.dnn.readNetFromCaffe(proto, weights)
        except Exception as e:
            print("[person_detector] Failed to load MobileNet-SSD:", e)
            self._load_failed = True
            self._net = None
        return self._net

    def detect_normalized(self, frame_bgr: np.ndarray) -> List[Dict[str, Any]]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        h, w = frame_bgr.shape[:2]
        if w < 2 or h < 2:
            return []
        net = self._ensure_net()
        if net is None:
            return []
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame_bgr, (300, 300)),
            0.007843,
            (300, 300),
            127.5,
        )
        net.setInput(blob)
        detections = net.forward()
        out: List[Dict[str, Any]] = []
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            if conf < self._confidence:
                continue
            cls_id = int(detections[0, 0, i, 1])
            if cls_id != _PERSON:
                continue
            x1 = int(detections[0, 0, i, 3] * w)
            y1 = int(detections[0, 0, i, 4] * h)
            x2 = int(detections[0, 0, i, 5] * w)
            y2 = int(detections[0, 0, i, 6] * h)
            box_w = max(0, x2 - x1)
            box_h = max(0, y2 - y1)
            if box_w * box_h < (w * h) * self._min_box_fraction:
                continue
            out.append(
                {
                    "x": round(x1 / float(w), 6),
                    "y": round(y1 / float(h), 6),
                    "w": round(box_w / float(w), 6),
                    "h": round(box_h / float(h), 6),
                    "score": round(conf, 4),
                    "label": "person",
                    "source": "opencv_ssd",
                }
            )
        return out
