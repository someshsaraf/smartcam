"""Person detection → motion clip state machine + edge trigger."""

from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Any, Optional

import cv2
import httpx
import numpy as np

from . import camera_store
from .events_store import append_event

logger = logging.getLogger(__name__)

_MOTION_CLIP_TIMEOUT = httpx.Timeout(3.0, read=12.0)
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
_capture_block_until: dict[int, float] = {}


def notify_motion_clip_accepted(cam_id: int, duration_seconds: int) -> None:
    """Block re-trigger until the clip capture window ends (saving may continue)."""
    dur = max(1.0, float(duration_seconds))
    with _trigger_lock:
        _capture_block_until[int(cam_id)] = time.time() + dur


def motion_capture_busy(cam_id: int, *, edge: Optional[str] = None) -> bool:
    """
    True while the ~60s capture is in progress. False during materializing/saving so
    a new clip may start while the previous MP4 is still being encoded.
    """
    cid = int(cam_id)
    if edge:
        st = fetch_edge_motion_status(edge, cid)
        if motion_recording_in_progress(st):
            return True
        with _trigger_lock:
            _capture_block_until.pop(cid, None)
        return False
    with _trigger_lock:
        until = _capture_block_until.get(cid, 0.0)
    return time.time() < until
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
    try:
        r = httpx.post(url, json=body, timeout=_MOTION_CLIP_TIMEOUT)
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
        status = fetch_edge_motion_status(edge, cam_id)

        if motion_capture_busy(cam_id, edge=edge):
            logger.debug(
                "motion clip skipped cam_id=%s (capture in progress phase=%s)",
                cam_id,
                status.get("phase"),
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
            notify_motion_clip_accepted(cam_id, pre + post)
            st = fetch_edge_motion_status(edge, cam_id)
            rid = str(st.get("recording_id") or result.get("recording_id") or "")
            append_event(
                cam_id,
                "person_detected",
                recording_id=rid or None,
                person_count=int(person_count),
                detail={
                    "pre_seconds": st.get("pre_seconds"),
                    "post_seconds": st.get("post_seconds"),
                    "duration_seconds": st.get("duration_seconds"),
                    "person_detected_at": detected_at,
                    "objects_detected": tag_list,
                    "source": source,
                },
            )
            logger.info(
                "motion clip started cam_id=%s rid=%s",
                cam_id,
                st.get("recording_id"),
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
