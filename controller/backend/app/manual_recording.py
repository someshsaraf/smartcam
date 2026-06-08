"""
Per-camera manual RTSP → MP4 recording on the controller (ffmpeg).

When ``edge_base_url`` is set and the stream host matches the edge host, ``main.py``
proxies manual start/stop to the edge agent. If the RTSP URL points at another host
(e.g. a VIGI camera on the LAN while ``edge_base_url`` refers to a Pi), recording stays
on the controller using this module.

Concurrency: one active manual session per camera_id (global lock).
"""

from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .ffmpeg_mobile import (
    finalize_mp4_for_mobile,
    h264_mobile_fragmented_mp4_args,
    mp4_ios_playable,
    mp4_listable_fast,
)
from .mediamtx_paths import rtsp_url
from .recording_thumbnails import (
    remove_recording_thumbnail,
    thumbnail_exists_for,
    write_recording_thumbnail,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_sessions: Dict[int, "_ManualSession"] = {}


class _ManualSession:
    __slots__ = ("proc", "path", "rid")

    def __init__(self, proc: subprocess.Popen, path: Path, rid: str) -> None:
        self.proc = proc
        self.path = path
        self.rid = rid


def backend_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def recordings_dir_for(camera_id: int) -> Path:
    d = backend_data_dir() / "recordings" / str(int(camera_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kill_subprocess(proc: subprocess.Popen, *, grace_sec: float = 1.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=grace_sec)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass


def manual_status_local(camera_id: int) -> Dict[str, Any]:
    with _LOCK:
        s = _sessions.get(int(camera_id))
        if s is None:
            return {"active": False, "filename": None}
        proc = s.proc
        if proc.poll() is not None:
            return {"active": False, "filename": None}
        name = s.path.name if s.path else None
        return {"active": True, "filename": name}


def start_manual_local(
    camera_id: int,
    cam_row: Dict[str, Any],
    *,
    recording_mode: str,
    flip_180: bool,
) -> Dict[str, Any]:
    if str(recording_mode or "").strip().lower() != "off":
        raise ValueError("set recording mode to Off before manual recording")
    url = rtsp_url(cam_row)
    if not url.startswith(("rtsp://", "rtsps://")):
        raise ValueError("camera has no RTSP URL for manual recording")

    ff = shutil.which("ffmpeg")
    if not ff:
        raise ValueError("ffmpeg not found on PATH")

    out_dir = recordings_dir_for(camera_id)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"manual_{ts}.mp4"
    rid = f"manual_{ts}"

    cmd = [
        ff,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
    ]
    if flip_180:
        cmd.extend(["-vf", "vflip,hflip"])
    cmd.extend(h264_mobile_fragmented_mp4_args(preset="veryfast"))
    cmd.append(str(out_path))

    with _LOCK:
        existing = _sessions.get(int(camera_id))
        if existing is not None and existing.proc.poll() is None:
            raise ValueError("manual recording already active")
        if existing is not None:
            _sessions.pop(int(camera_id), None)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            raise ValueError(f"ffmpeg failed to start: {e}") from e
        _sessions[int(camera_id)] = _ManualSession(proc, out_path, rid)

    logger.info("[manual_rec] camera %s started -> %s", camera_id, out_path.name)
    return {"active": True, "filename": out_path.name, "recording_id": rid}


def stop_manual_local(camera_id: int) -> Dict[str, Any]:
    with _LOCK:
        s = _sessions.pop(int(camera_id), None)
    if s is None:
        return {"active": False, "filename": None, "size": 0}

    proc = s.proc
    out_path = s.path
    filename = out_path.name if out_path else ""

    if proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=8.0)
        except Exception:
            _kill_subprocess(proc, grace_sec=1.0)

    playable_name = ""
    size_out = 0
    if out_path is not None and out_path.is_file():
        time.sleep(0.4)
        ok = finalize_mp4_for_mobile(out_path)
        if not ok:
            time.sleep(0.75)
            ok = finalize_mp4_for_mobile(out_path)
        if ok and mp4_ios_playable(out_path):
            playable_name = out_path.name
            try:
                size_out = int(out_path.stat().st_size)
            except OSError:
                size_out = 0
            write_recording_thumbnail(out_path, seek_seconds=1.0)
        else:
            logger.warning("[manual_rec] clip unusable, removing: %s", out_path.name)
            remove_recording_thumbnail(out_path)
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass
            filename = ""

    logger.info("[manual_rec] camera %s stopped (%s)", camera_id, playable_name or "none")
    return {
        "active": False,
        "filename": playable_name or None,
        "size": size_out,
    }


_SAFE_MP4 = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")


def resolve_recording_file(camera_id: int, filename: str) -> Optional[Path]:
    if not _SAFE_MP4.match(filename):
        return None
    root = recordings_dir_for(camera_id).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def list_local_recordings_for_camera(
    camera_id: int, cam_name: str
) -> list[Dict[str, Any]]:
    root = recordings_dir_for(camera_id)
    out: list[Dict[str, Any]] = []
    for p in sorted(root.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if not _SAFE_MP4.match(p.name):
            continue
        if not mp4_listable_fast(p):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            {
                "camId": int(camera_id),
                "camName": cam_name,
                "edgeBaseUrl": "",
                "name": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "hasThumbnail": thumbnail_exists_for(p),
            }
        )
    return out
