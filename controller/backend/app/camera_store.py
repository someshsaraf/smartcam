# camera_store.py — single source of truth for cameras + persistence

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CAMERAS_FILE = DATA_DIR / "cameras.json"

_lock = threading.RLock()
cameras: list[dict[str, Any]] = []
selected_camera: Optional[dict[str, Any]] = None

DEFAULT_SETTINGS: dict[str, Any] = {
    "recording_mode": "motion",  # "motion" | "continuous" | "off"
    "pre_record_seconds": 10,
    "post_record_seconds": 50,
    "quality": "medium",  # "high" | "medium" | "low"
    "flip_180": False,
}

_change_listeners: list[Callable[[], None]] = []


def add_change_listener(cb: Callable[[], None]) -> None:
    """Register for persist/save-driven camera list changes (add/remove/settings)."""
    if cb not in _change_listeners:
        _change_listeners.append(cb)


def remove_change_listener(cb: Callable[[], None]) -> None:
    try:
        _change_listeners.remove(cb)
    except ValueError:
        pass


def _notify() -> None:
    for cb in list(_change_listeners):
        try:
            cb()
        except Exception:
            pass


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _default_settings() -> dict[str, Any]:
    return deepcopy(DEFAULT_SETTINGS)


def _merge_settings(cam: dict[str, Any]) -> dict[str, Any]:
    settings = deepcopy(DEFAULT_SETTINGS)
    existing = cam.get("settings")
    if isinstance(existing, dict):
        for k, v in existing.items():
            if k in DEFAULT_SETTINGS:
                settings[k] = v
    cam["settings"] = settings
    return cam


def load() -> None:
    global cameras, selected_camera
    with _lock:
        _ensure_dir()
        if not CAMERAS_FILE.is_file():
            cameras = []
            selected_camera = None
            return
        try:
            raw = json.loads(CAMERAS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cameras = []
            selected_camera = None
            return
        cams = raw.get("cameras") if isinstance(raw, dict) else None
        if not isinstance(cams, list):
            cameras = []
            selected_camera = None
            return
        cameras = []
        for c in cams:
            if not isinstance(c, dict):
                continue
            url = c.get("url")
            if not url or not isinstance(url, str):
                continue
            eb = c.get("edge_base_url")
            mt = c.get("mediamtx_path")
            mq = c.get("mqtt_camera_id")
            cam = {
                "id": int(c["id"]),
                "name": str(c.get("name", "")),
                "location": str(c.get("location", "")),
                "url": str(url),
                "edge_base_url": eb.strip().rstrip("/")
                if isinstance(eb, str) and eb.strip()
                else None,
                "mediamtx_path": str(mt).strip()
                if isinstance(mt, str) and str(mt).strip()
                else None,
                "mqtt_camera_id": str(mq).strip()
                if isinstance(mq, str) and str(mq).strip()
                else None,
            }
            if "settings" in c and isinstance(c["settings"], dict):
                cam["settings"] = c["settings"]
            _merge_settings(cam)
            cameras.append(cam)
        cameras.sort(key=lambda x: x["id"])
        sel_id = raw.get("selected_id")
        selected_camera = None
        if sel_id is not None:
            selected_camera = next(
                (c for c in cameras if c["id"] == int(sel_id)), None
            )


def save() -> None:
    with _lock:
        _ensure_dir()
        payload = {
            "cameras": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "location": c["location"],
                    "url": c["url"],
                    "edge_base_url": c.get("edge_base_url"),
                    "mediamtx_path": c.get("mediamtx_path"),
                    "mqtt_camera_id": c.get("mqtt_camera_id"),
                    "settings": deepcopy(c.get("settings", _default_settings())),
                }
                for c in cameras
            ],
            "selected_id": selected_camera["id"] if selected_camera else None,
        }
        CAMERAS_FILE.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


