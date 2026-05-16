"""Pi 4 edge HTTP API: recordings CRUD + settings; starts EdgeRecorder thread."""

from __future__ import annotations

from . import env_loader  # noqa: F401  # loads edge-agent/.env

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

from . import _shared_bootstrap  # noqa: F401

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .worker import EdgeRecorder
from .zeroconf_publish import EdgeZeroconfPublisher

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")

# Raspberry Pi 5 controller (MQTT). Override with SURVEILLANCE_MQTT_HOST.
_CONTROLLER_PI5_IP = "192.168.2.104"


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else str(v)


_EDGE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL_DIR = _EDGE_ROOT / "models"

_rtsp = _env("SURVEILLANCE_RTSP_URL", "").strip()
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _recorder, _zc_pub
    _zc_pub = None
    try:
        _zc_pub = EdgeZeroconfPublisher(
            camera_id=_cam_id,
            display_name=_edge_display_name,
            location=_edge_location,
            rtsp_url=_rtsp,
            mediamtx_path=_mediamtx_path,
            http_port=_http_port,
        )
        _zc_pub.register()
    except Exception as exc:
        print("[edge] Zeroconf registration failed:", exc)

    try:
        if _rtsp:
            port = int(_env("SURVEILLANCE_MQTT_PORT", "1883"))
            user = _env("SURVEILLANCE_MQTT_USER") or None
            pwd = _env("SURVEILLANCE_MQTT_PASSWORD") or None
            prefix = _env("SURVEILLANCE_MQTT_TOPIC_PREFIX", "surveillance/cameras").strip()
            _recorder = EdgeRecorder(
                camera_mqtt_id=_cam_id,
                rtsp_url=_rtsp,
                recordings_root=_rec_root,
                mqtt_host=_mqtt_host,
                mqtt_port=port,
                mqtt_user=user,
                mqtt_password=pwd,
                topic_prefix=prefix,
                model_dir=_model_dir if _model_dir.is_dir() else None,
            )
            _recorder.start()
        else:
            print("[edge] SURVEILLANCE_RTSP_URL not set; recorder not started")
        yield
    finally:
        if _recorder is not None:
            _recorder.stop()
            _recorder = None
        if _zc_pub is not None:
            _zc_pub.unregister()
            _zc_pub = None


app = FastAPI(title="Surveillance Edge Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "role": "edge",
        "camera_mqtt_id": _cam_id,
        "controller_pi5_mqtt_host": _mqtt_host,
        "rtsp_configured": bool(_rtsp),
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
    }


@app.get("/recordings")
def list_recordings() -> List[dict[str, Any]]:
    root = _rec_root
    root.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith("_") or p.name.startswith("."):
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
    return FileResponse(path, media_type="video/mp4", filename=filename)


@app.delete("/recordings/files/{filename}")
def delete_file(filename: str):
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = _rec_root / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    path.unlink()
    return {"ok": True}


@app.get("/settings")
def get_settings():
    if _recorder is None:
        return {
            "recording_mode": "motion",
            "pre_record_seconds": 10,
            "post_record_seconds": 50,
            "quality": "medium",
            "flip_180": False,
            "note": "recorder offline — set SURVEILLANCE_RTSP_URL (MQTT defaults to Pi 5 at 192.168.2.104)",
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


