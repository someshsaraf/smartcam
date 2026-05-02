"""
Object detector for person / vehicle / animal using OpenCV MobileNet-SSD (PASCAL VOC).

Filters out small irrelevant VOC classes (bottle, chair, etc.). Insects, leaves, curtains,
and fan blur are not VOC classes — they should not produce confident box outputs on this
detector (unlike naive motion pixels).

Hailo / YOLO upgrade: swap ``Detector.detect_interesting`` for a pipeline that runs your
Hailo SSD/YOLO graph and maps COCO (or custom) labels to person / vehicle / animal only,
with confidence and minimum box area checks similar to below.

Model files (place under backend/models/):

- MobileNetSSD_deploy.prototxt — https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt
- mobilenet_iter_73000.caffemodel — https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel

Or run: ``backend/scripts/fetch_ssd_models.sh`` (downloads both into ``backend/models/``).

If SSD files are missing, optional **MOG2** motion fallback can trigger clips (not person-only).
Set ``SURVEILLANCE_FRAME_DIFF_FALLBACK=0`` to disable fallback when models are absent.

Environment (optional): ``SURVEILLANCE_SSD_PROTO``, ``SURVEILLANCE_SSD_WEIGHTS``,
``SURVEILLANCE_SSD_CONFIDENCE`` (0–1, default 0.45),
``SURVEILLANCE_SSD_MIN_BOX_FRACTION`` (0–1, default 0.0005; lower keeps smaller person boxes).

Fallback motion (SSD unavailable): ``SURVEILLANCE_FRAME_DIFF_FALLBACK`` (default ``1``),
``SURVEILLANCE_FALLBACK_MIN_AREA_FRACTION`` (default ``0.012`` of frame for contour area).

Concurrency: one caller at a time per instance (each CameraRecorder owns its own Detector).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from surveillance_shared.rtsp_env import apply_rtsp_env

apply_rtsp_env()

import cv2
import numpy as np

# PASCAL VOC indices used by MobileNet-SSD deploy model (background + 20 classes)
# https://github.com/chuanqi305/MobileNet-SSD/blob/master/deploy.prototxt
_PERSON = 15
_VEHICLE = {1, 2, 4, 6, 7, 14, 19}  # aeroplane, bicycle, boat, bus, car, motorbike, train
_ANIMAL = {3, 8, 10, 12, 13, 17}  # bird, cat, cow, dog, horse, sheep
_VOC_INTERESTING_NAMES: Dict[int, str] = {
    1: "aeroplane",
    2: "bicycle",
    4: "boat",
    6: "bus",
    7: "car",
    14: "motorbike",
    19: "train",
    3: "bird",
    8: "cat",
    10: "cow",
    12: "dog",
    13: "horse",
    17: "sheep",
    15: "person",
}


MODEL_DIR = Path(
        os.environ.get(
            "SURVEILLANCE_MODEL_DIR",
            str(Path(__file__).resolve().parent / "models"),
        )
    )


def _model_paths() -> Tuple[str, str]:
    proto = os.environ.get(
        "SURVEILLANCE_SSD_PROTO",
        str(MODEL_DIR / "MobileNetSSD_deploy.prototxt"),
    )
    # Official repo hosts weights as mobilenet_iter_73000.caffemodel (not MobileNetSSD_deploy.caffemodel).
    weights = os.environ.get(
        "SURVEILLANCE_SSD_WEIGHTS",
        str(MODEL_DIR / "mobilenet_iter_73000.caffemodel"),
    )
    return proto, weights


def _frame_diff_fallback_enabled() -> bool:
    v = os.environ.get("SURVEILLANCE_FRAME_DIFF_FALLBACK", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _fallback_min_area_fraction() -> float:
    try:
        return _parse_float_env(
            "SURVEILLANCE_FALLBACK_MIN_AREA_FRACTION", 0.012, 0.0001, 0.5
        )
    except ValueError:
        return 0.012


def _parse_float_env(name: str, default: float, min_v: float, max_v: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw.strip())
    except ValueError as e:
        raise ValueError(f"{name} must be a float") from e
    if v < min_v or v > max_v:
        raise ValueError(f"{name} must be between {min_v} and {max_v}")
    return v


def get_detector_diagnostics() -> Dict[str, Any]:
    """
    Paths and whether Caffe SSD weights load (read-only check for /system/recording).
    Does not mutate Detector instances used by workers.
    """
    proto, weights = _model_paths()
    p_proto = Path(proto)
    p_weights = Path(weights)
    files_present = p_proto.is_file() and p_weights.is_file()
    load_ok = False
    load_error: Optional[str] = None
    if files_present:
        try:
            cv2.dnn.readNetFromCaffe(str(p_proto), str(p_weights))
            load_ok = True
        except Exception as e:
            load_error = str(e)
    conf = 0.45
    min_frac = 0.0005
    env_parse_error: Optional[str] = None
    try:
        conf = _parse_float_env("SURVEILLANCE_SSD_CONFIDENCE", 0.45, 0.0, 1.0)
        min_frac = _parse_float_env(
            "SURVEILLANCE_SSD_MIN_BOX_FRACTION", 0.0005, 0.0, 1.0
        )
    except ValueError as e:
        env_parse_error = str(e)
    fb = _frame_diff_fallback_enabled()
    if files_present and load_ok:
        pipeline = "ssd"
    elif fb:
        pipeline = "frame_diff"
    else:
        pipeline = "none"
    return {
        "model_proto_path": str(p_proto),
        "model_weights_path": str(p_weights),
        "model_files_present": files_present,
        "model_load_ok": load_ok,
        "model_load_error": load_error,
        "effective_confidence_threshold": conf,
        "effective_min_box_fraction": min_frac,
        "env_parse_error": env_parse_error,
        "frame_diff_fallback_enabled": fb,
        "effective_fallback_min_area_fraction": _fallback_min_area_fraction(),
        "motion_pipeline": pipeline,
    }


class Detector:
    def __init__(
        self,
        confidence_threshold: Optional[float] = None,
        min_box_fraction: Optional[float] = None,
    ) -> None:
        if confidence_threshold is not None:
            self._confidence_threshold = confidence_threshold
        else:
            try:
                self._confidence_threshold = _parse_float_env(
                    "SURVEILLANCE_SSD_CONFIDENCE", 0.45, 0.0, 1.0
                )
            except ValueError as e:
                print("[detector] SURVEILLANCE_SSD_CONFIDENCE invalid:", e, "; using 0.45")
                self._confidence_threshold = 0.45
        if min_box_fraction is not None:
            self._min_box_fraction = min_box_fraction
        else:
            try:
                self._min_box_fraction = _parse_float_env(
                    "SURVEILLANCE_SSD_MIN_BOX_FRACTION", 0.0005, 0.0, 1.0
                )
            except ValueError as e:
                print(
                    "[detector] SURVEILLANCE_SSD_MIN_BOX_FRACTION invalid:",
                    e,
                    "; using 0.0005",
                )
                self._min_box_fraction = 0.0005
        self._net: cv2.dnn.Net | None = None
        self._mog: Optional[cv2.BackgroundSubtractor] = None
        self._fallback_warmup_remaining = 90
        self._logged_fallback = False
        self._warned_pipeline_dead = False
        self._ssd_load_failed = False

    def _ensure_net(self) -> cv2.dnn.Net | None:
        if self._ssd_load_failed:
            return None
        if self._net is not None:
            return self._net
        proto, weights = _model_paths()
        if not Path(proto).is_file() or not Path(weights).is_file():
            return None
        try:
            self._net = cv2.dnn.readNetFromCaffe(proto, weights)
        except Exception as e:
            print("[detector] Failed to load MobileNet-SSD:", e)
            self._ssd_load_failed = True
            self._net = None
        return self._net

    def _detect_ssd(self, frame_bgr: np.ndarray) -> bool:
        h, w = frame_bgr.shape[:2]
        net = self._ensure_net()
        if net is None:
            return False
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame_bgr, (300, 300)),
            0.007843,
            (300, 300),
            127.5,
        )
        net.setInput(blob)
        detections = net.forward()
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            if conf < self._confidence_threshold:
                continue
            cls_id = int(detections[0, 0, i, 1])
            if cls_id == _PERSON or cls_id in _VEHICLE or cls_id in _ANIMAL:
                x1 = int(detections[0, 0, i, 3] * w)
                y1 = int(detections[0, 0, i, 4] * h)
                x2 = int(detections[0, 0, i, 5] * w)
                y2 = int(detections[0, 0, i, 6] * h)
                box_w = max(0, x2 - x1)
                box_h = max(0, y2 - y1)
                if box_w * box_h < (w * h) * self._min_box_fraction:
                    continue
                return True
        return False

    def _detect_motion_fallback(self, frame_bgr: np.ndarray) -> bool:
        """Large foreground blobs vs learned background — not class-aware."""
        if self._mog is None:
            self._mog = cv2.createBackgroundSubtractorMOG2(
                history=500, varThreshold=40, detectShadows=False
            )
        mask = self._mog.apply(frame_bgr)
        if self._fallback_warmup_remaining > 0:
            self._fallback_warmup_remaining -= 1
            return False
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=1)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        h, w = frame_bgr.shape[:2]
        min_a = (w * h) * _fallback_min_area_fraction()
        for c in contours:
            if cv2.contourArea(c) >= min_a:
                return True
        return False

    def detect_interesting(self, frame_bgr: np.ndarray) -> bool:
        """
        Returns True when SSD sees person/vehicle/animal, or when MOG2 fallback fires
        if SSD weights are unavailable (optional).
        """
        if frame_bgr is None or frame_bgr.size == 0:
            return False
        net = self._ensure_net()
        if net is not None:
            return self._detect_ssd(frame_bgr)
        if _frame_diff_fallback_enabled():
            if not self._logged_fallback:
                print(
                    "[detector] SSD unavailable; using MOG2 motion fallback "
                    "(not person/vehicle-only). Install models in",
                    MODEL_DIR,
                    "or set SURVEILLANCE_FRAME_DIFF_FALLBACK=0 to disable clips without SSD.",
                )
                self._logged_fallback = True
            return self._detect_motion_fallback(frame_bgr)
        if not self._warned_pipeline_dead:
            print(
                "[detector] MobileNet-SSD files missing and frame-diff fallback disabled; "
                "motion clips will not trigger. Place weights in",
                MODEL_DIR,
                "or enable SURVEILLANCE_FRAME_DIFF_FALLBACK=1.",
            )
            self._warned_pipeline_dead = True
        return False

    def detect_interesting_with_tags(self, frame_bgr: np.ndarray) -> tuple[bool, List[str]]:
        """Interesting VOC classes or ``motion`` when MOG2 fallback fires."""
        if frame_bgr is None or frame_bgr.size == 0:
            return False, []
        net = self._ensure_net()
        if net is not None:
            return self._detect_ssd_tags(frame_bgr)
        if _frame_diff_fallback_enabled():
            if not self._logged_fallback:
                print(
                    "[detector] SSD unavailable; using MOG2 motion fallback.",
                )
                self._logged_fallback = True
            hit = self._detect_motion_fallback(frame_bgr)
            return hit, (["motion"] if hit else [])
        if not self._warned_pipeline_dead:
            print("[detector] SSD missing and fallback disabled; no detections.")
            self._warned_pipeline_dead = True
        return False, []

    def _detect_ssd_tags(self, frame_bgr: np.ndarray) -> tuple[bool, List[str]]:
        h, w = frame_bgr.shape[:2]
        net = self._ensure_net()
        if net is None:
            return False, []
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame_bgr, (300, 300)),
            0.007843,
            (300, 300),
            127.5,
        )
        net.setInput(blob)
        detections = net.forward()
        tags: List[str] = []
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            if conf < self._confidence_threshold:
                continue
            cls_id = int(detections[0, 0, i, 1])
            if cls_id == _PERSON or cls_id in _VEHICLE or cls_id in _ANIMAL:
                x1 = int(detections[0, 0, i, 3] * w)
                y1 = int(detections[0, 0, i, 4] * h)
                x2 = int(detections[0, 0, i, 5] * w)
                y2 = int(detections[0, 0, i, 6] * h)
                box_w = max(0, x2 - x1)
                box_h = max(0, y2 - y1)
                if box_w * box_h < (w * h) * self._min_box_fraction:
                    continue
                name = _VOC_INTERESTING_NAMES.get(cls_id, "object")
                if name not in tags:
                    tags.append(name)
        return bool(tags), sorted(tags)

    def last_class_names(self) -> List[str]:
        return ["person", "vehicle", "animal"]
