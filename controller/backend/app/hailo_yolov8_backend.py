"""Hailo YOLOv8n backend for SmartCam live overlays.

Returns normalized boxes compatible with existing frontend overlays:
{"x": 0..1, "y": 0..1, "w": 0..1, "h": 0..1, "score": 0..1, "label": "person"}
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, List, Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)
PERSON_CLASS_ID = 0
REQUIRED_HEF_BASENAME = "yolov8n.hef"


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


def _default_hef_path() -> str:
    here = Path(__file__).resolve()
    return str((here.parents[1] / "models" / REQUIRED_HEF_BASENAME).resolve())


def _resolve_hef_path(raw: str) -> tuple[str, Optional[str]]:
    """Return (absolute path, error). Only yolov8n.hef is permitted."""
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        backend_root = Path(__file__).resolve().parents[1]
        p = (backend_root / p).resolve()
    if p.name != REQUIRED_HEF_BASENAME:
        return (
            str(p),
            f"SMARTCAM_HAILO_HEF_PATH must be {REQUIRED_HEF_BASENAME} (YOLOv8n on Hailo), not {p.name}",
        )
    if not p.is_file():
        return (
            str(p),
            f"{REQUIRED_HEF_BASENAME} not found at {p} — copy compiled HEF to controller/backend/models/",
        )
    return str(p), None


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["w"]), ay1 + float(a["h"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["w"]), by1 + float(b["h"])
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def _scan_hailo_device_ids() -> list[str]:
    """Resolve PCIe device id(s). Empty VDevice(device_ids) causes error 74 on many systems."""
    override = os.environ.get("SMARTCAM_HAILO_DEVICE_ID", "").strip()
    if override:
        return [override]
    from hailo_platform import Device  # type: ignore

    ids = Device.scan()
    return list(ids) if ids else []


def _release_vdevice(target: Any) -> None:
    if target is None:
        return
    try:
        target.release()
    except Exception:
        pass


def _nms(boxes: List[dict[str, Any]], iou_thr: float) -> List[dict[str, Any]]:
    boxes = sorted(boxes, key=lambda d: float(d.get("score", 0.0)), reverse=True)
    kept: List[dict[str, Any]] = []
    for b in boxes:
        if all(_iou(b, k) < iou_thr for k in kept):
            kept.append(b)
    return kept


class HailoYolov8Detector:
    def __init__(self) -> None:
        raw_hef = os.environ.get("SMARTCAM_HAILO_HEF_PATH", _default_hef_path())
        self.hef_path, hef_err = _resolve_hef_path(raw_hef)
        self.input_size = _env_int("SMARTCAM_HAILO_INPUT_SIZE", 640, 64, 2048)
        self.conf = _env_float("SMARTCAM_PERSON_CONFIDENCE", 0.90, 0.01, 0.99)
        self.nms_iou = _env_float("SMARTCAM_PERSON_NMS_IOU", 0.45, 0.01, 0.99)
        self.min_box_px = _env_int("SMARTCAM_PERSON_MIN_BOX_PX", 24, 1, 4096)
        self.max_detections = _env_int("SMARTCAM_PERSON_MAX_DETECTIONS", 24, 1, 256)
        self._lock = threading.Lock()
        self._ready = False
        self._error: Optional[str] = hef_err
        self._hef = None
        self._target = None
        self._network_group = None
        self._input_vstreams_params = None
        self._output_vstreams_params = None
        self._input_name: Optional[str] = None
        self._InferVStreams = None
        if hef_err:
            logger.error(hef_err)

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _init(self) -> bool:
        if self._ready:
            return True
        if self._error:
            return False
        self.hef_path, hef_err = _resolve_hef_path(
            os.environ.get("SMARTCAM_HAILO_HEF_PATH", _default_hef_path())
        )
        if hef_err:
            self._error = hef_err
            logger.error(self._error)
            return False
        with self._lock:
            if self._ready:
                return True
            try:
                from hailo_platform import (  # type: ignore
                    ConfigureParams,
                    HEF,
                    HailoStreamInterface,
                    InferVStreams,
                    InputVStreamParams,
                    OutputVStreamParams,
                    VDevice,
                )
            except Exception as e:
                self._error = f"HailoRT Python bindings missing: {e}"
                logger.error(self._error)
                return False

            target: Any = None
            try:
                self._hef = HEF(self.hef_path)
                device_ids = _scan_hailo_device_ids()
                if not device_ids:
                    self._error = (
                        "HailoRT Device.scan() found no devices. If hailortcli works, set "
                        "SMARTCAM_HAILO_DEVICE_ID=0001:01:00.0 (from hailortcli identify) and "
                        "restart uvicorn."
                    )
                    logger.error(self._error)
                    return False
                logger.info("Opening Hailo VDevice device_ids=%s hef=%s", device_ids, self.hef_path)
                target = VDevice(device_ids=device_ids)
                self._target = target
                configure_params = ConfigureParams.create_from_hef(
                    hef=self._hef,
                    interface=HailoStreamInterface.PCIe,
                )
                network_groups = target.configure(self._hef, configure_params)
                if not network_groups:
                    raise RuntimeError("Hailo configure returned no network groups")
                self._network_group = network_groups[0]
                self._input_vstreams_params = InputVStreamParams.make(self._network_group)
                self._output_vstreams_params = OutputVStreamParams.make(self._network_group)
                in_infos = self._hef.get_input_vstream_infos()
                if not in_infos:
                    raise RuntimeError("HEF has no input vstreams")
                self._input_name = in_infos[0].name
                self._InferVStreams = InferVStreams
                self._ready = True
                self._error = None
                logger.info(
                    "Hailo YOLOv8 ready devices=%s hef=%s input=%s",
                    device_ids,
                    self.hef_path,
                    self._input_name,
                )
                return True
            except Exception as e:
                _release_vdevice(target)
                self._target = None
                self._hef = None
                err = str(e)
                if "74" in err or "HAILO_OUT_OF_PHYSICAL_DEVICES" in err:
                    self._error = (
                        f"Failed to open Hailo device: {e}. "
                        "Stop other Hailo apps, run: hailortcli fw-control identify, "
                        "then set SMARTCAM_HAILO_DEVICE_ID to that PCIe id (e.g. 0001:01:00.0)."
                    )
                else:
                    self._error = f"Failed to initialize Hailo backend: {e}"
                logger.exception(self._error)
                return False

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        img = cv2.resize(frame_bgr, (self.input_size, self.input_size), interpolation=cv2.INTER_LINEAR)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return np.ascontiguousarray(img, dtype=np.uint8)

    def _infer(self, frame_bgr: np.ndarray) -> Optional[dict[str, Any]]:
        if not self._init():
            return None
        assert self._InferVStreams is not None
        assert self._network_group is not None
        assert self._input_vstreams_params is not None
        assert self._output_vstreams_params is not None
        assert self._input_name is not None
        input_data = {self._input_name: np.expand_dims(self._preprocess(frame_bgr), axis=0)}
        with self._lock:
            with self._network_group.activate():
                with self._InferVStreams(self._network_group, self._input_vstreams_params, self._output_vstreams_params) as pipe:
                    return pipe.infer(input_data)

    def detect_people_normalized(self, frame_bgr: np.ndarray) -> List[dict[str, Any]]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        h, w = frame_bgr.shape[:2]
        if h < 2 or w < 2:
            return []
        outputs = self._infer(frame_bgr)
        if outputs is None:
            return []
        boxes = _nms(self._parse_outputs(outputs), self.nms_iou)
        return boxes[: self.max_detections]

    def _parse_outputs(self, outputs: Any) -> List[dict[str, Any]]:
        candidates: list[np.ndarray] = []

        def collect(obj: Any) -> None:
            if obj is None:
                return
            if isinstance(obj, dict):
                for v in obj.values():
                    collect(v)
                return
            if isinstance(obj, (list, tuple)):
                for v in obj:
                    collect(v)
                return
            try:
                arr = np.asarray(obj)
            except Exception:
                return
            if arr.size:
                candidates.append(arr)

        collect(outputs)
        parsed: List[dict[str, Any]] = []
        for arr in candidates:
            arr = np.squeeze(arr)
            # Common Hailo YOLO NMS can be (classes, detections, 5) or
            # (batch, classes, detections, 5). Keep COCO class 0 = person.
            if arr.ndim == 4 and arr.shape[-1] >= 5 and arr.shape[1] > PERSON_CLASS_ID:
                arr = arr[0, PERSON_CLASS_ID, :, :]
            elif arr.ndim == 3 and arr.shape[-1] >= 5 and arr.shape[0] > PERSON_CLASS_ID:
                arr = arr[PERSON_CLASS_ID, :, :]
            elif arr.ndim > 2 and arr.shape[-1] >= 5:
                arr = arr.reshape(-1, arr.shape[-1])
            if arr.ndim == 1 and arr.shape[0] in (5, 6):
                arr = arr.reshape(1, -1)
            if arr.ndim != 2 or arr.shape[1] < 5:
                continue
            for row in arr:
                vals = [float(x) for x in row[:6]]
                if len(vals) >= 6 and int(round(vals[5])) != PERSON_CLASS_ID:
                    continue
                score = vals[4]
                if not np.isfinite(score) or score < self.conf:
                    continue
                box = self._coords_to_box(vals[:4], score)
                if box is None:
                    continue
                if box["w"] * self.input_size < self.min_box_px or box["h"] * self.input_size < self.min_box_px:
                    continue
                parsed.append(box)
        return parsed

    def _coords_to_box(self, coords: Sequence[float], score: float) -> Optional[dict[str, Any]]:
        c = list(coords)
        if not all(np.isfinite(c)):
            return None
        if max(c) > 2.0:
            c = [v / float(self.input_size) for v in c]
        x1, y1, x2, y2 = c
        xy = self._make_box(x1, y1, x2, y2, score)
        yx = self._make_box(y1, x1, y2, x2, score)
        if xy is None:
            return yx
        if yx is None:
            return xy
        xy_ratio = xy["h"] / max(1e-6, xy["w"])
        yx_ratio = yx["h"] / max(1e-6, yx["w"])
        return yx if yx_ratio > xy_ratio and yx_ratio > 1.0 else xy

    def _make_box(self, x1: float, y1: float, x2: float, y2: float, score: float) -> Optional[dict[str, Any]]:
        x1, y1, x2, y2 = _clip01(x1), _clip01(y1), _clip01(x2), _clip01(y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return {"x": round(x1, 6), "y": round(y1, 6), "w": round(x2 - x1, 6), "h": round(y2 - y1, 6), "score": round(float(score), 4), "label": "person", "source": "hailo_yolov8n"}


_DETECTOR: Optional[HailoYolov8Detector] = None
_DETECTOR_LOCK = threading.Lock()


def reset_detector_cache() -> None:
    """Release Hailo handles so the next request can re-open the device (e.g. after error 74)."""
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is not None:
            _release_vdevice(_DETECTOR._target)
            _DETECTOR._target = None
            _DETECTOR._ready = False
        _DETECTOR = None


def get_detector() -> HailoYolov8Detector:
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is None:
            _DETECTOR = HailoYolov8Detector()
        return _DETECTOR


def detect_people_normalized(frame_bgr: np.ndarray) -> List[dict[str, Any]]:
    return get_detector().detect_people_normalized(frame_bgr)
