"""Per-camera RTSP decode + face detection; WebSocket fan-out for Phase 1 UI overlays."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Optional, Tuple

import cv2
import numpy as np

from . import camera_store
from . import mediamtx_manager
from .face_backend import detect_faces_normalized, inference_debug_status
from .motion_recording import schedule_motion_clip_trigger
from .rtsp_env import apply_rtsp_env

apply_rtsp_env()

logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_positive_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _overlay_delay_ms() -> int:
    """Run inference on RTSP frames this many ms behind live (aligns with HLS playback)."""
    return _parse_positive_int("SMARTCAM_DETECTION_OVERLAY_DELAY_MS", 6500, 0, 15000)


def _parse_float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _person_hold_sec() -> float:
    """Keep last person detection visible this long after a miss (reduces flicker)."""
    return _parse_float_env("SMARTCAM_PERSON_HOLD_SEC", 2.5, 0.0, 30.0)


def _people_from_faces(faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        f
        for f in faces
        if isinstance(f, dict) and str(f.get("label", "")).lower() == "person"
    ]


class _DelayedFrameBuffer:
    """Hold recent frames; return the newest frame at least delay_sec old."""

    def __init__(self, delay_sec: float, *, max_frames: Optional[int] = None) -> None:
        self._delay_sec = max(0.0, float(delay_sec))
        if max_frames is not None:
            cap = max(16, min(400, int(max_frames)))
        else:
            # deque maxlen must exceed delay * fps or oldest frames are dropped too soon.
            cap = max(48, int(self._delay_sec * 30 * 2) + 20)
            cap = min(cap, 400)
        self._maxlen = cap
        self._frames: Deque[Tuple[float, np.ndarray]] = deque(maxlen=cap)
        self._zero_delay_latest: Optional[np.ndarray] = None

    def push(self, frame: np.ndarray) -> None:
        if frame is None or frame.size == 0:
            return
        if self._delay_sec <= 0.0:
            self._zero_delay_latest = frame
            return
        self._frames.append((time.monotonic(), frame.copy()))

    def oldest_age_ms(self) -> int:
        if not self._frames:
            return 0
        return int(max(0.0, (time.monotonic() - self._frames[0][0]) * 1000))

    def frame_for_inference(self) -> Optional[np.ndarray]:
        if self._delay_sec <= 0.0:
            return self._zero_delay_latest
        if not self._frames:
            return None
        now = time.monotonic()
        chosen: Optional[np.ndarray] = None
        chosen_t = -1.0
        for ts, frame in self._frames:
            if now - ts >= self._delay_sec and ts > chosen_t:
                chosen = frame
                chosen_t = ts
        return chosen


class DetectionWsHub:
    """Broadcast detection JSON from worker threads to WS clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: list[Any] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, websocket: Any) -> None:
        with self._lock:
            self._clients.append(websocket)

    async def unregister(self, websocket: Any) -> None:
        with self._lock:
            if websocket in self._clients:
                self._clients.remove(websocket)

    def broadcast_json(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return

        async def _send_all() -> None:
            with self._lock:
                clients = list(self._clients)
            dead: list[Any] = []
            for ws in clients:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            if dead:
                with self._lock:
                    for ws in dead:
                        if ws in self._clients:
                            self._clients.remove(ws)

        try:
            asyncio.run_coroutine_threadsafe(_send_all(), loop)
        except RuntimeError:
            pass


class _CameraWorker(threading.Thread):
    def __init__(
        self,
        *,
        cam: dict[str, Any],
        rtsp_url: str,
        hub: DetectionWsHub,
        interval_frames: int,
        min_interval_sec: float,
        inference_delay_sec: float,
    ) -> None:
        super().__init__(daemon=True)
        self._cam = cam
        self._rtsp_url = rtsp_url
        self._hub = hub
        self._interval_frames = max(1, interval_frames)
        self._min_interval_sec = max(0.05, min_interval_sec)
        self._inference_delay_sec = max(0.0, float(inference_delay_sec))
        self._frame_buffer = _DelayedFrameBuffer(self._inference_delay_sec)
        self._person_hold_sec = _person_hold_sec()
        self._held_snapshot: Optional[tuple[float, list[dict[str, Any]], list[dict[str, Any]]]] = None
        self._prev_person_detected = False
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        cid = int(self._cam["id"])
        cap: Optional[cv2.VideoCapture] = None
        frame_i = 0
        last_send = 0.0
        last_buffer_status = 0.0
        delay_ms = int(self._inference_delay_sec * 1000)
        logger.info(
            "live_detection worker start cam_id=%s url=%s inference_delay_ms=%d",
            cid,
            self._rtsp_url,
            int(self._inference_delay_sec * 1000),
        )

        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                if not cap.isOpened():
                    logger.warning(
                        "live_detection cannot open cam_id=%s (retry in 3s)", cid
                    )
                    meta = inference_debug_status()
                    self._hub.broadcast_json(
                        {
                            "type": "detections",
                            "camera_id": cid,
                            "ts": _utc_iso(),
                            "faces": [],
                            "person_count": 0,
                            "person_detected": False,
                            "face_count": 0,
                            "error": "capture_unavailable",
                            "backend": meta.get("backend"),
                            "hailo_ready": meta.get("hailo_ready"),
                            "hailo_error": meta.get("hailo_error"),
                        }
                    )
                    self._stop.wait(3.0)
                    continue

            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("live_detection read failed cam_id=%s", cid)
                cap.release()
                cap = None
                self._stop.wait(1.0)
                continue

            self._frame_buffer.push(frame)

            frame_i += 1
            if frame_i % self._interval_frames != 0:
                continue

            now = time.monotonic()
            if now - last_send < self._min_interval_sec:
                continue

            infer_frame = self._frame_buffer.frame_for_inference()
            if infer_frame is None:
                if (
                    self._inference_delay_sec > 0.0
                    and now - last_buffer_status >= 1.5
                ):
                    last_buffer_status = now
                    meta = inference_debug_status()
                    self._hub.broadcast_json(
                        {
                            "type": "detections",
                            "camera_id": cid,
                            "ts": _utc_iso(),
                            "faces": [],
                            "person_count": 0,
                            "person_detected": False,
                            "face_count": 0,
                            "status": "buffering",
                            "buffer_age_ms": self._frame_buffer.oldest_age_ms(),
                            "inference_delay_ms": delay_ms,
                            "backend": meta.get("backend"),
                            "hailo_ready": meta.get("hailo_ready"),
                            "hailo_error": meta.get("hailo_error"),
                        }
                    )
                continue

            infer_error: Optional[str] = None
            try:
                faces = detect_faces_normalized(infer_frame)
            except Exception as e:
                logger.exception("live_detection infer cam_id=%s: %s", cid, e)
                faces = []
                infer_error = str(e)

            people = _people_from_faces(faces)
            if len(people) > 0:
                self._held_snapshot = (now, list(faces), people)
            elif self._held_snapshot is not None:
                held_at, held_faces, held_people = self._held_snapshot
                if now - held_at <= self._person_hold_sec:
                    faces = held_faces
                    people = held_people
                else:
                    self._held_snapshot = None

            meta = inference_debug_status()
            last_send = now
            person_detected = len(people) > 0
            self._hub.broadcast_json(
                {
                    "type": "detections",
                    "camera_id": cid,
                    "ts": _utc_iso(),
                    "faces": faces,
                    "face_count": len(faces),
                    "person_count": len(people),
                    "person_detected": person_detected,
                    "backend": meta.get("backend"),
                    "hailo_ready": meta.get("hailo_ready"),
                    "hailo_error": meta.get("hailo_error") or infer_error,
                    "person_detection_source": meta.get("person_detection_source"),
                }
            )
            if person_detected and not self._prev_person_detected:
                schedule_motion_clip_trigger(cid, tags=["person"])
            self._prev_person_detected = person_detected

        if cap is not None:
            cap.release()
        logger.info("live_detection worker stop cam_id=%s", cid)


class LiveDetectionService:
    def __init__(self) -> None:
        self._hub = DetectionWsHub()
        self._workers: list[_CameraWorker] = []
        self._lock = threading.Lock()
        self._started = False

    @property
    def ws_hub(self) -> DetectionWsHub:
        return self._hub

    def status(self) -> dict[str, Any]:
        with self._lock:
            worker_cameras: list[dict[str, Any]] = []
            for w in self._workers:
                worker_cameras.append(
                    {
                        "camera_id": int(w._cam["id"]),
                        "rtsp_url": w._rtsp_url,
                    }
                )
            return {
                "enabled": self._started,
                "workers": len(self._workers),
                "worker_cameras": worker_cameras,
                "interval_frames": _parse_positive_int(
                    "SMARTCAM_FACE_DETECT_INTERVAL_FRAMES", 2, 1, 120
                ),
                "overlay_delay_ms": _overlay_delay_ms(),
                "inference_delay_ms": _overlay_delay_ms(),
                "person_hold_sec": _person_hold_sec(),
                **inference_debug_status(),
            }

    def restart_workers(self) -> None:
        """Public: rebind RTSP workers (e.g. after MediaMTX starts)."""
        self._restart_workers()

    def _resolve_rtsp_url(self, cam: dict[str, Any]) -> Optional[str]:
        """Prefer localhost MediaMTX RTSP when embedded MTX runs (one upstream pull)."""
        cams = camera_store.list_cameras()
        path_map = mediamtx_manager.camera_id_to_mediamtx_path(cams)
        cid = int(cam["id"])
        if mediamtx_manager.should_run_mediamtx() and cid in path_map:
            host = os.environ.get("CONTROLLER_INFERENCE_RTSP_HOST", "127.0.0.1").strip()
            port = _parse_positive_int("CONTROLLER_INFERENCE_RTSP_PORT", 8554, 1, 65535)
            path = path_map[cid].strip("/")
            return f"rtsp://{host}:{port}/{path}"

        url = cam.get("url")
        if isinstance(url, str) and url.strip().lower().startswith("rtsp://"):
            return url.strip()
        logger.warning(
            "live_detection skip cam_id=%s: no rtsp URL (MediaMTX disabled or missing url)",
            cid,
        )
        return None

    def _restart_workers(self) -> None:
        with self._lock:
            if not self._started:
                return
            for w in self._workers:
                w.stop()
            for w in self._workers:
                w.join(timeout=4.0)
            self._workers.clear()

            max_cams = _parse_positive_int("SMARTCAM_LIVE_DETECTION_MAX_CAMERAS", 6, 1, 16)
            interval = _parse_positive_int("SMARTCAM_FACE_DETECT_INTERVAL_FRAMES", 2, 1, 120)
            min_gap = float(os.environ.get("SMARTCAM_FACE_WS_MIN_INTERVAL_SEC", "0.12"))
            try:
                min_gap = float(min_gap)
            except ValueError:
                min_gap = 0.12
            min_gap = max(0.05, min(2.0, min_gap))
            infer_delay_sec = _overlay_delay_ms() / 1000.0

            cams = camera_store.list_cameras()
            for cam in cams[:max_cams]:
                rtsp = self._resolve_rtsp_url(cam)
                if not rtsp:
                    continue
                w = _CameraWorker(
                    cam=cam,
                    rtsp_url=rtsp,
                    hub=self._hub,
                    interval_frames=interval,
                    min_interval_sec=min_gap,
                    inference_delay_sec=infer_delay_sec,
                )
                self._workers.append(w)
                w.start()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._hub.set_loop(loop)
        if os.environ.get("SMARTCAM_LIVE_DETECTION_DISABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            logger.info("live_detection disabled (SMARTCAM_LIVE_DETECTION_DISABLED)")
            return
        with self._lock:
            self._started = True
        camera_store.add_change_listener(self._on_cameras_changed)
        try:
            from .hailo_yolov8_backend import warm_up_hailo_backend

            err = warm_up_hailo_backend()
            if err:
                logger.warning("Hailo warm-up failed (workers will retry): %s", err)
            else:
                logger.info("Hailo YOLOv8n warm-up OK")
        except Exception as e:
            logger.warning("Hailo warm-up skipped: %s", e)
        # MediaMTX needs a moment to accept RTSP reads after restart.
        threading.Timer(0.8, self._restart_workers).start()
        threading.Timer(3.5, self._restart_workers).start()
        logger.info("live_detection service started")

    def stop(self) -> None:
        camera_store.remove_change_listener(self._on_cameras_changed)
        with self._lock:
            self._started = False
            for w in self._workers:
                w.stop()
            for w in self._workers:
                w.join(timeout=5.0)
            self._workers.clear()
        try:
            from .hailo_yolov8_backend import reset_detector_cache

            reset_detector_cache()
        except Exception:
            pass
        logger.info("live_detection service stopped")

    def _on_cameras_changed(self) -> None:
        threading.Timer(1.0, self._restart_workers).start()


_service = LiveDetectionService()


def get_service() -> LiveDetectionService:
    return _service
