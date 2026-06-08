
"""
Camera Store
Central camera registry and selected camera state.

Initialization order (first match wins):
1. JSON file from SMARTCAM_CAMERAS_JSON, or data/cameras.json next to cwd, if it
   contains at least one camera object.
2. One camera from env: SMARTCAM_VIGI_IP + SMARTCAM_VIGI_USER + SMARTCAM_VIGI_PASS
3. Hardcoded bootstrap_default_cameras() (replace CHANGE_ME or use env above).

VLC can play an RTSP URL directly; the dashboard only shows streams for cameras
that exist in this store. If your API returns an empty list, add cameras on the
Devices page or populate JSON / env as above.
"""

from __future__ import annotations

import json
import os
import time
from threading import Lock
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote


def _preload_backend_dotenv() -> None:
    """Load backend/.env before reading SMARTCAM_* so JSON path works without shell export."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("../.env", "../../.env"):
        p = os.path.normpath(os.path.join(here, rel))
        if os.path.isfile(p):
            load_dotenv(p, override=False)
            return


_preload_backend_dotenv()

_cameras: Dict[int, dict] = {}
_change_listeners: List[Callable] = []
_selected_camera_id: Optional[int] = None
_lock = Lock()
# Set when cameras are loaded from a JSON file (used as default save target).
_persist_source_path: Optional[str] = None


def _rtsp_userinfo(username: str, password: str) -> str:
    """Percent-encode user/password for rtsp://user:pass@host/... (VLC is lenient; parsers are not)."""
    u = quote(str(username), safe="")
    p = quote(str(password), safe="")
    return f"{u}:{p}"


def add_camera(camera: dict) -> None:
    camera_id = int(camera.get("id"))
    with _lock:
        _cameras[camera_id] = {
            **camera,
            "id": camera_id,
            "updated_at": time.time(),
        }
    notify_change()


def remove_camera(camera_id: int) -> None:
    global _selected_camera_id
    camera_id = int(camera_id)

    with _lock:
        if camera_id in _cameras:
            del _cameras[camera_id]

        if _selected_camera_id == camera_id:
            _selected_camera_id = None

    notify_change()


def get_camera(camera_id: int) -> Optional[dict]:
    return _cameras.get(int(camera_id))


def list_cameras() -> List[dict]:
    return list(_cameras.values())


def update_camera(camera_id: int, updates: dict) -> None:
    camera_id = int(camera_id)

    with _lock:
        if camera_id not in _cameras:
            return

        _cameras[camera_id].update(updates)
        _cameras[camera_id]["updated_at"] = time.time()

    notify_change()


def clear_cameras() -> None:
    global _selected_camera_id

    with _lock:
        _cameras.clear()
        _selected_camera_id = None

    notify_change()


def set_selected_camera(camera_id: int) -> None:
    global _selected_camera_id

    camera_id = int(camera_id)

    if camera_id not in _cameras:
        return

    _selected_camera_id = camera_id

    notify_change()


def get_selected_camera() -> Optional[dict]:
    global _selected_camera_id

    if _selected_camera_id:
        selected = _cameras.get(_selected_camera_id)

        if selected:
            return selected

    cameras = list(_cameras.values())

    if cameras:
        return cameras[0]

    return None


def get_selected_camera_id() -> Optional[int]:
    return _selected_camera_id


def set_camera_online(camera_id: int) -> None:
    update_camera(
        camera_id,
        {
            "status": "online",
        },
    )


def set_camera_offline(camera_id: int) -> None:
    update_camera(
        camera_id,
        {
            "status": "offline",
        },
    )


def add_change_listener(listener: Callable) -> None:
    if listener not in _change_listeners:
        _change_listeners.append(listener)


def remove_change_listener(listener: Callable) -> None:
    if listener in _change_listeners:
        _change_listeners.remove(listener)


def notify_change() -> None:
    listeners = list(_change_listeners)

    for listener in listeners:
        try:
            listener()
        except Exception as e:
            print(f"[camera_store] listener error: {e}")


def edge_base_url(camera: dict) -> Any:
    return camera.get("edge_base_url")


def set_edge_base_url(camera_id: int, url: str) -> None:
    update_camera(
        camera_id,
        {
            "edge_base_url": url,
        },
    )


def has_edge_agent(camera: dict) -> bool:
    return bool(camera.get("edge_base_url"))


def register_vigi_camera(
    ip: str,
    username: str,
    password: str,
    name: str = "TP-Link VIGI",
) -> None:
    camera_id = int(ip.split(".")[-1])
    userinfo = _rtsp_userinfo(username, password)

    add_camera(
        {
            "id": camera_id,
            "name": name,
            "manufacturer": "TP-Link",
            "model": "VIGI",
            "ip": ip,
            "status": "online",
            "type": "ONVIF/RTSP",
            "main_stream": f"rtsp://{userinfo}@{ip}:554/stream1",
            "sub_stream": f"rtsp://{userinfo}@{ip}:554/stream2",
            "resolution": "1920x1080",
            "ai_enabled": True,
            "created_at": time.time(),
        }
    )


def bootstrap_default_cameras() -> None:
    register_vigi_camera(
        ip="192.168.2.42",
        username="admin",
        password="CHANGE_ME",
        name="Front Gate",
    )


