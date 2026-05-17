"""Trigger Pi edge motion clips when controller live detection sees a person."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import httpx

from . import camera_store

logger = logging.getLogger(__name__)

_MOTION_CLIP_TIMEOUT = httpx.Timeout(5.0, read=45.0)
_last_trigger_by_cam: dict[int, float] = {}
_trigger_lock = threading.Lock()
_COOLDOWN_SEC = 2.0


def _normalize_tags(tags: Optional[list[str]]) -> list[str]:
    if not tags:
        return ["person"]
    out: list[str] = []
    for t in tags:
        if isinstance(t, str) and t.strip():
            out.append(t.strip().lower())
    return out or ["person"]


def schedule_motion_clip_trigger(
    cam_id: int,
    *,
    tags: Optional[list[str]] = None,
    source: str = "person_detection",
) -> None:
    """
    Ask the Pi edge to create a motion clip (evt_*.mp4) when the camera is in
    motion recording mode. No-op for non-edge cameras (local SSD loop handles them).
  """

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

        now = time.time()
        with _trigger_lock:
            last = _last_trigger_by_cam.get(cam_id, 0.0)
            if now - last < _COOLDOWN_SEC:
                return
            _last_trigger_by_cam[cam_id] = now

        pre = int(settings.get("pre_record_seconds", 10))
        post = int(settings.get("post_record_seconds", 50))
        body: dict[str, Any] = {
            "pre_record_seconds": pre,
            "post_record_seconds": post,
            "objects_detected": _normalize_tags(tags),
            "source": source,
        }
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
                return
            data = r.json()
            if isinstance(data, dict) and not data.get("accepted"):
                logger.debug(
                    "motion clip trigger declined cam_id=%s reason=%s",
                    cam_id,
                    data.get("reason"),
                )
        except Exception as e:
            logger.warning("motion clip trigger error cam_id=%s: %s", cam_id, e)

    threading.Thread(
        target=_run,
        name=f"motion-clip-trigger-{cam_id}",
        daemon=True,
    ).start()
