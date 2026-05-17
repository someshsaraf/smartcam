"""Hailo YOLOv8n backend for SmartCam live overlays.

Returns normalized boxes compatible with existing frontend overlays:
{"x": 0..1, "y": 0..1, "w": 0..1, "h": 0..1, "score": 0..1, "label": "person"}
"""
from __future__ import annotations

import logging
import os
import threading
import time
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _presence_confidence() -> float:
    """Floor for person-in-frame (profile / dim light). Side poses often score < 0.90."""
    return _env_float("SMARTCAM_PERSON_PRESENCE_CONFIDENCE", 0.62, 0.01, 0.99)


def _overlay_min_confidence() -> float:
    """Draw boxes at or above this score (defaults to presence, not 0.90)."""
    raw = os.environ.get("SMARTCAM_PERSON_OVERLAY_MIN_CONFIDENCE")
    if raw is not None and str(raw).strip() != "":
        return _env_float("SMARTCAM_PERSON_OVERLAY_MIN_CONFIDENCE", 0.62, 0.01, 0.99)
    return _presence_confidence()


def _box_plausible(box: dict[str, Any]) -> bool:
    w = float(box.get("w", 0.0))
    h = float(box.get("h", 0.0))
    if w <= 0.0 or h <= 0.0:
        return False
    area = w * h
    max_area = _env_float("SMARTCAM_PERSON_MAX_BOX_AREA", 0.42, 0.05, 0.95)
    if area < 1e-5 or area > max_area:
        return False
    ar = h / max(1e-6, w)
    return 0.12 <= ar <= 6.0


def _hailo_coord_order() -> str:
    """Corner order in each detection row: yxyx (Hailo postprocess default) or xyxy."""
    raw = os.environ.get("SMARTCAM_HAILO_COORD_ORDER", "yxyx").strip().lower()
    return "xyxy" if raw in ("xyxy", "xy", "x") else "yxyx"


def _corners_from_row(
    row: Sequence[float], input_size: int
) -> Optional[tuple[float, float, float, float]]:
    """Parse Hailo row to xmin, ymin, xmax, ymax in model-input normalized space."""
    if len(row) < 4:
        return None
    c0, c1, c2, c3 = (float(row[i]) for i in range(4))
    vals = [c0, c1, c2, c3]
    if max(vals) > 1.5:
        inv = 1.0 / float(input_size)
        vals = [v * inv for v in vals]
        c0, c1, c2, c3 = vals
    if not all(np.isfinite(v) for v in (c0, c1, c2, c3)):
        return None
    if _hailo_coord_order() == "yxyx":
        ymin, xmin, ymax, xmax = c0, c1, c2, c3
    else:
        xmin, ymin, xmax, ymax = c0, c1, c2, c3
    if xmax <= xmin or ymax <= ymin:
        return None
    return xmin, ymin, xmax, ymax


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


def _looks_like_per_class_detections(obj: list | tuple) -> bool:
    n = len(obj)
    if n < 1 or n > 120:
        return False
    for i in range(min(n, 4)):
        if not isinstance(obj[i], (list, tuple, np.ndarray)):
            return False
    return True


def _summarize_hailo_output(outputs: Any) -> str:
    parts: list[str] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 4 or len(parts) > 12:
            return
        if isinstance(obj, dict):
            parts.append(f"dict(keys={list(obj.keys())[:4]})")
            for v in list(obj.values())[:2]:
                walk(v, depth + 1)
        elif isinstance(obj, (list, tuple)):
            parts.append(f"list(len={len(obj)})")
            if len(obj) > PERSON_CLASS_ID:
                walk(obj[PERSON_CLASS_ID], depth + 1)
        else:
            try:
                arr = np.asarray(obj)
                if arr.size:
                    parts.append(f"ndarray{arr.shape} dtype={arr.dtype}")
            except Exception:
                parts.append(type(obj).__name__)

    walk(outputs)
    return "; ".join(parts) or "empty"


