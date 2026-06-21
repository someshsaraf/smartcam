"""
Per-camera continuous RTSP → segmented MP4 recording on the controller (ffmpeg).

When ``edge_base_url`` is set and the stream host matches the edge host, continuous
recording is delegated to the edge agent (settings pushed via HTTP). Otherwise the
controller records locally (typical for LAN cameras such as VIGI).

Segments default to 60 seconds (``SMARTCAM_CONTINUOUS_SEGMENT_SECONDS``).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

from . import camera_store
from .ffmpeg_mobile import finalize_mp4_for_mobile, h264_mobile_video_args, mp4_ios_playable
from .manual_recording import _kill_subprocess, recordings_dir_for
from .mediamtx_paths import rtsp_url
from .recording_hub import RecordingHub
from .recording_thumbnails import remove_recording_thumbnail, write_recording_thumbnail

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "recording_mode": "motion",
    "pre_record_seconds": 10,
    "post_record_seconds": 50,
    "quality": "medium",
    "flip_180": False,
}

_STATE_LOCK = threading.Lock()
_recording_active: Set[int] = set()
_last_payload: Optional[Dict[str, Any]] = None
_edge_sync_key: Dict[int, str] = {}
_supervisor_thread: Optional[threading.Thread] = None


def segment_seconds() -> int:
    raw = os.environ.get("SMARTCAM_CONTINUOUS_SEGMENT_SECONDS", "60").strip()
    try:
        sec = int(raw)
    except ValueError:
        sec = 60
    return max(30, min(sec, 3600))


def _url_hostname(u: str) -> str | None:
    t = (u or "").strip()
    if not t:
        return None
    try:
        h = urlparse(t).hostname
        return str(h).strip().lower() if h else None
    except Exception:
        return None


def _proxy_to_edge(cam: Dict[str, Any], edge: str) -> bool:
    """True when continuous/manual should run on the edge agent (same host as RTSP or no RTSP)."""
    ru = rtsp_url(cam).strip()
    if not ru.startswith(("rtsp://", "rtsps://")):
        return True
    rtsp_h = _url_hostname(ru)
    edge_h = _url_hostname(edge)
    if not rtsp_h or not edge_h:
        return True
    if rtsp_h in ("127.0.0.1", "localhost", "::1") or edge_h in ("127.0.0.1", "localhost", "::1"):
        return True
    return rtsp_h == edge_h


def motion_proxy_to_edge(cam: Dict[str, Any], edge: str) -> bool:
    """
    True when person-motion clips should run on the Pi edge agent.

    Only cameras with ``edge_base_url`` on the row use the edge (Pi feeds). Standalone
    LAN cameras (VIGI via ``SMARTCAM_VIGI_*``, no edge URL) always record on the controller.
    """
    if not edge or not str(cam.get("edge_base_url") or "").strip():
        return False
    ru = rtsp_url(cam).strip()
    if not ru.startswith(("rtsp://", "rtsps://")):
        return True
    rtsp_h = _url_hostname(ru)
    edge_h = _url_hostname(edge)
    cam_ip = str(cam.get("ip") or "").strip().lower()
    if cam_ip and rtsp_h and rtsp_h == cam_ip and edge_h and rtsp_h != edge_h:
        return False
    if rtsp_h in ("127.0.0.1", "localhost", "::1"):
        return True
    if rtsp_h and edge_h and rtsp_h == edge_h:
        return True
    return True


def _edge_base(cam: Dict[str, Any]) -> str:
    u = str(cam.get("edge_base_url") or "").strip().rstrip("/")
    if u.startswith(("http://", "https://")):
        return u
    return ""


def _effective_settings(row: Dict[str, Any]) -> Dict[str, Any]:
    return {**_DEFAULT_SETTINGS, **(dict(row).get("settings") or {})}


def _settings_sync_key(settings: Dict[str, Any]) -> str:
    keys = ("recording_mode", "pre_record_seconds", "post_record_seconds", "quality", "flip_180")
    return "|".join(f"{k}={settings.get(k)!r}" for k in keys)


def get_continuous_recording_active_ids() -> Set[int]:
    with _STATE_LOCK:
        return set(_recording_active)


def recording_ws_payload() -> Dict[str, Any]:
    from .recording_hub import combined_recording_ws_payload

    return combined_recording_ws_payload()


def _broadcast_if_changed(hub: RecordingHub) -> None:
    from .recording_hub import broadcast_combined_recording_state

    broadcast_combined_recording_state()


def _set_active(camera_ids: Set[int], hub: RecordingHub) -> None:
    global _recording_active
    with _STATE_LOCK:
        _recording_active = set(camera_ids)
    _broadcast_if_changed(hub)


def push_settings_to_edge(edge: str, settings: Dict[str, Any]) -> None:
    if not edge:
        return
    body = {
        k: settings[k]
        for k in ("recording_mode", "pre_record_seconds", "post_record_seconds", "quality", "flip_180")
        if k in settings
    }
    if not body:
        return
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.patch(f"{edge}/settings", json=body)
            if r.status_code >= 400:
                logger.warning("[continuous] edge settings patch %s -> %s", edge, r.status_code)
    except Exception as e:
        logger.warning("[continuous] edge settings patch failed %s: %s", edge, e)


def notify_settings_changed(camera_id: int) -> None:
    """Drop cached edge sync so the supervisor re-pushes on the next poll."""
    with _STATE_LOCK:
        _edge_sync_key.pop(int(camera_id), None)


def _wanted_local_continuous() -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for row in camera_store.list_cameras():
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get("id", -1))
        except (TypeError, ValueError):
            continue
        if cid < 0:
            continue
        settings = _effective_settings(row)
        if str(settings.get("recording_mode", "")).strip().lower() != "continuous":
            continue
        edge = _edge_base(row)
        if edge and _proxy_to_edge(row, edge):
            continue
        url = rtsp_url(row).strip()
        if not url.startswith(("rtsp://", "rtsps://")):
            continue
        out[cid] = dict(row)
    return out


def _wanted_edge_continuous() -> Dict[int, Tuple[Dict[str, Any], str, Dict[str, Any]]]:
    out: Dict[int, Tuple[Dict[str, Any], str, Dict[str, Any]]] = {}
    for row in camera_store.list_cameras():
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get("id", -1))
        except (TypeError, ValueError):
            continue
        if cid < 0:
            continue
        settings = _effective_settings(row)
        if str(settings.get("recording_mode", "")).strip().lower() != "continuous":
            continue
        edge = _edge_base(row)
        if not edge or not _proxy_to_edge(row, edge):
            continue
        out[cid] = (dict(row), edge, settings)
    return out


def _run_continuous_session(
    camera_id: int,
    cam_row: Dict[str, Any],
    hub: RecordingHub,
    local_stop: threading.Event,
    global_stop: threading.Event,
) -> None:
    ff = shutil.which("ffmpeg")
    if not ff:
        logger.warning("[continuous] camera %s: ffmpeg missing", camera_id)
        time.sleep(5.0)
        return

    settings = _effective_settings(cam_row)
    url = rtsp_url(cam_row).strip()
    if not url.startswith(("rtsp://", "rtsps://")):
        return

    seg_sec = segment_seconds()
    out_dir = recordings_dir_for(camera_id)
    list_path = out_dir / "_continuous_segment_list.txt"
    try:
        if list_path.is_file():
            list_path.unlink()
    except OSError:
        pass

    pattern = "%Y-%m-%d_%H-%M-%S.mp4"
    cmd = [
        ff,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
    ]
    if settings.get("flip_180"):
        cmd.extend(["-vf", "vflip,hflip"])
    cmd.extend(h264_mobile_video_args(preset="veryfast"))
    cmd.extend(
        [
            "-f",
            "segment",
            "-segment_time",
            str(seg_sec),
            "-segment_list",
            list_path.name,
            "-segment_list_type",
            "flat",
            "-segment_list_size",
            "1000",
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            pattern,
        ]
    )

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(out_dir),
        )
    except OSError as e:
        logger.warning("[continuous] camera %s: ffmpeg start failed: %s", camera_id, e)
        time.sleep(5.0)
        return

    logger.info("[continuous] camera %s: started (%ss segments)", camera_id, seg_sec)
    list_line_count = 0
    last_filename = ""

    def _finalize_segment(path: Path) -> None:
        if not path.is_file():
            return
        ok = finalize_mp4_for_mobile(path)
        if not ok:
            time.sleep(0.75)
            ok = finalize_mp4_for_mobile(path)
        if ok and mp4_ios_playable(path):
            write_recording_thumbnail(path, seek_seconds=1.0)
        else:
            logger.warning("[continuous] camera %s: unusable segment %s", camera_id, path.name)
            remove_recording_thumbnail(path)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    while not local_stop.is_set() and not global_stop.is_set():
        if proc.poll() is not None:
            break
        row = camera_store.get_camera(camera_id)
        if row is None:
            break
        cur = _effective_settings(dict(row))
        if str(cur.get("recording_mode", "")).strip().lower() != "continuous":
            break
        if list_path.is_file():
            try:
                lines = list_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            while list_line_count < len(lines):
                raw = lines[list_line_count].strip()
                list_line_count += 1
                if not raw:
                    continue
                seg_path = Path(raw)
                if not seg_path.is_absolute():
                    seg_path = (out_dir / raw).resolve()
                else:
                    seg_path = seg_path.resolve()
                if last_filename and last_filename != seg_path.name:
                    prev = out_dir / last_filename
                    _finalize_segment(prev)
                last_filename = seg_path.name
        time.sleep(0.25)

    if proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=8.0)
        except Exception:
            _kill_subprocess(proc, grace_sec=1.0)

    if last_filename:
        last_seg = out_dir / last_filename
        time.sleep(0.5)
        _finalize_segment(last_seg)

    logger.info("[continuous] camera %s: stopped", camera_id)


def _continuous_worker(
    camera_id: int,
    cam_row: Dict[str, Any],
    hub: RecordingHub,
    local_stop: threading.Event,
    global_stop: threading.Event,
) -> None:
    while not local_stop.is_set() and not global_stop.is_set():
        row = camera_store.get_camera(camera_id)
        if row is None:
            break
        settings = _effective_settings(dict(row))
        if str(settings.get("recording_mode", "")).strip().lower() != "continuous":
            break
        _run_continuous_session(camera_id, dict(row), hub, local_stop, global_stop)
        if local_stop.is_set() or global_stop.is_set():
            break
        time.sleep(2.0)


def _supervisor_loop(hub: RecordingHub, stop: threading.Event) -> None:
    workers: Dict[int, Tuple[threading.Thread, threading.Event]] = {}
    try:
        while not stop.wait(2.0):
            edge_want = _wanted_edge_continuous()
            for cid, (_, edge, settings) in edge_want.items():
                key = _settings_sync_key(settings)
                with _STATE_LOCK:
                    prev = _edge_sync_key.get(cid)
                if prev != key:
                    push_settings_to_edge(edge, settings)
                    with _STATE_LOCK:
                        _edge_sync_key[cid] = key

            for cid in list(_edge_sync_key):
                if cid not in edge_want:
                    row = camera_store.get_camera(cid)
                    if row is not None:
                        push_settings_to_edge(
                            _edge_base(dict(row)),
                            _effective_settings(dict(row)),
                        )
                    with _STATE_LOCK:
                        _edge_sync_key.pop(cid, None)

            local_want = _wanted_local_continuous()
            for cid, (th, ev) in list(workers.items()):
                if cid not in local_want:
                    ev.set()
                    th.join(timeout=10.0)
                    del workers[cid]

            for cid, row in local_want.items():
                if cid in workers:
                    continue
                ev = threading.Event()
                th = threading.Thread(
                    target=_continuous_worker,
                    args=(cid, row, hub, ev, stop),
                    name=f"continuous-cam{cid}",
                    daemon=True,
                )
                th.start()
                workers[cid] = (th, ev)

            active: Set[int] = set(edge_want.keys()) | set(workers.keys())
            _set_active(active, hub)
    finally:
        for _, (th, ev) in list(workers.items()):
            ev.set()
        for _, (th, ev) in list(workers.items()):
            th.join(timeout=10.0)
        workers.clear()
        _set_active(set(), hub)


def start_continuous_recording_background(hub: RecordingHub, stop: threading.Event) -> None:
    global _supervisor_thread
    if _supervisor_thread is not None and _supervisor_thread.is_alive():
        return
    t = threading.Thread(
        target=_supervisor_loop,
        args=(hub, stop),
        name="continuous-recording-supervisor",
        daemon=True,
    )
    t.start()
    _supervisor_thread = t


def get_continuous_supervisor_thread() -> Optional[threading.Thread]:
    return _supervisor_thread
