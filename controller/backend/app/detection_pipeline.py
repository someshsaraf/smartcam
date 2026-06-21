"""
SmartCam detection pipeline:

RTSP frame → MOG2 motion gate → Hailo YOLOv8n (OpenCV SSD fallback) → ByteTrack
→ consecutive-frame confirmation → events / WebSocket overlay.

One ``CameraDetectionPipeline`` per camera worker thread.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Protocol, Tuple

import numpy as np

from .byte_tracker import ByteTracker
from .hailo_yolov8_backend import HailoYolov8Detector, hailo_detector_diagnostics
from .mog2_motion_gate import Mog2MotionGate
from .opencv_person_detector import OpenCVPersonDetector, person_detector_diagnostics


class _DetectorBackend(Protocol):
    def available(self) -> bool: ...
    def detect_normalized(self, frame_bgr: np.ndarray) -> List[Dict[str, Any]]: ...
    def backend_name(self) -> str: ...


def _event_confirm_frames() -> int:
    raw = os.environ.get("SMARTCAM_EVENT_CONFIRM_FRAMES", "2").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 2
    return max(1, min(n, 30))


def _mog2_enabled() -> bool:
    v = os.environ.get("SMARTCAM_MOG2_ENABLED", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _create_detector() -> Tuple[_DetectorBackend, bool, Optional[str]]:
    hailo = HailoYolov8Detector.shared()
    if hailo.available():
        return hailo, True, None
    ssd = OpenCVPersonDetector()
    if ssd.available():
        return ssd, False, hailo.hailo_error()
    return ssd, False, hailo.hailo_error() or "no detector backend available"


def pipeline_diagnostics() -> Dict[str, Any]:
    hailo_diag = hailo_detector_diagnostics()
    ssd_diag = person_detector_diagnostics()
    hailo_ready = bool(hailo_diag.get("hailo_ready"))
    backend = "hailo_yolov8n" if hailo_ready else "opencv_ssd"
    return {
        "pipeline": "mog2_hailo_yolov8n_bytetrack_confirm",
        "mog2_enabled": _mog2_enabled(),
        "detection_fps_env": os.environ.get("SMARTCAM_DETECTION_FPS", "5"),
        "event_confirm_frames": _event_confirm_frames(),
        "backend": backend,
        "hailo_ready": hailo_ready,
        "hailo_error": hailo_diag.get("hailo_error"),
        "opencv_ssd_ready": bool(ssd_diag.get("model_load_ok")),
        "person_confidence_threshold": hailo_diag.get("person_confidence_threshold")
        or ssd_diag.get("confidence_threshold"),
        "animal_confidence_threshold": hailo_diag.get("animal_confidence_threshold")
        or ssd_diag.get("animal_confidence_threshold"),
        "hailo": hailo_diag,
        "opencv_ssd": ssd_diag,
    }


def _mog2_heartbeat_frames() -> int:
    """Run YOLO every N frames even when MOG2 sees no motion (catches static people)."""
    raw = os.environ.get("SMARTCAM_MOG2_HEARTBEAT_FRAMES", "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(0, min(n, 60))


class CameraDetectionPipeline:
    """Per-camera MOG2 → detect → track → confirm pipeline."""

    def __init__(self) -> None:
        self._mog2 = Mog2MotionGate()
        self._tracker = ByteTracker()
        self._detector, self._hailo_ready, self._hailo_error = _create_detector()
        self._confirm_frames = _event_confirm_frames()
        self._mog2_on = _mog2_enabled()
        self._heartbeat = _mog2_heartbeat_frames()
        self._frame_idx = 0
        self._last_motion = False

    @property
    def detector_available(self) -> bool:
        return self._detector.available()

    @property
    def hailo_ready(self) -> bool:
        return self._hailo_ready

    @property
    def hailo_error(self) -> Optional[str]:
        return self._hailo_error

    @property
    def backend_name(self) -> str:
        if self._hailo_ready:
            return "hailo_yolov8n"
        return "opencv_ssd"

    def process_frame(self, frame_bgr: np.ndarray) -> Dict[str, Any]:
        self._frame_idx += 1
        motion = True
        if self._mog2_on:
            motion = self._mog2.has_motion(frame_bgr)
        self._last_motion = motion

        heartbeat_infer = (
            self._heartbeat > 0 and self._frame_idx % self._heartbeat == 0
        )
        run_infer = motion or heartbeat_infer

        if not run_infer:
            tracked = self._tracker.update([], confirm_frames=self._confirm_frames)
            return self._result(
                tracked,
                motion=motion,
                inferred=False,
                raw_count=0,
                heartbeat_infer=heartbeat_infer,
            )

        raw = self._detector.detect_normalized(frame_bgr)
        tracked = self._tracker.update(raw, confirm_frames=self._confirm_frames)
        return self._result(
            tracked,
            motion=motion,
            inferred=True,
            raw_count=len(raw),
            heartbeat_infer=heartbeat_infer,
        )

    def _result(
        self,
        tracked: List[Dict[str, Any]],
        *,
        motion: bool,
        inferred: bool,
        raw_count: int = 0,
        heartbeat_infer: bool = False,
    ) -> Dict[str, Any]:
        confirmed = [d for d in tracked if d.get("confirmed")]
        person_all = sum(
            1
            for d in tracked
            if str(d.get("category") or d.get("label") or "").lower() == "person"
        )
        animal_all = sum(
            1
            for d in tracked
            if str(d.get("category") or "").lower() == "animal"
            or str(d.get("label") or "").lower()
            in ("bird", "cat", "cow", "dog", "horse", "sheep")
        )
        person_confirmed = sum(
            1
            for d in confirmed
            if str(d.get("category") or d.get("label") or "").lower() == "person"
        )
        animal_confirmed = sum(
            1
            for d in confirmed
            if str(d.get("category") or "").lower() == "animal"
            or str(d.get("label") or "").lower()
            in ("bird", "cat", "cow", "dog", "horse", "sheep")
        )
        return {
            "faces": tracked,
            "confirmed_faces": confirmed,
            "person_count": person_all,
            "animal_count": animal_all,
            "person_confirmed_count": person_confirmed,
            "animal_confirmed_count": animal_confirmed,
            "motion_detected": motion,
            "inferred": inferred,
            "heartbeat_infer": heartbeat_infer,
            "raw_detection_count": raw_count,
            "event_count": person_confirmed + animal_confirmed,
        }
