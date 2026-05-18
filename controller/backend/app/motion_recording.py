"""Person detection → motion clip state machine + edge trigger."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from typing import Any, Optional

import cv2
import httpx
import numpy as np

from . import camera_store
from .events_store import append_event
from .mqtt_bridge import get_bridge

logger = logging.getLogger(__name__)


def _positive_float_env(name: str, default: float, *, lo: float = 0.1, hi: float = 120.0) -> float:
    """Read a positive-float env var; fall back to default on missing/invalid."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("invalid %s=%r; using default %.2f", name, raw, default)
        return default
    if not (lo <= value <= hi):
        logger.warning("%s=%.2f out of range [%.2f, %.2f]; using default %.2f", name, value, lo, hi, default)
        return default
    return value


def _load_motion_clip_timeout() -> httpx.Timeout:
    """Tunable trigger timeout — defaults sized for thumbnail upload over slow Wi-Fi."""
    connect = _positive_float_env("SMARTCAM_MOTION_CLIP_CONNECT_SEC", 5.0)
    write = _positive_float_env("SMARTCAM_MOTION_CLIP_WRITE_SEC", 15.0)
    read = _positive_float_env("SMARTCAM_MOTION_CLIP_READ_SEC", 20.0)
    pool = _positive_float_env("SMARTCAM_MOTION_CLIP_POOL_SEC", 5.0)
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


_MOTION_CLIP_TIMEOUT = _load_motion_clip_timeout()
_MOTION_STATUS_TIMEOUT = httpx.Timeout(2.0, read=4.0)
_STATUS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_STATUS_CACHE_TTL_SEC = 45.0

_CAPTURE_PHASES = frozenset({"starting", "post_roll"})


def motion_status_idle() -> dict[str, Any]:
    return {
        "active": False,
        "phase": "idle",
        "remaining_seconds": 0,
        "pre_seconds": 0,
        "post_seconds": 0,
        "recording_id": "",
        "filename": None,
        "objects_detected": [],
    }


def cache_motion_status(cam_id: int, data: dict[str, Any]) -> None:
    _STATUS_CACHE[int(cam_id)] = (time.time(), data)


