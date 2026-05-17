"""Pi 4 edge HTTP API: recordings CRUD + settings; starts EdgeRecorder thread."""

from __future__ import annotations

from . import env_loader  # noqa: F401  # loads edge-agent/.env

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

from . import _shared_bootstrap  # noqa: F401

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from surveillance_shared.ffmpeg_mobile import (
    finalize_mp4_for_mobile,
    mp4_ios_playable,
    mp4_probe_ok,
)

from .local_publisher import LocalPublisher
from .worker import EdgeRecorder
from .zeroconf_publish import EdgeZeroconfPublisher

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")

_CHUNK = 1024 * 1024


def _iter_mp4_file(path: Path):
    """
    Stream file bytes without a fixed Content-Length.

    ``FileResponse`` pins length at stat time; if the file still grows (encoder
    finishing the MP4), Starlette can emit more bytes than declared and raise
    ``RuntimeError: Response content longer than Content-Length``.
    """
    with path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            yield chunk

# Raspberry Pi 5 controller (MQTT). Override with SURVEILLANCE_MQTT_HOST.
_CONTROLLER_PI5_IP = "192.168.2.139"


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else str(v)


_EDGE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL_DIR = _EDGE_ROOT / "models"

_rtsp_explicit = _env("SURVEILLANCE_RTSP_URL", "").strip()
_mqtt_host = (_env("SURVEILLANCE_MQTT_HOST", _CONTROLLER_PI5_IP) or _CONTROLLER_PI5_IP).strip()
_cam_id = _env("SURVEILLANCE_EDGE_CAMERA_ID", "camera1").strip()
_rec_root = Path(_env("SURVEILLANCE_RECORDINGS_DIR", str(_EDGE_ROOT / "data" / "recordings")))
_model_dir = Path(_env("SURVEILLANCE_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))
_http_port = int(_env("SURVEILLANCE_EDGE_HTTP_PORT", "8080"))
_edge_display_name = _env("SURVEILLANCE_EDGE_DISPLAY_NAME", "Vigilance Edge").strip()
_edge_location = _env("SURVEILLANCE_EDGE_LOCATION", "").strip()
_mediamtx_path = _env("SURVEILLANCE_MEDIAMTX_PATH", _cam_id).strip()

_recorder: Optional[EdgeRecorder] = None
_zc_pub: Optional[EdgeZeroconfPublisher] = None
_publisher: Optional[LocalPublisher] = None
_effective_rtsp: str = ""
_advertised_rtsp: str = ""


def _settings_provider() -> dict[str, Any]:
    """
    Snapshot of recorder settings used by ``LocalPublisher`` to drive its YAML.

    Returns ``{}`` when the recorder hasn't been constructed yet, in which
    case ``LocalPublisher`` falls back to its safe defaults.
    """
    rec = _recorder
    if rec is None:
        return {}
    try:
        return rec.snapshot_settings()
    except Exception as e:
        logger.warning("settings snapshot failed: %s", e)
        return {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _recorder, _zc_pub, _publisher, _effective_rtsp, _advertised_rtsp

    # 1) Start the publisher first so the recorder can consume from it.
    _publisher = LocalPublisher(
        recorder_settings_provider=_settings_provider,
        config_dir=_EDGE_ROOT / "data",
    )
    try:
        _publisher.start()
    except Exception as exc:
        logger.warning("LocalPublisher start failed: %s", exc)

    # 2) Decide effective and advertised RTSP URLs.
    pub_loopback = _publisher.effective_rtsp_url() if _publisher else None
    pub_lan = _publisher.advertised_rtsp_url() if _publisher else None
    if _rtsp_explicit:
        # Operator-supplied override always wins; we both consume and advertise it.
        _effective_rtsp = _rtsp_explicit
        _advertised_rtsp = _rtsp_explicit
    else:
        _effective_rtsp = pub_loopback or ""
        _advertised_rtsp = pub_lan or ""

    # 3) Register mDNS with whatever URL we resolved (empty string is fine; the
    # controller will mark the row ``incomplete`` instead of fabricating one).
    _zc_pub = None
    try:
        _zc_pub = EdgeZeroconfPublisher(
            camera_id=_cam_id,
            display_name=_edge_display_name,
            location=_edge_location,
            rtsp_url=_advertised_rtsp,
            mediamtx_path=_mediamtx_path,
            http_port=_http_port,
        )
        _zc_pub.register()
    except Exception as exc:
        logger.warning("Zeroconf registration failed: %s", exc)

    # 4) Start the recorder if (and only if) we have a real RTSP URL.
    try:
        if _effective_rtsp:
            port = int(_env("SURVEILLANCE_MQTT_PORT", "1883"))
            user = _env("SURVEILLANCE_MQTT_USER") or None
            pwd = _env("SURVEILLANCE_MQTT_PASSWORD") or None
            prefix = _env("SURVEILLANCE_MQTT_TOPIC_PREFIX", "surveillance/cameras").strip()
            on_changed = _publisher.update_settings if _publisher is not None else None
            _recorder = EdgeRecorder(
                camera_mqtt_id=_cam_id,
                rtsp_url=_effective_rtsp,
                recordings_root=_rec_root,
                mqtt_host=_mqtt_host,
                mqtt_port=port,
                mqtt_user=user,
                mqtt_password=pwd,
                topic_prefix=prefix,
                model_dir=_model_dir if _model_dir.is_dir() else None,
                on_settings_changed=on_changed,
            )
            _recorder.start()
        else:
            print(
                "[edge] No RTSP URL: set SURVEILLANCE_PI_CAMERA=1 to enable the "
                "built-in publisher, or SURVEILLANCE_RTSP_URL to point at an "
                "external source. Recorder not started."
            )
        yield
    finally:
        if _recorder is not None:
            _recorder.stop()
            _recorder = None
        if _zc_pub is not None:
            _zc_pub.unregister()
            _zc_pub = None
        if _publisher is not None:
            _publisher.stop()
            _publisher = None


app = FastAPI(title="Surveillance Edge Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    pub_snapshot = _publisher.snapshot() if _publisher is not None else {
        "enabled": False,
        "running": False,
        "binary": None,
        "loopback_url": None,
        "lan_url": None,
        "cam_id": None,
        "quality": None,
        "flip_180": None,
        "bind_port": None,
    }
    return {
        "role": "edge",
        "camera_mqtt_id": _cam_id,
        "controller_pi5_mqtt_host": _mqtt_host,
        "rtsp_configured": bool(_effective_rtsp),
        "rtsp_env_set": bool(_rtsp_explicit),
        "effective_rtsp_url": _effective_rtsp,
        "rtsp_source": "publisher"
        if (not _rtsp_explicit and _effective_rtsp)
        else ("operator" if _rtsp_explicit else "none"),
        "model_dir": str(_model_dir.resolve()),
        "model_files_present": bool(
            (_model_dir / "MobileNetSSD_deploy.prototxt").is_file()
            and (_model_dir / "mobilenet_iter_73000.caffemodel").is_file()
        ),
        "recordings_dir": str(_rec_root.resolve()),
        "mdns_service": "_vigilance-edge._tcp.local.",
        "edge_http_port": _http_port,
        "edge_display_name": _edge_display_name,
        "mediamtx_path": _mediamtx_path,
        "publisher_enabled": bool(pub_snapshot.get("enabled")),
        "publisher_running": bool(pub_snapshot.get("running")),
        "publisher_url": pub_snapshot.get("loopback_url"),
        "publisher_loopback_url": pub_snapshot.get("loopback_url"),
        "publisher_lan_url": pub_snapshot.get("lan_url"),
        "mediamtx_binary": pub_snapshot.get("binary") or "",
    }


@app.get("/recordings/names")
def list_all_recording_names() -> List[str]:
    """All .mp4 basenames on disk (including corrupt/partial); used for bulk delete fallback."""
    root = _rec_root
    root.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for p in root.glob("*.mp4"):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if _SAFE_NAME.match(p.name):
            names.append(p.name)
    return sorted(names, reverse=True)


@app.get("/recordings")
def list_recordings() -> List[dict[str, Any]]:
    root = _rec_root
    root.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if not _SAFE_NAME.match(p.name):
            continue
        if not mp4_probe_ok(p):
            continue
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return out


@app.get("/recordings/files/{filename}")
def get_file(filename: str):
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = _rec_root / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if not mp4_probe_ok(path):
        raise HTTPException(
            status_code=422,
            detail="recording incomplete or corrupt",
        )
    # Do not auto-finalize on GET (thumbnails fire many range requests). Use POST finalize-mobile.
    if not mp4_ios_playable(path):
        raise HTTPException(
            status_code=422,
            detail="clip needs conversion for mobile playback",
        )
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Accept-Ranges": "bytes",
        },
    )


@app.post("/recordings/files/{filename}/finalize-mobile")
def finalize_file_for_mobile(filename: str):
    """Re-mux/re-encode an existing clip for iOS/Android HTML5 playback."""
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = _rec_root / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if not mp4_probe_ok(path):
        raise HTTPException(
            status_code=422,
            detail="recording incomplete or corrupt; re-record this clip",
        )
    if not finalize_mp4_for_mobile(path):
        raise HTTPException(
            status_code=422,
            detail="could not repair clip for mobile playback",
        )
    return {"ok": True, "filename": filename}


@app.delete("/recordings/files/{filename}")
def delete_file(filename: str):
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = _rec_root / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    path.unlink()
    return {"ok": True}


@app.delete("/recordings/all")
def delete_all_files():
    """Remove every safe .mp4 clip in the edge recordings directory."""
    root = _rec_root
    root.mkdir(parents=True, exist_ok=True)
    deleted = 0
    failed: list[str] = []
    for p in root.glob("*.mp4"):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if not _SAFE_NAME.match(p.name):
            continue
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            failed.append(p.name)
            logger.warning("delete_all: could not remove %s: %s", p.name, e)
    return {"ok": len(failed) == 0, "deleted": deleted, "failed": failed}


@app.post("/recordings/manual/start")
def manual_record_start():
    if _recorder is None:
        raise HTTPException(status_code=503, detail="recorder not running")
    try:
        return _recorder.start_manual_recording()
    except ValueError as e:
        msg = str(e)
        code = 409 if "already active" in msg.lower() else 400
        raise HTTPException(status_code=code, detail=msg) from e


@app.post("/recordings/manual/stop")
def manual_record_stop():
    if _recorder is None:
        raise HTTPException(status_code=503, detail="recorder not running")
    return _recorder.stop_manual_recording()


@app.get("/recordings/manual/status")
def manual_record_status():
    if _recorder is None:
        return {"active": False, "filename": None}
    return _recorder.manual_recording_status()


def _parse_motion_clip_body(body: Optional[dict[str, Any]]) -> dict[str, Any]:
    pre = None
    post = None
    tags: Optional[list[str]] = None
    if isinstance(body, dict):
        if body.get("pre_record_seconds") is not None:
            pre = int(body["pre_record_seconds"])
        if body.get("post_record_seconds") is not None:
            post = int(body["post_record_seconds"])
        raw_tags = body.get("objects_detected")
        if isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags if t is not None]
    return {"pre_seconds": pre, "post_seconds": post, "objects_detected": tags}


