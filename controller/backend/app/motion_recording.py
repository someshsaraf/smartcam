"""
Person-triggered motion clips when ``recording_mode`` is ``motion``.

- Pi edge cameras (RTSP host matches ``edge_base_url``): ``POST /recordings/motion/trigger``.
- LAN / VIGI cameras on the controller: local rolling JPEG buffer + ffmpeg ``evt_*.mp4``.

Concurrency: per-camera buffer + clip threads; cooldown between triggers.
"""

from __future__ import annotations

import collections
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import cv2
import httpx

from . import camera_store
from .continuous_recording import _edge_base, _proxy_to_edge, push_settings_to_edge
from .ffmpeg_mobile import finalize_mp4_for_mobile, h264_mobile_output_args, mp4_ios_playable
from .manual_recording import recordings_dir_for
from .mediamtx_paths import rtsp_url
from .recording_thumbnails import write_recording_thumbnail
from .rtsp_capture import apply_rtsp_env

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "recording_mode": "motion",
    "pre_record_seconds": 10,
    "post_record_seconds": 50,
    "quality": "medium",
    "flip_180": False,
}

COOLDOWN_SEC = 2.0
_MOTION_TRIGGER_TIMEOUT = httpx.Timeout(3.0, read=12.0)
_MOTION_STATUS_TIMEOUT = httpx.Timeout(2.0, read=4.0)

_STATUS_CACHE: Dict[int, Tuple[float, Dict[str, Any]]] = {}
_buffers: Dict[int, Deque[Tuple[float, bytes]]] = {}
_buffer_lock = threading.Lock()
_last_had_person: Dict[int, bool] = {}
_last_trigger_at: Dict[int, float] = {}
_streak: Dict[int, int] = {}
_episode_triggered: Dict[int, bool] = {}
_state_lock = threading.Lock()
_local_status: Dict[int, Dict[str, Any]] = {}
_busy: Dict[int, bool] = {}
_motion_active: Set[int] = set()


