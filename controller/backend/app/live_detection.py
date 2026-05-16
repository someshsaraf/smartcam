"""Per-camera RTSP decode + face detection; WebSocket fan-out for Phase 1 UI overlays."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import cv2

from . import camera_store
from . import mediamtx_manager
from .face_backend import detect_faces_normalized
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
    ) -> None:
        super().__init__(daemon=True)
        self._cam = cam
        self._rtsp_url = rtsp_url
        self._hub = hub
        self._interval_frames = max(1, interval_frames)
        self._min_interval_sec = max(0.05, min_interval_sec)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        cid = int(self._cam["id"])
        cap: Optional[cv2.VideoCapture] = None
        frame_i = 0
        last_send = 0.0
        logger.info("live_detection worker start cam_id=%s url=%s", cid, self._rtsp_url)

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
                    self._hub.broadcast_json(
                        {
                            "type": "detections",
                            "camera_id": cid,
                            "ts": _utc_iso(),
                            "faces": [],
                            "error": "capture_unavailable",
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

            frame_i += 1
            if frame_i % self._interval_frames != 0:
                continue

            now = time.monotonic()
            if now - last_send < self._min_interval_sec:
                continue

            try:
                faces = detect_faces_normalized(frame)
            except Exception as e:
                logger.exception("live_detection infer cam_id=%s: %s", cid, e)
                faces = []

            people = [
                f
                for f in faces
                if isinstance(f, dict) and str(f.get("label", "")).lower() == "person"
            ]
            last_send = now
            self._hub.broadcast_json(
                {
                    "type": "detections",
                    "camera_id": cid,
                    "ts": _utc_iso(),
                    "faces": faces,
                    "person_count": len(people),
                    "person_detected": len(people) > 0,
                }
            )

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
            return {
                "enabled": self._started,
                "workers": len(self._workers),
                "backend": os.environ.get("SMARTCAM_FACE_BACKEND", "opencv"),
                "interval_frames": _parse_positive_int(
                    "SMARTCAM_FACE_DETECT_INTERVAL_FRAMES", 5, 1, 120
                ),
            }

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
            interval = _parse_positive_int("SMARTCAM_FACE_DETECT_INTERVAL_FRAMES", 5, 1, 120)
            min_gap = float(os.environ.get("SMARTCAM_FACE_WS_MIN_INTERVAL_SEC", "0.12"))
            try:
                min_gap = float(min_gap)
            except ValueError:
                min_gap = 0.12
            min_gap = max(0.05, min(2.0, min_gap))

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
        # MediaMTX needs a moment to accept RTSP reads after restart.
        threading.Timer(0.8, self._restart_workers).start()
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
        logger.info("live_detection service stopped")

    def _on_cameras_changed(self) -> None:
        threading.Timer(1.0, self._restart_workers).start()


_service = LiveDetectionService()


def get_service() -> LiveDetectionService:
    return _service
