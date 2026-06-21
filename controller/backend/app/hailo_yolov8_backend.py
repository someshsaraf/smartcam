"""
Hailo YOLOv8n person/animal detection for SmartCam controller.

Expects a HEF compiled with HailoRT NMS postprocess (standard yolov8n.hef).
Falls back gracefully when ``hailo_platform`` or the HEF file is unavailable.

Concurrency: use one shared ``HailoYolov8Detector`` instance; inference is serialized
with a process-wide lock because the Hailo VDevice is shared.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# COCO class indices we care about (yolov8n HEF with NMS)
_COCO_PERSON = 0
_COCO_ANIMALS: Dict[int, str] = {
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
}
_COCO_ALLOWED = {_COCO_PERSON, *_COCO_ANIMALS.keys()}

_INFER_LOCK = threading.Lock()
_SHARED: Optional["HailoYolov8Detector"] = None
_SHARED_LOCK = threading.Lock()


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _hef_path() -> Path:
    raw = os.environ.get("SMARTCAM_HAILO_HEF_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _backend_dir() / "models" / "yolov8n.hef"


def _input_size() -> int:
    raw = os.environ.get("SMARTCAM_HAILO_INPUT_SIZE", "640").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 640
    return max(320, min(n, 1280))


def _parse_float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    v = float(str(raw).strip())
    if v < lo or v > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


def _letterbox(
    frame_bgr: np.ndarray, size: int
) -> Tuple[np.ndarray, float, float, float]:
    """Resize with aspect ratio preserved; return RGB float32 NHWC and scale factors."""
    h, w = frame_bgr.shape[:2]
    if h < 2 or w < 2:
        raise ValueError("frame too small")
    scale = min(size / w, size / h)
    nw = int(round(w * scale))
    nh = int(round(h * scale))
    resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    pad_x = (size - nw) // 2
    pad_y = (size - nh) // 2
    canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32)
    return rgb, scale, float(pad_x), float(pad_y)


def _box_to_normalized(
    y1: float,
    x1: float,
    y2: float,
    x2: float,
    frame_w: int,
    frame_h: int,
    scale: float,
    pad_x: float,
    pad_y: float,
) -> Optional[Dict[str, float]]:
    """Map model-space box back to normalized 0–1 coords on the original frame."""
    if scale <= 0:
        return None
    ox1 = (x1 - pad_x) / scale
    oy1 = (y1 - pad_y) / scale
    ox2 = (x2 - pad_x) / scale
    oy2 = (y2 - pad_y) / scale
    ox1 = max(0.0, min(float(frame_w), ox1))
    oy1 = max(0.0, min(float(frame_h), oy1))
    ox2 = max(0.0, min(float(frame_w), ox2))
    oy2 = max(0.0, min(float(frame_h), oy2))
    bw = ox2 - ox1
    bh = oy2 - oy1
    if bw <= 1.0 or bh <= 1.0:
        return None
    return {
        "x": ox1 / frame_w,
        "y": oy1 / frame_h,
        "w": bw / frame_w,
        "h": bh / frame_h,
    }


class HailoYolov8Detector:
    """HailoRT sync inference for YOLOv8n with built-in NMS."""

    def __init__(self) -> None:
        self._input_size = _input_size()
        try:
            self._person_conf = _parse_float_env(
                "SMARTCAM_PERSON_CONFIDENCE", 0.25, 0.0, 1.0
            )
            self._animal_conf = _parse_float_env(
                "SMARTCAM_ANIMAL_CONFIDENCE", 0.20, 0.0, 1.0
            )
        except ValueError:
            self._person_conf = 0.25
            self._animal_conf = 0.20
        self._hef_path = _hef_path()
        self._ready = False
        self._error: Optional[str] = None
        self._input_name: Optional[str] = None
        self._output_name: Optional[str] = None
        self._target: Any = None
        self._network_group: Any = None
        self._network_group_params: Any = None
        self._input_params: Any = None
        self._output_params: Any = None
        self._infer_pipeline: Any = None
        self._init_runtime()

    @classmethod
    def shared(cls) -> "HailoYolov8Detector":
        global _SHARED
        with _SHARED_LOCK:
            if _SHARED is None:
                _SHARED = cls()
            return _SHARED

    def _init_runtime(self) -> None:
        if not self._hef_path.is_file():
            self._error = f"HEF not found: {self._hef_path}"
            return
        try:
            from hailo_platform import (  # type: ignore
                ConfigureParams,
                FormatType,
                HEF,
                HailoStreamInterface,
                InferVStreams,
                InputVStreamParams,
                OutputVStreamParams,
                VDevice,
            )
        except ImportError as e:
            self._error = f"hailo_platform not installed: {e}"
            return
        try:
            hef = HEF(str(self._hef_path))
            self._target = VDevice()
            configure_params = ConfigureParams.create_from_hef(
                hef, interface=HailoStreamInterface.PCIe
            )
            network_groups = self._target.configure(hef, configure_params)
            self._network_group = network_groups[0]
            self._network_group_params = self._network_group.create_params()
            self._input_params = InputVStreamParams.make_from_network_group(
                self._network_group,
                quantized=False,
                format_type=FormatType.FLOAT32,
            )
            self._output_params = OutputVStreamParams.make_from_network_group(
                self._network_group,
                quantized=False,
                format_type=FormatType.FLOAT32,
            )
            infos_in = hef.get_input_vstream_infos()
            infos_out = hef.get_output_vstream_infos()
            if not infos_in or not infos_out:
                self._error = "HEF missing input/output vstream infos"
                return
            self._input_name = infos_in[0].name
            self._output_name = infos_out[0].name
            self._network_group.activate(self._network_group_params)
            self._infer_pipeline = InferVStreams(
                self._network_group,
                self._input_params,
                self._output_params,
            )
            self._infer_pipeline.__enter__()
            self._ready = True
        except Exception as e:
            self._error = str(e)
            self._ready = False

    def available(self) -> bool:
        return self._ready

    def hailo_ready(self) -> bool:
        return self._ready

    def hailo_error(self) -> Optional[str]:
        return self._error

    def backend_name(self) -> str:
        return "hailo_yolov8n"

    def detect_normalized(self, frame_bgr: np.ndarray) -> List[Dict[str, Any]]:
        if not self._ready or frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            return []
        h, w = frame_bgr.shape[:2]
        if w < 2 or h < 2:
            return []
        try:
            rgb, scale, pad_x, pad_y = _letterbox(frame_bgr, self._input_size)
        except ValueError:
            return []
        input_data = np.expand_dims(rgb, axis=0)
        with _INFER_LOCK:
            try:
                raw = self._infer_pipeline.infer({self._input_name: input_data})
            except Exception as e:
                self._error = str(e)
                self._ready = False
                return []
        return self._parse_nms_output(raw, w, h, scale, pad_x, pad_y)

    def _parse_nms_output(
        self,
        raw: Dict[str, Any],
        frame_w: int,
        frame_h: int,
        scale: float,
        pad_x: float,
        pad_y: float,
    ) -> List[Dict[str, Any]]:
        if not raw:
            return []
        key = self._output_name or next(iter(raw.keys()))
        batch = raw.get(key)
        if batch is None:
            return []
        arr = np.asarray(batch)
        if arr.ndim == 4:
            arr = arr[0]
        if arr.ndim != 3 or arr.shape[-1] < 5:
            return []
        out: List[Dict[str, Any]] = []
        for cls_idx, cls_dets in enumerate(arr):
            if cls_idx not in _COCO_ALLOWED:
                continue
            is_person = cls_idx == _COCO_PERSON
            min_conf = self._person_conf if is_person else self._animal_conf
            label = "person" if is_person else _COCO_ANIMALS.get(cls_idx, "animal")
            for det in cls_dets:
                score = float(det[4])
                if score < min_conf:
                    continue
                y1, x1, y2, x2 = float(det[0]), float(det[1]), float(det[2]), float(det[3])
                norm = _box_to_normalized(
                    y1, x1, y2, x2, frame_w, frame_h, scale, pad_x, pad_y
                )
                if norm is None:
                    continue
                out.append(
                    {
                        **norm,
                        "label": label,
                        "category": "person" if is_person else "animal",
                        "score": round(score, 4),
                    }
                )
        return out


def hailo_detector_diagnostics() -> Dict[str, Any]:
    det = HailoYolov8Detector.shared()
    return {
        "hailo_ready": det.hailo_ready(),
        "hailo_error": det.hailo_error(),
        "hailo_hef_path": str(_hef_path()),
        "hailo_hef_present": _hef_path().is_file(),
        "hailo_input_size": _input_size(),
        "person_confidence_threshold": det._person_conf,
        "animal_confidence_threshold": det._animal_conf,
        "backend": det.backend_name() if det.hailo_ready() else None,
    }