@app.post("/recordings/motion/trigger")
async def motion_clip_trigger(body: Optional[dict[str, Any]] = None):
    if _recorder is None:
        raise HTTPException(status_code=503, detail="recorder not running")
    parsed = _parse_motion_clip_body(body)
    try:
        return await asyncio.to_thread(
            _recorder.trigger_motion_clip,
            pre_seconds=parsed["pre_seconds"],
            post_seconds=parsed["post_seconds"],
            objects_detected=parsed["objects_detected"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/recordings/motion/status")
async def motion_clip_status():
    if _recorder is None:
        return {"active": False, "phase": "idle", "remaining_seconds": 0}
    return await asyncio.to_thread(_recorder.motion_clip_status)


@app.get("/settings")
def get_settings():
    if _recorder is None:
        return {
            "recording_mode": "motion",
            "pre_record_seconds": 10,
            "post_record_seconds": 50,
            "quality": "medium",
            "flip_180": False,
            "note": (
                "recorder offline — set SURVEILLANCE_RTSP_URL or SURVEILLANCE_PI_CAMERA=1 "
                "with mediamtx installed (see docs/SETUP_PI4.md)"
            ),
        }
    return _recorder.snapshot_settings()


@app.patch("/settings")
def patch_settings(body: dict[str, Any]):
    if _recorder is None:
        raise HTTPException(status_code=503, detail="recorder not running")
    allowed = {
        k: body[k]
        for k in ("recording_mode", "pre_record_seconds", "post_record_seconds", "quality", "flip_180")
        if k in body
    }
    if not allowed:
        return _recorder.snapshot_settings()
    return _recorder.update_settings(allowed)
