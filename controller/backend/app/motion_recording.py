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
from .continuous_recording import _edge_base, motion_proxy_to_edge, push_settings_to_edge
from .ffmpeg_mobile import finalize_mp4_for_mobile, h264_mobile_output_args, mp4_ios_playable
from .manual_recording import recordings_dir_for
from .recording_thumbnails import write_recording_thumbnail

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
_next_trigger_allowed_at: Dict[int, float] = {}
_last_person_seen_at: Dict[int, float] = {}
_streak: Dict[int, int] = {}
_state_lock = threading.Lock()
_local_status: Dict[int, Dict[str, Any]] = {}
_busy: Dict[int, bool] = {}
_motion_active: Set[int] = set()
_post_roll_lock = threading.Lock()
_post_roll: Dict[int, Dict[str, Any]] = {}


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


def _person_gap_seconds() -> float:
    """No person detections for this long starts a new motion episode."""
    raw = os.environ.get("SMARTCAM_MOTION_PERSON_GAP_SECONDS", "2.5").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 2.5
    return max(0.5, min(v, 30.0))


def _clip_cooldown_seconds(settings: Dict[str, Any]) -> float:
    raw = os.environ.get("SMARTCAM_MOTION_CLIP_COOLDOWN_SECONDS", "5").strip()
    try:
        env_sec = float(raw)
    except ValueError:
        env_sec = 5.0
    return max(COOLDOWN_SEC, min(env_sec, 120.0))


def reset_person_trigger_state(camera_id: int) -> None:
    cid = int(camera_id)
    _last_had_person.pop(cid, None)
    _last_person_seen_at.pop(cid, None)
    _streak.pop(cid, None)
    _next_trigger_allowed_at.pop(cid, None)
    with _state_lock:
        _busy.pop(cid, None)
    with _post_roll_lock:
        _post_roll.pop(cid, None)


def _reset_motion_episode(camera_id: int) -> None:
    cid = int(camera_id)
    _streak[cid] = 0

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
    """True while a motion clip thread owns the camera (post-roll uses the detection reader)."""
    with _state_lock:
        return bool(_busy.get(int(camera_id)))


def person_record_eligible(camera_id: int) -> bool:
    if not _motion_mode(camera_id):
        return False
    return not motion_capture_busy(camera_id)


def _start_post_roll_collection(camera_id: int, detected_at: float, post_seconds: int) -> None:
    cid = int(camera_id)
    end = float(detected_at) + float(post_seconds)
    with _post_roll_lock:
        _post_roll[cid] = {"end": end, "frames": []}


def _finish_post_roll_collection(camera_id: int) -> List[bytes]:
    cid = int(camera_id)
    with _post_roll_lock:
        active = _post_roll.pop(cid, None)
    if not active:
        return []
    return list(active.get("frames") or [])