def _cameras_json_candidates() -> List[str]:
    explicit = os.environ.get("SMARTCAM_CAMERAS_JSON", "").strip()
    if explicit:
        return [explicit]
    return [
        "data/cameras.json",
        os.path.join(os.path.dirname(__file__), "..", "data", "cameras.json"),
    ]


def load_cameras_from_json_file(path: str) -> int:
    """
    Load cameras from JSON. Accepts a list of objects or {"cameras": [...]}.
    Each object must include integer "id" (or "camera_id"). Returns number loaded.
    """
    path = os.path.normpath(os.path.expanduser(path or ""))
    if not path or not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[camera_store] cannot read {path}: {e}")
        return 0

    if isinstance(raw, dict) and "cameras" in raw:
        raw = raw["cameras"]
    if not isinstance(raw, list):
        print(f"[camera_store] {path}: expected list or {{cameras: []}}")
        return 0

    count = 0
    global _persist_source_path
    for item in raw:
        if not isinstance(item, dict):
            print(f"[camera_store] skip non-object in {path}: {item!r}")
            continue
        row = dict(item)
        if "id" not in row and row.get("camera_id") is not None:
            row["id"] = row["camera_id"]
        if "id" not in row:
            print(f"[camera_store] skip entry without id in {path}: {item!r}")
            continue
        # UI + MediaMTX glue expect `url` (RTSP); JSON often only has `main_stream`.
        url = str(row.get("url") or "").strip()
        if not url:
            ms = row.get("main_stream") or row.get("mainStream")
            if ms:
                row["url"] = str(ms).strip()
        add_camera(row)
        count += 1
    if count > 0:
        _persist_source_path = os.path.normpath(os.path.abspath(path))
    return count


def _init_from_json() -> bool:
    for path in _cameras_json_candidates():
        n = load_cameras_from_json_file(os.path.normpath(path))
        if n > 0:
            print(f"[camera_store] loaded {n} camera(s) from {path}")
            return True
    return False


def _init_from_env_vigi() -> bool:
    ip = os.environ.get("SMARTCAM_VIGI_IP", "").strip()
    if not ip:
        return False
    user = os.environ.get("SMARTCAM_VIGI_USER", "admin").strip() or "admin"
    password = os.environ.get("SMARTCAM_VIGI_PASS", "").strip()
    if not password:
        print("[camera_store] SMARTCAM_VIGI_IP set but SMARTCAM_VIGI_PASS is empty; skipping env camera")
        return False
    name = os.environ.get("SMARTCAM_VIGI_NAME", "Camera").strip() or "Camera"
    register_vigi_camera(ip=ip, username=user, password=password, name=name)
    print(f"[camera_store] registered camera from env SMARTCAM_VIGI_IP={ip}")
    return True


def _init_store() -> None:
    if _init_from_json():
        return
    if _init_from_env_vigi():
        return
    bootstrap_default_cameras()


def _log_registry_startup(reason: str = "startup") -> None:
    n = len(list_cameras())
    ej = os.path.expanduser(os.environ.get("SMARTCAM_CAMERAS_JSON", "").strip())
    bits: List[str] = [f"[camera_store] ({reason}) registry has {n} camera(s)."]
    if ej:
        bits.append(f"SMARTCAM_CAMERAS_JSON={ej!r} file_exists={os.path.isfile(ej)}")
    else:
        bits.append("SMARTCAM_CAMERAS_JSON not set.")
        for cand in _cameras_json_candidates():
            p = os.path.normpath(os.path.expanduser(cand))
            if os.path.isfile(p):
                bits.append(f"Found default JSON: {p!r}")
                break
        else:
            bits.append("No default data/cameras.json on candidate paths.")
    print(" ".join(bits), flush=True)


def reload_cameras_from_json() -> int:
    """Clear registry and re-run init (JSON → env VIGI → bootstrap). For REPL or an admin HTTP route."""
    global _persist_source_path
    _persist_source_path = None
    clear_cameras()
    _init_store()
    _log_registry_startup("reload")
    return len(list_cameras())


def _resolve_persist_json_path() -> str:
    """Where to write the registry (same file as SMARTCAM_CAMERAS_JSON when set)."""
    env = os.environ.get("SMARTCAM_CAMERAS_JSON", "").strip()
    if env:
        return os.path.normpath(os.path.expanduser(env))
    if _persist_source_path:
        return _persist_source_path
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "data", "cameras.json"))


def persist_cameras_to_json() -> bool:
    """
    Write all in-memory cameras to JSON (atomic replace).

    Uses SMARTCAM_CAMERAS_JSON when set, else the file we loaded from, else
    backend/data/cameras.json. Creates parent directories as needed.
    """
    path = _resolve_persist_json_path()
    parent = os.path.dirname(path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            print(f"[camera_store] cannot create directory {parent!r}: {e}", flush=True)
            return False
    with _lock:
        items: List[dict] = []
        for row in _cameras.values():
            d = dict(row)
            d.pop("_change_token", None)
            items.append(d)
    payload: Dict[str, Any] = {"cameras": items}
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        print(f"[camera_store] persist failed {path!r}: {e}", flush=True)
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False
    print(f"[camera_store] persisted {len(items)} camera(s) -> {path}", flush=True)
    return True


_init_store()
_log_registry_startup()
