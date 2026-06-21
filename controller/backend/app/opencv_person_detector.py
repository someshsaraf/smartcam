"""
MobileNet-SSD (PASCAL VOC) person and animal boxes for live overlays.

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

# PASCAL VOC indices used by MobileNet-SSD deploy model (background + 20 classes)
_PERSON = 15
_ANIMAL = {3, 8, 10, 12, 13, 17}  # bird, cat, cow, dog, horse, sheep
_VOC_CLASS_NAMES: Dict[int, str] = {
    3: "bird",
    8: "cat",
    10: "cow",
    12: "dog",
    13: "horse",
    17: "sheep",
    15: "person",
}
# Lying pets (overhead cameras) are often misclassified as person — reclassify wide boxes.
_HORIZONTAL_PERSON_ASPECT = 1.12
_OVERLAP_IOU = 0.35


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
        animal_conf = _parse_float_env("SMARTCAM_ANIMAL_CONFIDENCE", 0.32, 0.0, 1.0)
        min_frac = _parse_float_env("SMARTCAM_PERSON_MIN_BOX_FRACTION", 0.0005, 0.0, 1.0)
        env_err = None
    except ValueError as e:
        conf, animal_conf, min_frac = 0.45, 0.32, 0.0005
        env_err = str(e)
    return {
        "pipeline": "opencv_mobilenet_ssd_person_animal",
        "model_proto": str(p_proto),
        "model_weights": str(p_weights),
        "model_files_present": present,
        "model_load_ok": load_ok,
        "model_load_error": err,
        "confidence_threshold": conf,
        "animal_confidence_threshold": animal_conf,
        "min_box_fraction": min_frac,
        "env_parse_error": env_err,
    }


def _box_iou(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["w"]), ay1 + float(a["h"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["w"]), by1 + float(b["h"])
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0.0:
        return 0.0
    return inter / union


def _reclassify_horizontal_person(box: Dict[str, Any]) -> Dict[str, Any]:
    """Overhead lying dogs often score as VOC person — prefer animal when box is wide."""
    if str(box.get("category") or "") != "person":
        return box
    bw = float(box.get("w") or 0.0)
    bh = float(box.get("h") or 0.0)
    if bh <= 1e-6:
        return box
    if bw / bh < _HORIZONTAL_PERSON_ASPECT:
        return box
    out = dict(box)
    out["category"] = "animal"
    out["label"] = "dog"
    out["reclassified"] = True
    return out


def _resolve_overlapping(boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop duplicate overlaps; when person and animal collide, keep animal."""
    ordered = sorted(boxes, key=lambda d: float(d.get("score") or 0.0), reverse=True)
    kept: List[Dict[str, Any]] = []
    for cand in ordered:
        replace_idx: Optional[int] = None
        skip = False
        for i, existing in enumerate(kept):
            if _box_iou(cand, existing) < _OVERLAP_IOU:
                continue
            cand_animal = str(cand.get("category") or "") == "animal"
            exist_animal = str(existing.get("category") or "") == "animal"
            if cand_animal and not exist_animal:
                replace_idx = i
                break
            skip = True
            break
        if skip:
            continue
        if replace_idx is not None:
            kept[replace_idx] = cand
        else:
            kept.append(cand)
    return kept


class OpenCVPersonDetector:
    """Person and animal VOC classes; normalized boxes for ``App.jsx`` overlay."""

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
        try:
            self._animal_confidence = _parse_float_env(
                "SMARTCAM_ANIMAL_CONFIDENCE", 0.32, 0.0, 1.0
            )
        except ValueError:
            self._animal_confidence = 0.32
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
        raw: List[Dict[str, Any]] = []
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            cls_id = int(detections[0, 0, i, 1])
            if cls_id == _PERSON:
                category = "person"
                min_conf = self._confidence
            elif cls_id in _ANIMAL:
                category = "animal"
                min_conf = self._animal_confidence
            else:
                continue
            if conf < min_conf:
                continue
            x1 = int(detections[0, 0, i, 3] * w)
            y1 = int(detections[0, 0, i, 4] * h)
            x2 = int(detections[0, 0, i, 5] * w)
            y2 = int(detections[0, 0, i, 6] * h)
            box_w = max(0, x2 - x1)
            box_h = max(0, y2 - y1)
            if box_w * box_h < (w * h) * self._min_box_fraction:
                continue
            label = _VOC_CLASS_NAMES.get(cls_id, category)
            raw.append(
                {
                    "x": round(x1 / float(w), 6),
                    "y": round(y1 / float(h), 6),
                    "w": round(box_w / float(w), 6),
                    "h": round(box_h / float(h), 6),
                    "score": round(conf, 4),
                    "label": label,
                    "category": category,
                    "source": "opencv_ssd",
                }
            )
        adjusted = [_reclassify_horizontal_person(b) for b in raw]
        return _resolve_overlapping(adjusted)
