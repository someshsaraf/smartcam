"""
Background RTSP readers + SmartCam detection pipeline, broadcasting to ``DetectionsHub``.

Pipeline: MOG2 → Hailo YOLOv8n (OpenCV SSD fallback) → ByteTrack → frame confirmation → event.

Supervisor thread polls ``camera_store`` every few seconds, starts/stops per-camera worker
threads. Workers throttle inference with ``SMARTCAM_DETECTION_FPS`` (default 5 Hz).

Concurrency: one worker thread per camera; each owns a ``VideoCapture`` and pipeline instance.
Process-wide RTSP env is applied before OpenCV loads.

Security: only ``rtsp://`` / ``rtsps://`` URLs from ``camera_store`` are opened.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from . import camera_store
from .detection_pipeline import CameraDetectionPipeline, pipeline_diagnostics
from .detections_hub import DetectionsHub
from .mediamtx_paths import rtsp_url
from .motion_recording import (
    motion_capture_busy,
    on_person_detected,
    person_record_eligible,
    person_trigger_streak,
    person_trigger_min_frames,
    push_motion_buffer_frame,
    reset_person_trigger_state,
)
from .rtsp_capture import apply_rtsp_env

logger = logging.getLogger(__name__)

# Import cv2 only after RTSP env (apply_rtsp_env already ran in opencv_person_detector)
import cv2  # noqa: E402


def person_detection_enabled() -> bool:
    v = os.environ.get("SMARTCAM_PERSON_DETECT_ENABLED", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _interval_seconds() -> float:
    fps_raw = os.environ.get("SMARTCAM_DETECTION_FPS", "").strip()
    if fps_raw:
        try:
            fps = float(fps_raw)
        except ValueError:
            fps = 5.0
        fps = max(0.2, min(fps, 30.0))
        return 1.0 / fps
    try:
        ms = int(os.environ.get("SMARTCAM_PERSON_DETECT_INTERVAL_MS", "200").strip())
    except ValueError:
        ms = 200
    ms = max(50, min(ms, 5000))
    return ms / 1000.0


def _wanted_cameras() -> Dict[int, str]:
    out: Dict[int, str] = {}
    for row in camera_store.list_cameras():
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get("id", -1))
        except (TypeError, ValueError):
            continue
        if cid < 0:
            continue
        url = rtsp_url(row)
        if not url.startswith(("rtsp://", "rtsps://")):
            continue
        out[cid] = url
    return out


def _detector_ready() -> bool:
    diag = pipeline_diagnostics()
    if diag.get("hailo_ready"):
        return True
    return bool(diag.get("opencv_ssd_ready"))


def _camera_worker(
    camera_id: int,
    rtsp_url_value: str,
    hub: DetectionsHub,
    local_stop: threading.Event,
    global_stop: threading.Event,
) -> None:
    apply_rtsp_env()
    pipeline = CameraDetectionPipeline()
    if not pipeline.detector_available:
        logger.warning(
            "[person_rtsp] camera %s: no detector backend (Hailo HEF or OpenCV SSD required)",
            camera_id,
        )
        return

    cap = cv2.VideoCapture(rtsp_url_value, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    if not cap.isOpened():
        logger.error("[person_rtsp] camera %s: failed to open RTSP", camera_id)
        return

    reset_person_trigger_state(camera_id)
    interval = _interval_seconds()
    logger.info(
        "[person_rtsp] camera %s: pipeline started (backend=%s, interval=%.2fs)",
        camera_id,
        pipeline.backend_name,
        interval,
    )
    while not local_stop.is_set() and not global_stop.is_set():
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(1.0)
            continue
        try:
            result = pipeline.process_frame(frame)
        except Exception as e:
            logger.exception("[person_rtsp] camera %s: pipeline error: %s", camera_id, e)
            result = {
                "faces": [],
                "person_count": 0,
                "animal_count": 0,
                "person_confirmed_count": 0,
                "animal_confirmed_count": 0,
                "event_count": 0,
                "motion_detected": False,
                "inferred": False,
            }

        person_count = int(result.get("person_count") or 0)
        animal_count = int(result.get("animal_count") or 0)
        event_count = int(result.get("event_count") or 0)

        now_ts = time.time()
        # Pipeline already applies ByteTrack + SMARTCAM_EVENT_CONFIRM_FRAMES.
        on_person_detected(camera_id, 1 if event_count > 0 else 0, detected_at=now_ts)
        if result.get("motion_detected"):
            push_motion_buffer_frame(camera_id, frame, now_ts)

        ts = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "type": "detections",
            "camera_id": camera_id,
            "ts": ts,
            "faces": result.get("faces") or [],
            "person_count": person_count,
            "person_detected": person_count > 0,
            "animal_count": animal_count,
            "animal_detected": animal_count > 0,
            "person_confirmed_count": int(result.get("person_confirmed_count") or 0),
            "animal_confirmed_count": int(result.get("animal_confirmed_count") or 0),
            "event_count": event_count,
            "motion_detected": bool(result.get("motion_detected")),
            "inferred": bool(result.get("inferred")),
            "heartbeat_infer": bool(result.get("heartbeat_infer")),
            "raw_detection_count": int(result.get("raw_detection_count") or 0),
            "person_capture_busy": motion_capture_busy(camera_id),
            "person_record_eligible": person_record_eligible(camera_id),
            "person_trigger_streak": person_trigger_streak(camera_id),
            "person_trigger_min_frames": person_trigger_min_frames(),
            "hailo_ready": pipeline.hailo_ready,
            "hailo_error": pipeline.hailo_error,
            "backend": pipeline.backend_name,
            "person_detection_source": pipeline.backend_name,
        }
        hub.schedule_broadcast(payload)

        if local_stop.wait(interval):
            break

    cap.release()
    logger.info("[person_rtsp] camera %s: reader stopped", camera_id)


def _supervisor_loop(hub: DetectionsHub, stop: threading.Event) -> None:
    workers: Dict[int, Tuple[threading.Thread, threading.Event]] = {}
    warned_backend = False
    try:
        while not stop.wait(2.0):
            if not person_detection_enabled():
                for cid, (th, ev) in list(workers.items()):
                    ev.set()
                    th.join(timeout=8.0)
                    del workers[cid]
                continue

            if not _detector_ready():
                if not warned_backend:
                    diag = pipeline_diagnostics()
                    logger.warning(
                        "[person_rtsp] no detector backend ready (hailo=%s, ssd=%s) — "
                        "install yolov8n.hef + hailo_platform or OpenCV SSD weights",
                        diag.get("hailo_ready"),
                        diag.get("opencv_ssd_ready"),
                    )
                    warned_backend = True
                for cid, (th, ev) in list(workers.items()):
                    ev.set()
                    th.join(timeout=8.0)
                    del workers[cid]
                continue
            warned_backend = False

            want = _wanted_cameras()
            for cid, (th, ev) in list(workers.items()):
                if cid not in want:
                    ev.set()
                    th.join(timeout=8.0)
                    del workers[cid]

            for cid, url in want.items():
                if cid in workers:
                    continue
                ev = threading.Event()
                th = threading.Thread(
                    target=_camera_worker,
                    args=(cid, url, hub, ev, stop),
                    name=f"person-rtsp-cam{cid}",
                    daemon=True,
                )
                th.start()
                workers[cid] = (th, ev)
    finally:
        for _, (th, ev) in list(workers.items()):
            ev.set()
        for _, (th, ev) in list(workers.items()):
            th.join(timeout=8.0)
        workers.clear()


_supervisor_thread: Optional[threading.Thread] = None


def start_person_detection_background(hub: DetectionsHub, stop: threading.Event) -> None:
    global _supervisor_thread
    if _supervisor_thread is not None and _supervisor_thread.is_alive():
        return
    t = threading.Thread(
        target=_supervisor_loop,
        args=(hub, stop),
        name="person-detection-supervisor",
        daemon=True,
    )
    t.start()
    _supervisor_thread = t


def get_supervisor_thread() -> Optional[threading.Thread]:
    return _supervisor_thread