def _min_trigger_frames() -> int:
    raw = os.environ.get("SMARTCAM_PERSON_TRIGGER_MIN_FRAMES", "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(n, 30))


def get_motion_recording_active_ids() -> Set[int]:
    with _state_lock:
        return set(_motion_active)


def reset_person_trigger_state(camera_id: int) -> None:
    cid = int(camera_id)
    _last_had_person.pop(cid, None)
    _streak.pop(cid, None)
    _episode_triggered.pop(cid, None)


def _clear_episode_trigger(camera_id: int) -> None:
    _episode_triggered[int(camera_id)] = False


def _set_motion_recording_active(camera_id: int, active: bool) -> None:
    cid = int(camera_id)
    with _state_lock:
        if active:
            _motion_active.add(cid)
        else:
            _motion_active.discard(cid)
    try:
        from .recording_hub import broadcast_combined_recording_state

        broadcast_combined_recording_state()
    except Exception as e:
        logger.debug("[motion] recording ws broadcast failed cam %s: %s", cid, e)


def _buffer_seconds() -> float:
    raw = os.environ.get("SMARTCAM_MOTION_BUFFER_SECONDS", "30").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 30.0
    return max(10.0, min(120.0, v))


def _effective_settings(camera_id: int) -> Dict[str, Any]:
    row = camera_store.get_camera(camera_id)
    if row is None:
        return dict(_DEFAULT_SETTINGS)
    return {**_DEFAULT_SETTINGS, **(dict(row).get("settings") or {})}


def _motion_mode(camera_id: int) -> bool:
    return str(_effective_settings(camera_id).get("recording_mode", "")).strip().lower() == "motion"


def _idle_status() -> Dict[str, Any]:
    return {
        "active": False,
        "phase": "idle",
        "capture_active": False,
        "remaining_seconds": 0,
        "pre_seconds": 0,
        "post_seconds": 0,
        "filename": None,
    }


def motion_capture_busy(camera_id: int) -> bool:
    with _state_lock:
        if _busy.get(int(camera_id)):
            return True
        st = _local_status.get(int(camera_id), {})
        return bool(st.get("active") or st.get("capture_active"))


def person_record_eligible(camera_id: int) -> bool:
    if not _motion_mode(camera_id):
        return False
    return not motion_capture_busy(camera_id)


def push_motion_buffer_frame(camera_id: int, frame_bgr: Any, ts: Optional[float] = None) -> None:
    """Append a JPEG to the rolling pre-roll buffer (motion mode only)."""
    cid = int(camera_id)
    if not _motion_mode(cid):
        return
    if motion_capture_busy(cid):
        return
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return
    now = float(ts if ts is not None else time.time())
    enc_ok, jpg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not enc_ok:
        return
    blob = jpg.tobytes()
    cutoff = now - _buffer_seconds()
    with _buffer_lock:
        dq = _buffers.setdefault(cid, collections.deque())
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        dq.append((now, blob))


def _buffer_frames_in_range(camera_id: int, start: float, until: float) -> List[bytes]:
    with _buffer_lock:
        dq = _buffers.get(int(camera_id), collections.deque())
        return [blob for t, blob in dq if start <= t <= until]


def person_trigger_streak(camera_id: int) -> int:
    return int(_streak.get(int(camera_id), 0))


def person_trigger_min_frames() -> int:
    return _min_trigger_frames()


def on_person_detected(
    camera_id: int,
    person_count: int,
    *,
    detected_at: Optional[float] = None,
) -> None:
    """
    After ``SMARTCAM_PERSON_TRIGGER_MIN_FRAMES`` consecutive person frames (default 3),
    start one motion clip per presence episode (re-arms when the person leaves).
    """
    cid = int(camera_id)
    settings = _effective_settings(cid)
    if str(settings.get("recording_mode", "")).strip().lower() != "motion":
        _last_had_person[cid] = person_count > 0
        _streak[cid] = 0
        _episode_triggered[cid] = False
        return

    now_person = int(person_count) > 0
    if not now_person:
        _last_had_person[cid] = False
        _streak[cid] = 0
        _episode_triggered[cid] = False
        return

    _streak[cid] = _streak.get(cid, 0) + 1
    _last_had_person[cid] = True

    if _episode_triggered.get(cid):
        return
    if _streak[cid] < _min_trigger_frames():
        return
    if motion_capture_busy(cid):
        return

    ts = float(detected_at if detected_at is not None else time.time())
    with _state_lock:
        if ts - _last_trigger_at.get(cid, 0.0) < COOLDOWN_SEC:
            return
        if _busy.get(cid):
            return
        _last_trigger_at[cid] = ts
        _episode_triggered[cid] = True

    logger.info(
        "[motion] triggering clip for camera %s (streak=%s, pre=%ss post=%ss)",
        cid,
        _streak[cid],
        settings.get("pre_record_seconds", 10),
        settings.get("post_record_seconds", 50),
    )
    threading.Thread(
        target=_dispatch_motion_clip,
        args=(cid, ts),
        name=f"motion-trigger-{cid}",
        daemon=True,
    ).start()


def _dispatch_motion_clip(camera_id: int, detected_at: float) -> None:
    row = camera_store.get_camera(camera_id)
    if row is None:
        _clear_episode_trigger(camera_id)
        return
    cam = dict(row)
    settings = _effective_settings(camera_id)
    pre_s = max(1, min(120, int(settings.get("pre_record_seconds", 10))))
    post_s = max(1, min(600, int(settings.get("post_record_seconds", 50))))
    duration_s = pre_s + post_s

    edge = _edge_base(cam)
    try:
        if edge and _proxy_to_edge(cam, edge):
            ok = _trigger_edge_clip(camera_id, edge, detected_at, pre_s, post_s, duration_s)
            if not ok:
                _clear_episode_trigger(camera_id)
        else:
            _trigger_local_clip(camera_id, cam, settings, detected_at, pre_s, post_s, duration_s)
    except Exception as e:
        logger.exception("[motion] dispatch failed cam %s: %s", camera_id, e)
        _clear_episode_trigger(camera_id)
        _set_motion_recording_active(camera_id, False)


def _trigger_edge_clip(
    camera_id: int,
    edge: str,
    detected_at: float,
    pre_s: int,
    post_s: int,
    duration_s: int,
) -> bool:
    url = f"{edge.rstrip('/')}/recordings/motion/trigger"
    body = {
        "person_detected_at": detected_at,
        "pre_seconds": pre_s,
        "post_seconds": post_s,
        "duration_seconds": duration_s,
        "objects_detected": ["person"],
    }
    try:
        r = httpx.post(url, json=body, timeout=_MOTION_TRIGGER_TIMEOUT)
        if r.status_code >= 400:
            logger.warning(
                "[motion] edge trigger cam %s HTTP %s: %s",
                camera_id,
                r.status_code,
                (r.text or "")[:200],
            )
            return False
        data = r.json()
        if isinstance(data, dict):
            cache_motion_status(camera_id, data)
            if data.get("accepted"):
                logger.info("[motion] edge clip accepted for camera %s", camera_id)
                _set_motion_recording_active(camera_id, True)
                threading.Thread(
                    target=_edge_recording_watch,
                    args=(camera_id, float(post_s)),
                    name=f"motion-edge-watch-{camera_id}",
                    daemon=True,
                ).start()
                return True
            logger.warning(
                "[motion] edge rejected clip cam %s: %s",
                camera_id,
                data.get("reason") or data,
            )
            return False
    except Exception as e:
        logger.warning("[motion] edge trigger failed cam %s: %s", camera_id, e)
    return False


def _edge_recording_watch(camera_id: int, post_seconds: float) -> None:
    time.sleep(max(1.0, float(post_seconds)) + 2.0)
    _set_motion_recording_active(camera_id, False)


def _set_local_status(camera_id: int, status: Dict[str, Any]) -> None:
    with _state_lock:
        _local_status[int(camera_id)] = dict(status)


def _trigger_local_clip(
    camera_id: int,
    cam: Dict[str, Any],
    settings: Dict[str, Any],
    detected_at: float,
    pre_s: int,
    post_s: int,
    duration_s: int,
) -> None:
    cid = int(camera_id)
    with _state_lock:
        if _busy.get(cid):
            return
        _busy[cid] = True

    rid = f"evt_{int(time.time() * 1000)}"
    clip_end = detected_at + float(post_s)
    _set_motion_recording_active(cid, True)
    _set_local_status(
        cid,
        {
            "active": True,
            "capture_active": True,
            "phase": "starting",
            "pre_seconds": pre_s,
            "post_seconds": post_s,
            "duration_seconds": duration_s,
            "remaining_seconds": max(0, int(clip_end - time.time())),
            "filename": None,
            "recording_id": rid,
        },
    )

    threading.Thread(
        target=_run_local_motion_clip,
        args=(cid, cam, settings, detected_at, pre_s, post_s, duration_s, rid),
        name=f"local-motion-{cid}",
        daemon=True,
    ).start()


def _run_local_motion_clip(
    camera_id: int,
    cam: Dict[str, Any],
    settings: Dict[str, Any],
    detected_at: float,
    pre_s: int,
    post_s: int,
    duration_s: int,
    rid: str,
) -> None:
    apply_rtsp_env()
    cid = int(camera_id)
    url = rtsp_url(cam).strip()
    flip = bool(settings.get("flip_180"))
    read_fps = 15.0
    q = str(settings.get("quality", "medium")).lower()
    if q == "high":
        read_fps = 25.0
    elif q == "medium":
        read_fps = 25.0

    out_dir = recordings_dir_for(cid)
    out_mp4 = out_dir / f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    tmp = out_dir / f"_tmp_{rid}"
    clip_start = float(detected_at) - float(pre_s)
    pre_jpegs = _buffer_frames_in_range(cid, clip_start, float(detected_at))
    idx = 0

    try:
        ff = shutil.which("ffmpeg")
        if not ff:
            logger.warning("[motion] camera %s: ffmpeg missing", cid)
            _clear_episode_trigger(cid)
            return
        if not url.startswith(("rtsp://", "rtsps://")):
            logger.warning("[motion] camera %s: no RTSP URL", cid)
            _clear_episode_trigger(cid)
            return

        tmp.mkdir(parents=True, exist_ok=True)
        for blob in pre_jpegs:
            (tmp / f"{idx:05d}.jpg").write_bytes(blob)
            idx += 1

        clip_end = float(detected_at) + float(post_s)
        _set_local_status(
            cid,
            {
                "active": True,
                "capture_active": True,
                "phase": "post_roll",
                "pre_seconds": pre_s,
                "post_seconds": post_s,
                "duration_seconds": duration_s,
                "remaining_seconds": max(0, int(clip_end - time.time())),
                "ends_at": clip_end,
                "filename": None,
                "recording_id": rid,
            },
        )

        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        while time.time() < clip_end:
            _set_local_status(
                cid,
                {
                    "active": True,
                    "capture_active": True,
                    "phase": "post_roll",
                    "pre_seconds": pre_s,
                    "post_seconds": post_s,
                    "duration_seconds": duration_s,
                    "remaining_seconds": max(0, int(clip_end - time.time())),
                    "ends_at": clip_end,
                    "filename": None,
                    "recording_id": rid,
                },
            )
            if not cap.isOpened():
                break
            loop_t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue
            if flip:
                frame = cv2.flip(cv2.flip(frame, 0), 1)
            enc_ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if enc_ok:
                (tmp / f"{idx:05d}.jpg").write_bytes(jpg.tobytes())
                idx += 1
            elapsed = time.perf_counter() - loop_t0
            time.sleep(max(0.0, (1.0 / read_fps) - elapsed))
        cap.release()

        if idx == 0:
            logger.warning("[motion] camera %s: no frames for clip", cid)
            _clear_episode_trigger(cid)
            return

        cmd = [
            ff,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-framerate",
            str(max(1, int(round(read_fps)))),
            "-i",
            str(tmp / "%05d.jpg"),
            *h264_mobile_output_args(preset="veryfast"),
            str(out_mp4),
        ]
        r = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            logger.warning("[motion] camera %s ffmpeg failed: %s", cid, (r.stderr or "")[-300:])
            _clear_episode_trigger(cid)
            return
        if not finalize_mp4_for_mobile(out_mp4) or not mp4_ios_playable(out_mp4):
            logger.warning("[motion] camera %s clip unusable: %s", cid, out_mp4.name)
            try:
                out_mp4.unlink(missing_ok=True)
            except OSError:
                pass
            _clear_episode_trigger(cid)
            return

        write_recording_thumbnail(out_mp4, seek_seconds=max(0.5, float(pre_s) - 0.5))
        logger.info("[motion] camera %s saved %s (%d frames)", cid, out_mp4.name, idx)
        _set_local_status(
            cid,
            {
                "active": False,
                "capture_active": False,
                "phase": "idle",
                "pre_seconds": pre_s,
                "post_seconds": post_s,
                "remaining_seconds": 0,
                "filename": out_mp4.name,
                "recording_id": rid,
            },
        )
    except Exception as e:
        logger.exception("[motion] camera %s clip failed: %s", cid, e)
        _clear_episode_trigger(cid)
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        with _buffer_lock:
            _buffers.pop(cid, None)
        with _state_lock:
            _busy[cid] = False
        _set_motion_recording_active(cid, False)
        st = _local_status.get(cid, _idle_status())
        if st.get("capture_active"):
            _set_local_status(cid, _idle_status())


def cache_motion_status(camera_id: int, data: Dict[str, Any]) -> None:
    _STATUS_CACHE[int(camera_id)] = (time.time(), dict(data))


def fetch_edge_motion_status(edge: str, camera_id: int) -> Dict[str, Any]:
    url = f"{edge.rstrip('/')}/recordings/motion/status"
    try:
        r = httpx.get(url, timeout=_MOTION_STATUS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            cache_motion_status(camera_id, data)
            return data
    except Exception as e:
        logger.debug("[motion] edge status cam %s: %s", camera_id, e)
    ts, cached = _STATUS_CACHE.get(int(camera_id), (0.0, _idle_status()))
    if time.time() - ts < 30.0:
        return dict(cached)
    return _idle_status()


def local_motion_status(camera_id: int) -> Dict[str, Any]:
    with _state_lock:
        st = _local_status.get(int(camera_id))
        if st:
            out = dict(st)
            if out.get("capture_active") and out.get("phase") == "post_roll":
                ends = out.get("ends_at")
                if isinstance(ends, (int, float)):
                    out["remaining_seconds"] = max(0, int(float(ends) - time.time()))
            return out
    return _idle_status()


def sync_edge_settings_for_camera(camera_id: int) -> None:
    """Push recording settings to a Pi edge when the camera row uses edge proxy."""
    row = camera_store.get_camera(camera_id)
    if row is None:
        return
    cam = dict(row)
    edge = _edge_base(cam)
    if not edge or not _proxy_to_edge(cam, edge):
        return
    push_settings_to_edge(edge, _effective_settings(camera_id))


def sync_all_edge_settings() -> None:
    for row in camera_store.list_cameras():
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        try:
            sync_edge_settings_for_camera(int(row["id"]))
        except Exception as e:
            logger.warning("[motion] edge settings sync cam %s: %s", row.get("id"), e)