def fetch_edge_motion_status(edge: str, cam_id: int) -> dict[str, Any]:
    """GET Pi motion/status; never raises — caller always returns HTTP 200."""
    base = edge.rstrip("/")
    url = f"{base}/recordings/motion/status"
    last_err: Optional[Exception] = None
    try:
        r = httpx.get(url, timeout=_MOTION_STATUS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            cache_motion_status(cam_id, data)
            return data
    except Exception as e:
        last_err = e
    if last_err is not None:
        logger.info(
            "edge motion status unavailable cam_id=%s (%s): %s",
            cam_id,
            base,
            last_err,
        )
    ts, cached = _STATUS_CACHE.get(int(cam_id), (0.0, motion_status_idle()))
    if time.time() - ts < _STATUS_CACHE_TTL_SEC:
        return cached
    return {**motion_status_idle(), "phase": "edge_unreachable"}


def motion_recording_in_progress(status: dict[str, Any]) -> bool:
    """True while edge is capturing the motion clip window (not while ffmpeg saving only)."""
    if not isinstance(status, dict):
        return False
    if bool(status.get("capture_active")):
        return True
    phase = str(status.get("phase") or "idle")
    if phase in _CAPTURE_PHASES:
        return True
    ends = float(status.get("ends_at") or 0.0)
    if ends > time.time() and phase not in ("idle", "materializing"):
        return True
    return False


_last_trigger_by_cam: dict[int, float] = {}
_last_handle_by_cam: dict[int, float] = {}
_trigger_lock = threading.Lock()


def notify_motion_clip_accepted(cam_id: int, duration_seconds: int) -> None:
    """Legacy no-op: re-trigger gating uses MQTT Start/Stop via mqtt_bridge."""
    del cam_id, duration_seconds


def motion_capture_busy(cam_id: int, *, edge: Optional[str] = None) -> bool:
    """
    True from edge MQTT Start until Stop (recording complete).
    Does not poll GET /recordings/motion/status.
    """
    del edge
    bridge = get_bridge()
    if bridge is not None:
        return bridge.motion_clip_in_progress(int(cam_id))
    return False
_TRIGGER_COOLDOWN_SEC = 2.0
_HANDLE_MIN_INTERVAL_SEC = 0.35


_THUMB_JPEG_MAX_BYTES = 512_000
_THUMB_MAX_WIDTH = 480


def _encode_detection_thumbnail_b64(frame_bgr: Any) -> Optional[str]:
    """JPEG base64 of the Hailo inference frame (person-detected moment)."""
    if frame_bgr is None or not isinstance(frame_bgr, np.ndarray) or frame_bgr.size == 0:
        return None
    if frame_bgr.ndim < 2:
        return None
    img = frame_bgr
    h, w = img.shape[:2]
    if w > _THUMB_MAX_WIDTH:
        scale = float(_THUMB_MAX_WIDTH) / float(w)
        img = cv2.resize(img, (_THUMB_MAX_WIDTH, max(1, int(h * scale))))
    enc_ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not enc_ok:
        return None
    blob = jpg.tobytes()
    if len(blob) < 64 or len(blob) > _THUMB_JPEG_MAX_BYTES:
        return None
    return base64.b64encode(blob).decode("ascii")


def _normalize_tags(tags: Optional[list[str]]) -> list[str]:
    if not tags:
        return ["person"]
    out: list[str] = []
    for t in tags:
        if isinstance(t, str) and t.strip():
            out.append(t.strip().lower())
    return out or ["person"]


def _trigger_motion_clip(
    cam_id: int,
    edge: str,
    settings: dict[str, Any],
    *,
    tags: list[str],
    source: str,
    person_detected_at: float,
    detection_frame: Optional[Any] = None,
) -> dict[str, Any]:
    pre = int(settings.get("pre_record_seconds", 10))
    post = int(settings.get("post_record_seconds", 50))
    pre = max(1, min(120, pre))
    post = max(1, min(600, post))
    duration = pre + post
    body: dict[str, Any] = {
        "person_detected_at": float(person_detected_at),
        "duration_seconds": duration,
        "pre_roll_seconds": pre,
        "objects_detected": tags,
        "source": source,
    }
    thumb_b64 = _encode_detection_thumbnail_b64(detection_frame)
    if thumb_b64:
        body["thumbnail_jpeg_b64"] = thumb_b64
    url = f"{edge.rstrip('/')}/recordings/motion/trigger"

    def _post(payload: dict[str, Any]) -> dict[str, Any]:
        r = httpx.post(url, json=payload, timeout=_MOTION_CLIP_TIMEOUT)
        if r.status_code >= 400:
            logger.warning(
                "motion clip trigger failed cam_id=%s status=%s body=%s",
                cam_id,
                r.status_code,
                r.text[:200],
            )
            return {"accepted": False, "reason": f"http_{r.status_code}"}
        data = r.json()
        if not isinstance(data, dict):
            return {"accepted": False, "reason": "invalid_response"}
        if data.get("accepted"):
            cache_motion_status(cam_id, data)
        return data

    try:
        return _post(body)
    except httpx.TimeoutException as e:
        if thumb_b64:
            logger.warning(
                "motion clip trigger timed out cam_id=%s (likely thumbnail upload); retrying without thumbnail: %s",
                cam_id,
                e,
            )
            retry_body = {k: v for k, v in body.items() if k != "thumbnail_jpeg_b64"}
            try:
                return _post(retry_body)
            except httpx.TimeoutException as e2:
                logger.warning("motion clip trigger timed out (no-thumb retry) cam_id=%s: %s", cam_id, e2)
                return {"accepted": False, "reason": f"timeout: {e2}"}
            except Exception as e2:
                logger.warning("motion clip trigger error (no-thumb retry) cam_id=%s: %s", cam_id, e2)
                return {"accepted": False, "reason": str(e2)}
        logger.warning("motion clip trigger timed out cam_id=%s: %s", cam_id, e)
        return {"accepted": False, "reason": f"timeout: {e}"}
    except Exception as e:
        logger.warning("motion clip trigger error cam_id=%s: %s", cam_id, e)
        return {"accepted": False, "reason": str(e)}


def handle_person_detected(
    cam_id: int,
    *,
    tags: Optional[list[str]] = None,
    person_count: int = 1,
    source: str = "person_detection",
    person_detected_at: Optional[float] = None,
    detection_frame: Optional[Any] = None,
) -> None:
    """
    State machine on each person-positive inference frame:
    - Recording in progress → no-op (no duplicate events).
    - Idle → start motion clip; log person_detected only when edge accepts.
    """

    now = time.time()
    with _trigger_lock:
        last_h = _last_handle_by_cam.get(cam_id, 0.0)
        if now - last_h < _HANDLE_MIN_INTERVAL_SEC:
            return
        _last_handle_by_cam[cam_id] = now

    def _run() -> None:
        cam = camera_store.get_camera(cam_id)
        if not cam:
            return
        settings = cam.get("settings") or {}
        if settings.get("recording_mode") != "motion":
            return
        edge = camera_store.edge_base_url(cam)
        if not edge:
            return

        tag_list = _normalize_tags(tags)

        if motion_capture_busy(cam_id, edge=edge):
            logger.debug(
                "motion clip skipped cam_id=%s (MQTT recording active)",
                cam_id,
            )
            return

        now = time.time()
        with _trigger_lock:
            last = _last_trigger_by_cam.get(cam_id, 0.0)
            if now - last < _TRIGGER_COOLDOWN_SEC:
                return
            _last_trigger_by_cam[cam_id] = now

        detected_at = (
            float(person_detected_at)
            if person_detected_at is not None
            else time.time()
        )
        result = _trigger_motion_clip(
            cam_id,
            edge,
            settings,
            tags=tag_list,
            source=source,
            person_detected_at=detected_at,
            detection_frame=detection_frame,
        )
        if result.get("accepted"):
            pre = int(settings.get("pre_record_seconds", 10))
            post = int(settings.get("post_record_seconds", 50))
            rid = str(result.get("recording_id") or "")
            bridge = get_bridge()
            if bridge is not None and rid:
                bridge.mark_motion_clip_pending(cam_id, rid)
            append_event(
                cam_id,
                "person_detected",
                recording_id=rid or None,
                person_count=int(person_count),
                detail={
                    "pre_seconds": pre,
                    "post_seconds": post,
                    "duration_seconds": pre + post,
                    "person_detected_at": detected_at,
                    "objects_detected": tag_list,
                    "source": source,
                },
            )
            logger.info(
                "motion clip triggered cam_id=%s rid=%s (await MQTT Start)",
                cam_id,
                rid or "—",
            )
        else:
            logger.info(
                "motion clip declined cam_id=%s reason=%s",
                cam_id,
                result.get("reason"),
            )

    threading.Thread(
        target=_run,
        name=f"person-motion-{cam_id}",
        daemon=True,
    ).start()


def schedule_motion_clip_trigger(
    cam_id: int,
    *,
    tags: Optional[list[str]] = None,
    source: str = "person_detection",
) -> None:
    """Backward-compatible entry: run person state machine."""
    handle_person_detected(
        cam_id, tags=tags, source=source, person_count=1, detection_frame=None
    )