def push_motion_buffer_frame(camera_id: int, frame_bgr: Any, ts: Optional[float] = None) -> None:
    """Append a JPEG to the rolling pre-roll buffer (motion mode only)."""
    cid = int(camera_id)
    if not _motion_mode(cid):
        return
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return
    now = float(ts if ts is not None else time.time())
    enc_ok, jpg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not enc_ok:
        return
    blob = jpg.tobytes()

    with _post_roll_lock:
        active = _post_roll.get(cid)
        if active is not None and now <= float(active.get("end") or 0.0) + 0.5:
            frames = active.setdefault("frames", [])
            frames.append(blob)

    if motion_capture_busy(cid):
        return

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
    start a motion clip. Re-arms when the person is absent for ``SMARTCAM_MOTION_PERSON_GAP_SECONDS``
    or after a clip finishes (while someone is still in view, respects cooldown).
    """
    cid = int(camera_id)
    settings = _effective_settings(cid)
    now = float(detected_at if detected_at is not None else time.time())
    if str(settings.get("recording_mode", "")).strip().lower() != "motion":
        _last_had_person[cid] = person_count > 0
        _streak[cid] = 0
        _last_person_seen_at.pop(cid, None)
        return

    now_person = int(person_count) > 0
    if not now_person:
        _last_had_person[cid] = False
        last_seen = _last_person_seen_at.get(cid, 0.0)
        if last_seen > 0.0 and (now - last_seen) >= _person_gap_seconds():
            _reset_motion_episode(cid)
        return

    _last_person_seen_at[cid] = now
    _streak[cid] = _streak.get(cid, 0) + 1
    _last_had_person[cid] = True

    min_frames = _min_trigger_frames()
    if _streak[cid] < min_frames:
        return
    if motion_capture_busy(cid):
        return
    now_wall = time.time()
    if now_wall < _next_trigger_allowed_at.get(cid, 0.0):
        return

    with _state_lock:
        if _busy.get(cid):
            return
        if time.time() < _next_trigger_allowed_at.get(cid, 0.0):
            return
        _busy[cid] = True

    row = camera_store.get_camera(cid)
    logger.info(
        "[motion] triggering clip for camera %s (streak=%s/%s, pre=%ss post=%ss, edge=%s)",
        cid,
        _streak[cid],
        min_frames,
        settings.get("pre_record_seconds", 10),
        settings.get("post_record_seconds", 50),
        bool(_edge_base(dict(row)) if row else {}),
    )
    threading.Thread(
        target=_dispatch_motion_clip,
        args=(cid, now),
        name=f"motion-trigger-{cid}",
        daemon=True,
    ).start()


def _release_motion_trigger(camera_id: int, *, rearm: bool = True) -> None:
    """Clear busy flag; optionally allow a new clip while the person is still present."""
    cid = int(camera_id)
    settings = _effective_settings(cid)
    cooldown = _clip_cooldown_seconds(settings)
    with _state_lock:
        _busy[cid] = False
        _next_trigger_allowed_at[cid] = time.time() + cooldown
    with _post_roll_lock:
        _post_roll.pop(cid, None)
    if rearm:
        _reset_motion_episode(cid)


def _dispatch_motion_clip(camera_id: int, detected_at: float) -> None:
    row = camera_store.get_camera(camera_id)
    if row is None:
        _release_motion_trigger(camera_id)
        return
    cam = dict(row)
    settings = _effective_settings(camera_id)
    pre_s = max(1, min(120, int(settings.get("pre_record_seconds", 10))))
    post_s = max(1, min(600, int(settings.get("post_record_seconds", 50))))
    duration_s = pre_s + post_s

    edge = _edge_base(cam)
    try:
        use_edge = bool(edge and motion_proxy_to_edge(cam, edge))
        if use_edge:
            ok = _trigger_edge_clip(camera_id, edge, detected_at, pre_s, post_s, duration_s)
            if ok:
                return
            logger.warning(
                "[motion] camera %s: edge clip failed at %s — falling back to controller",
                camera_id,
                edge,
            )
        _trigger_local_clip(camera_id, cam, settings, detected_at, pre_s, post_s, duration_s)
    except Exception as e:
        logger.exception("[motion] dispatch failed cam %s: %s", camera_id, e)
        _release_motion_trigger(camera_id)
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
                    args=(camera_id, edge, float(duration_s)),
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


def _edge_recording_watch(camera_id: int, edge: str, clip_seconds: float) -> None:
    deadline = time.time() + max(30.0, float(clip_seconds) + 120.0)
    while time.time() < deadline:
        st = fetch_edge_motion_status(edge, int(camera_id))
        if not (st.get("active") or st.get("capture_active")):
            break
        time.sleep(1.0)
    _set_motion_recording_active(camera_id, False)
    _release_motion_trigger(camera_id)


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
        if not _busy.get(cid):
            _busy[cid] = True

    rid = f"evt_{int(time.time() * 1000)}"
    clip_end = detected_at + float(post_s)
    clip_start = float(detected_at) - float(pre_s)
    pre_jpegs = _buffer_frames_in_range(cid, clip_start, float(detected_at))
    _start_post_roll_collection(cid, float(detected_at), post_s)
    logger.info(
        "[motion] camera %s local clip armed (pre_frames=%s, post=%ss)",
        cid,
        len(pre_jpegs),
        post_s,
    )
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
        args=(cid, settings, detected_at, pre_s, post_s, duration_s, rid, pre_jpegs),
        name=f"local-motion-{cid}",
        daemon=True,
    ).start()


def _run_local_motion_clip(
    camera_id: int,
    settings: Dict[str, Any],
    detected_at: float,
    pre_s: int,
    post_s: int,
    duration_s: int,
    rid: str,
    pre_jpegs: List[bytes],
) -> None:
    cid = int(camera_id)
    read_fps = 15.0
    q = str(settings.get("quality", "medium")).lower()
    if q == "high":
        read_fps = 25.0
    elif q == "medium":
        read_fps = 25.0

    out_dir = recordings_dir_for(cid)
    out_mp4 = out_dir / f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    tmp = out_dir / f"_tmp_{rid}"
    idx = 0

    try:
        ff = shutil.which("ffmpeg")
        if not ff:
            logger.warning("[motion] camera %s: ffmpeg missing", cid)
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
            time.sleep(0.2)

        post_jpegs = _finish_post_roll_collection(cid)
        for blob in post_jpegs:
            (tmp / f"{idx:05d}.jpg").write_bytes(blob)
            idx += 1

        if idx == 0:
            logger.warning(
                "[motion] camera %s: no frames for clip (pre=%s post=%s)",
                cid,
                len(pre_jpegs),
                len(post_jpegs),
            )
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
            return
        if not finalize_mp4_for_mobile(out_mp4) or not mp4_ios_playable(out_mp4):
            logger.warning("[motion] camera %s clip unusable: %s", cid, out_mp4.name)
            try:
                out_mp4.unlink(missing_ok=True)
            except OSError:
                pass
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
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        with _buffer_lock:
            _buffers.pop(cid, None)
        _set_motion_recording_active(cid, False)
        st = _local_status.get(cid, _idle_status())
        if st.get("capture_active"):
            _set_local_status(cid, _idle_status())
        _release_motion_trigger(cid)


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


def motion_status_for_camera(cam: Dict[str, Any], camera_id: int) -> Dict[str, Any]:
    edge = _edge_base(cam)
    if edge and motion_proxy_to_edge(cam, edge):
        return fetch_edge_motion_status(edge, int(camera_id))
    return local_motion_status(int(camera_id))


def motion_debug_status(camera_id: int) -> Dict[str, Any]:
    """Lightweight trigger/recording diagnostics for troubleshooting."""
    cid = int(camera_id)
    row = camera_store.get_camera(cid)
    cam = dict(row) if row else {}
    settings = _effective_settings(cid)
    edge = _edge_base(cam)
    with _state_lock:
        busy = bool(_busy.get(cid))
    with _buffer_lock:
        buf_len = len(_buffers.get(cid, []))
    with _post_roll_lock:
        post = _post_roll.get(cid)
        post_frames = len((post or {}).get("frames") or [])
    return {
        "camera_id": cid,
        "recording_mode": settings.get("recording_mode"),
        "motion_mode": _motion_mode(cid),
        "busy": busy,
        "streak": person_trigger_streak(cid),
        "min_streak": _min_trigger_frames(),
        "next_trigger_in_sec": max(0.0, _next_trigger_allowed_at.get(cid, 0.0) - time.time()),
        "buffer_frames": buf_len,
        "post_roll_active": post is not None,
        "post_roll_frames": post_frames,
        "edge_base_url": edge or None,
        "proxy_to_edge": bool(edge and motion_proxy_to_edge(cam, edge)),
        "local_status": local_motion_status(cid),
    }


def sync_edge_settings_for_camera(camera_id: int) -> None:
    """Push recording settings to a Pi edge when the camera row uses edge proxy."""
    row = camera_store.get_camera(camera_id)
    if row is None:
        return
    cam = dict(row)
    edge = _edge_base(cam)
    if not edge or not motion_proxy_to_edge(cam, edge):
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