def _parse_person_rows(
    obj: Any,
    parsed: List[dict[str, Any]],
    det: "HailoYolov8Detector",
    *,
    score_min: float,
) -> None:
    try:
        arr = np.squeeze(np.asarray(obj, dtype=np.float32))
    except Exception:
        return
    if arr.size == 0:
        return
    if arr.ndim == 3 and arr.shape[-1] >= 5 and arr.shape[0] > PERSON_CLASS_ID:
        arr = arr[PERSON_CLASS_ID]
    if arr.ndim == 1 and arr.shape[0] >= 5:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2 or arr.shape[1] < 5:
        return
    for row in arr:
        score = float(row[4])
        if score > 1.0 and score <= 100.0:
            score /= 100.0
        if not np.isfinite(score) or score <= 0.0 or score < score_min:
            continue
        corners = _corners_from_row(row, det.input_size)
        if corners is None:
            continue
        box = det._row_to_frame_box(*corners, score)
        if box is None:
            continue
        if box["w"] * det._lb_src_w < det.min_box_px or box["h"] * det._lb_src_h < det.min_box_px:
            continue
        parsed.append(box)


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
        self._use_letterbox = _env_bool("SMARTCAM_HAILO_LETTERBOX", False)
        self._infer_lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._ready = False
        self._error: Optional[str] = hef_err
        self._hef = None
        self._target = None
        self._network_group = None
        self._input_vstreams_params = None
        self._output_vstreams_params = None
        self._input_name: Optional[str] = None
        self._InferVStreams = None
        self._lb_scale = 1.0
        self._lb_pad_x = 0
        self._lb_pad_y = 0
        self._lb_src_w = 1
        self._lb_src_h = 1
        self._hold_boxes: List[dict[str, Any]] = []
        self._hold_until = 0.0
        if hef_err:
            logger.error(hef_err)

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def ready(self) -> bool:
        return self._ready

    def _init(self) -> bool:
        if self._ready:
            return True
        with self._init_lock:
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
                elif "73" in err or "HAILO_DEVICE_IN_USE" in err:
                    self._error = (
                        f"Failed to open Hailo device: {e}. "
                        "Only one process may use the NPU. Stop other hailort/uvicorn instances, "
                        "do not run check_hailo.sh while the backend is running, then restart uvicorn."
                    )
                else:
                    self._error = f"Failed to initialize Hailo backend: {e}"
                logger.exception(self._error)
                return False

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Preprocess to model input. Letterbox (default) or stretch (baseline)."""
        h, w = frame_bgr.shape[:2]
        size = self.input_size
        self._lb_src_w = max(1, w)
        self._lb_src_h = max(1, h)
        if h < 2 or w < 2:
            self._lb_scale = 1.0
            self._lb_pad_x = 0
            self._lb_pad_y = 0
            return np.zeros((size, size, 3), dtype=np.uint8)

        if not self._use_letterbox:
            self._lb_scale = 1.0
            self._lb_pad_x = 0
            self._lb_pad_y = 0
            img = cv2.resize(frame_bgr, (size, size), interpolation=cv2.INTER_LINEAR)
            return np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), dtype=np.uint8)

        scale = min(size / float(w), size / float(h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        pad_x = (size - nw) // 2
        pad_y = (size - nh) // 2
        canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = rgb

        self._lb_scale = scale
        self._lb_pad_x = pad_x
        self._lb_pad_y = pad_y
        self._lb_src_w = w
        self._lb_src_h = h
        return np.ascontiguousarray(canvas, dtype=np.uint8)

    def _unmap_letterbox_corners(
        self, xmin: float, ymin: float, xmax: float, ymax: float, score: float
    ) -> Optional[dict[str, Any]]:
        """Map model-input normalized corners (letterboxed 640) to source-frame box."""
        s = float(self.input_size)
        x1p = (xmin * s - self._lb_pad_x) / self._lb_scale
        y1p = (ymin * s - self._lb_pad_y) / self._lb_scale
        x2p = (xmax * s - self._lb_pad_x) / self._lb_scale
        y2p = (ymax * s - self._lb_pad_y) / self._lb_scale
        return self._make_box(
            x1p / float(self._lb_src_w),
            y1p / float(self._lb_src_h),
            x2p / float(self._lb_src_w),
            y2p / float(self._lb_src_h),
            score,
        )

    def _row_to_frame_box(
        self, xmin: float, ymin: float, xmax: float, ymax: float, score: float
    ) -> Optional[dict[str, Any]]:
        """Map Hailo row corners to source-frame normalized box."""
        if self._use_letterbox:
            box = self._unmap_letterbox_corners(xmin, ymin, xmax, ymax, score)
        else:
            box = self._make_box(xmin, ymin, xmax, ymax, score)
        if box is None or not _box_plausible(box):
            return None
        return box

    def _infer(self, frame_bgr: np.ndarray) -> Optional[dict[str, Any]]:
        if not self._init():
            return None
        assert self._InferVStreams is not None
        assert self._network_group is not None
        assert self._input_vstreams_params is not None
        assert self._output_vstreams_params is not None
        assert self._input_name is not None
        input_data = {self._input_name: np.expand_dims(self._preprocess(frame_bgr), axis=0)}
        with self._infer_lock:
            with self._network_group.activate():
                with self._InferVStreams(self._network_group, self._input_vstreams_params, self._output_vstreams_params) as pipe:
                    return pipe.infer(input_data)

    def detect_people_normalized(self, frame_bgr: np.ndarray) -> List[dict[str, Any]]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        h, w = frame_bgr.shape[:2]
        if h < 2 or w < 2:
            return []
        presence_conf = _presence_confidence()
        overlay_min = _overlay_min_confidence()
        # Legacy env kept for diagnostics; presence drives detection.
        self.conf = _env_float("SMARTCAM_PERSON_CONFIDENCE", 0.90, 0.01, 0.99)
        hold_conf = _env_float(
            "SMARTCAM_PERSON_HOLD_CONFIDENCE",
            max(0.50, presence_conf - 0.10),
            0.01,
            0.99,
        )
        hold_sec = _env_float("SMARTCAM_PERSON_HOLD_SEC", 4.0, 0.0, 30.0)
        outputs = self._infer(frame_bgr)
        if outputs is None:
            return []
        boxes = [
            b
            for b in _nms(self._parse_outputs(outputs, score_min=presence_conf), self.nms_iou)
            if _box_plausible(b)
        ]
        now = time.monotonic()
        if boxes:
            self._hold_boxes = boxes
            self._hold_until = now + hold_sec
        elif self._hold_boxes and now <= self._hold_until:
            loose = [
                b
                for b in _nms(self._parse_outputs(outputs, score_min=hold_conf), self.nms_iou)
                if _box_plausible(b)
            ]
            held = [b for b in self._hold_boxes if _box_plausible(b)]
            boxes = loose if loose else held
        else:
            self._hold_boxes = []
            self._hold_until = 0.0
        boxes = [b for b in boxes if float(b.get("score", 0.0)) >= overlay_min]
        if (
            not boxes
            and os.environ.get("SMARTCAM_HAILO_PARSE_DEBUG", "").strip().lower()
            in ("1", "true", "yes", "on")
        ):
            logger.info("Hailo parse: 0 boxes; output summary=%s", _summarize_hailo_output(outputs))
        return boxes[: self.max_detections]

    def _parse_outputs(self, outputs: Any, *, score_min: Optional[float] = None) -> List[dict[str, Any]]:
        threshold = self.conf if score_min is None else score_min
        """Parse HailoRT-postprocess YOLO (y0,x0,y1,x1,score per class) and legacy tensor layouts."""
        parsed: List[dict[str, Any]] = []
        visited: set[int] = set()

        def consume(obj: Any) -> None:
            if obj is None:
                return
            oid = id(obj)
            if oid in visited:
                return
            visited.add(oid)

            if isinstance(obj, dict):
                for v in obj.values():
                    consume(v)
                return

            if isinstance(obj, (list, tuple)) and _looks_like_per_class_detections(obj):
                if len(obj) > PERSON_CLASS_ID:
                    _parse_person_rows(obj[PERSON_CLASS_ID], parsed, self, score_min=threshold)
                return

            if isinstance(obj, (list, tuple)):
                for v in obj:
                    consume(v)
                return

            _parse_person_rows(obj, parsed, self, score_min=threshold)

        consume(outputs)
        if not parsed:
            parsed.extend(self._parse_outputs_legacy(outputs, score_min=threshold))
        return parsed

    def _parse_outputs_legacy(self, outputs: Any, *, score_min: Optional[float] = None) -> List[dict[str, Any]]:
        threshold = self.conf if score_min is None else score_min
        """Fallback for non-postprocess tensors (xyxy + optional class column)."""
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
                if score > 1.0 and score <= 100.0:
                    score /= 100.0
                if not np.isfinite(score) or score < threshold:
                    continue
                corners = _corners_from_row(vals[:4], self.input_size)
                if corners is None:
                    continue
                box = self._row_to_frame_box(*corners, score)
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
    """Release Hailo handles (call only when live detection workers are stopped)."""
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is not None:
            with _DETECTOR._init_lock:
                _release_vdevice(_DETECTOR._target)
                _DETECTOR._target = None
                _DETECTOR._network_group = None
                _DETECTOR._hef = None
                _DETECTOR._ready = False
                _DETECTOR._error = None
        _DETECTOR = None


def warm_up_hailo_backend() -> Optional[str]:
    """Open Hailo once on the main thread before RTSP workers start."""
    det = get_detector()
    if det._init():
        return None
    return det.error


def get_detector() -> HailoYolov8Detector:
    global _DETECTOR
    with _DETECTOR_LOCK:
        if _DETECTOR is None:
            _DETECTOR = HailoYolov8Detector()
        return _DETECTOR


def detect_people_normalized(frame_bgr: np.ndarray) -> List[dict[str, Any]]:
    return get_detector().detect_people_normalized(frame_bgr)