def add_camera(cam: dict[str, Any]) -> dict[str, Any]:
    global cameras
    with _lock:
        url_raw = cam.get("url")
        if (
            not url_raw
            or not isinstance(url_raw, str)
            or not str(url_raw).strip()
        ):
            raise ValueError(
                "Camera URL is required. For Pi 4 edges with no advertised RTSP URL, start the "
                "edge-agent with SURVEILLANCE_PI_CAMERA=1 and install mediamtx, or set "
                "SURVEILLANCE_RTSP_URL on the edge, then use Detect cameras again or paste the "
                "RTSP URL manually."
            )
        url = str(url_raw).strip()
        existing = next((c for c in cameras if c["url"] == url), None)
        if existing:
            return existing
        next_id = max((c["id"] for c in cameras), default=-1) + 1
        eb = cam.get("edge_base_url")
        mt = cam.get("mediamtx_path")
        mq = cam.get("mqtt_camera_id")
        new_cam = {
            "id": next_id,
            "name": str(cam.get("name", "")),
            "location": str(cam.get("location", "")),
            "url": url,
            "edge_base_url": eb.strip().rstrip("/")
            if isinstance(eb, str) and eb.strip()
            else None,
            "mediamtx_path": str(mt).strip()
            if isinstance(mt, str) and str(mt).strip()
            else None,
            "mqtt_camera_id": str(mq).strip()
            if isinstance(mq, str) and str(mq).strip()
            else None,
        }
        _merge_settings(new_cam)
        cameras.append(new_cam)
        save()
    _notify()
    return new_cam


def list_cameras() -> list[dict[str, Any]]:
    with _lock:
        return deepcopy(cameras)


def delete_camera(cam_id: int) -> bool:
    """Remove camera by id; persists to CAMERAS_FILE. Returns False if missing."""
    global cameras, selected_camera
    with _lock:
        idx = next((i for i, c in enumerate(cameras) if c["id"] == cam_id), None)
        if idx is None:
            return False
        cameras.pop(idx)
        if selected_camera is not None and int(selected_camera["id"]) == int(cam_id):
            selected_camera = deepcopy(cameras[0]) if cameras else None
        save()
    _notify()
    return True


def get_camera(cam_id: int) -> Optional[dict[str, Any]]:
    with _lock:
        c = next((x for x in cameras if x["id"] == cam_id), None)
        return deepcopy(c) if c else None


def update_camera_settings(cam_id: int, settings: dict[str, Any]) -> dict[str, Any]:
    global cameras
    with _lock:
        c = next((x for x in cameras if x["id"] == cam_id), None)
        if not c:
            raise KeyError("camera not found")
        current = c.get("settings", _default_settings())
        for k in DEFAULT_SETTINGS:
            if k in settings:
                current[k] = settings[k]
        if current["recording_mode"] not in ("motion", "continuous", "off"):
            raise ValueError("recording_mode must be 'motion', 'continuous', or 'off'")
        q = str(current.get("quality", "medium")).lower()
        if q not in ("high", "medium", "low"):
            raise ValueError("quality must be 'high', 'medium', or 'low'")
        current["quality"] = q
        current["flip_180"] = bool(current.get("flip_180", False))
        pre = int(current["pre_record_seconds"])
        post = int(current["post_record_seconds"])
        if pre < 1 or pre > 120 or post < 1 or post > 300:
            raise ValueError("pre_record_seconds and post_record_seconds out of range")
        current["pre_record_seconds"] = pre
        current["post_record_seconds"] = post
        c["settings"] = current
        save()
        out = deepcopy(c)
    _notify()
    return out


def select_camera(cam_id: int) -> Optional[dict[str, Any]]:
    global selected_camera
    with _lock:
        selected_camera = next(
            (c for c in cameras if c["id"] == cam_id), None
        )
        save()
        return deepcopy(selected_camera) if selected_camera else None


def get_selected_camera() -> Optional[dict[str, Any]]:
    with _lock:
        return deepcopy(selected_camera) if selected_camera else None


def mqtt_id_for_camera(cam: dict[str, Any]) -> str:
    mid = cam.get("mqtt_camera_id")
    if isinstance(mid, str) and mid.strip():
        return mid.strip()
    return str(int(cam["id"]))


def edge_base_url(cam: dict[str, Any]) -> Optional[str]:
    u = cam.get("edge_base_url")
    if isinstance(u, str) and u.strip():
        return u.strip().rstrip("/")
    return None


def mediamtx_path_for_camera(cam: dict[str, Any]) -> str:
    mp = cam.get("mediamtx_path")
    if isinstance(mp, str) and mp.strip():
        return mp.strip().lstrip("/")
    url = cam.get("url", "")
    if isinstance(url, str) and url.rstrip():
        return url.rstrip("/").split("/")[-1]
    return "camera"


# Load on import so server restarts keep state
load()
