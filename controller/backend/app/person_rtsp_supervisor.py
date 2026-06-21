"""
Background RTSP readers + OpenCV person detection, broadcasting to ``DetectionsHub``.

Supervisor thread polls ``camera_store`` every few seconds, starts/stops per-camera worker
threads. Workers throttle inference with ``SMARTCAM_PERSON_DETECT_INTERVAL_MS``.

Concurrency: one worker thread per camera; each owns a ``VideoCapture`` and detector instance.
Process-wide RTSP env is applied before OpenCV loads (via ``opencv_person_detector`` import).

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
from .opencv_person_detector import OpenCVPersonDetector, ssd_model_files_present
from .rtsp_capture import apply_rtsp_env

logger = logging.getLogger(__name__)

# Import cv2 only after RTSP env (apply_rtsp_env already ran in opencv_person_detector)
import cv2  # noqa: E402


def person_detection_enabled() -> bool:
    v = os.environ.get("SMARTCAM_PERSON_DETECT_ENABLED", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _interval_seconds() -> float:
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


def _camera_worker(
    camera_id: int,
    rtsp_url_value: str,
    hub: DetectionsHub,
    local_stop: threading.Event,
    global_stop: threading.Event,
) -> None:
    apply_rtsp_env()
    det = OpenCVPersonDetector()
    if not det.available():
        logger.warning(
            "[person_rtsp] camera %s: SSD models missing under %s — run scripts/fetch_ssd_models.sh",
            camera_id,
            os.environ.get("SMARTCAM_MODEL_DIR", "controller/backend/models"),
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
    logger.info("[person_rtsp] camera %s: reader started", camera_id)
    while not local_stop.is_set() and not global_stop.is_set():
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(1.0)
            continue
        try:
            faces = det.detect_normalized(frame)
        except Exception as e:
            logger.exception("[person_rtsp] camera %s: detect error: %s", camera_id, e)
            faces = []

        person_count = sum(
            1 for d in faces if str(d.get("category") or d.get("label") or "").lower() == "person"
        )
        animal_count = sum(
            1 for d in faces if str(d.get("category") or "").lower() == "animal"
        )
        motion_count = person_count + animal_count

        now_ts = time.time()
        # Trigger/arm clip before buffering so the same frame can enter post-roll.
        on_person_detected(camera_id, motion_count, detected_at=now_ts)
        push_motion_buffer_frame(camera_id, frame, now_ts)

        ts = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "type": "detections",
            "camera_id": camera_id,
            "ts": ts,
            "faces": faces,
            "person_count": person_count,
            "person_detected": person_count > 0,
            "animal_count": animal_count,
            "animal_detected": animal_count > 0,
            "person_capture_busy": motion_capture_busy(camera_id),
            "person_record_eligible": person_record_eligible(camera_id),
            "person_trigger_streak": person_trigger_streak(camera_id),
            "person_trigger_min_frames": person_trigger_min_frames(),
            "hailo_ready": False,
            "backend": "opencv_ssd",
            "person_detection_source": "opencv_ssd",
        }
        hub.schedule_broadcast(payload)

        if local_stop.wait(interval):
            break

    cap.release()
    logger.info("[person_rtsp] camera %s: reader stopped", camera_id)


def _supervisor_loop(hub: DetectionsHub, stop: threading.Event) -> None:
    workers: Dict[int, Tuple[threading.Thread, threading.Event]] = {}
    warned_models = False
    try:
        while not stop.wait(2.0):
            if not person_detection_enabled():
                for cid, (th, ev) in list(workers.items()):
                    ev.set()
                    th.join(timeout=8.0)
                    del workers[cid]
                continue

            if not ssd_model_files_present():
                if not warned_models:
                    logger.warning(
                        "[person_rtsp] MobileNet-SSD weights not found — "
                        "install models (see controller/backend/scripts/fetch_ssd_models.sh)"
                    )
                    warned_models = True
                for cid, (th, ev) in list(workers.items()):
                    ev.set()
                    th.join(timeout=8.0)
                    del workers[cid]
                continue
            warned_models = False

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
