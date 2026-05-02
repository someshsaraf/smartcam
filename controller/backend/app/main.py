from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import camera_store, mqtt_bridge
from .discovery import discover, discover_edge_agents
from .detector import get_detector_diagnostics
from .recording_manager import RECORDINGS_ROOT, recording_manager
from .stream import generate_frames

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    mqtt_bridge.init_bridge_from_env(loop)
    recording_manager.start()
    yield
    recording_manager.stop()
    mqtt_bridge.shutdown_bridge()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")


class CameraCreate(BaseModel):
    name: str
    location: str
    url: str
    edge_base_url: Optional[str] = None
    mediamtx_path: Optional[str] = None
    mqtt_camera_id: Optional[str] = None


class CameraSettingsPatch(BaseModel):
    recording_mode: Optional[str] = None
    pre_record_seconds: Optional[int] = None
    post_record_seconds: Optional[int] = None
    quality: Optional[str] = None
    flip_180: Optional[bool] = None


def _recordings_dir(cam_id: int) -> Path:
    d = RECORDINGS_ROOT / str(int(cam_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _push_edge_settings(cam_id: int) -> None:
    c = camera_store.get_camera(cam_id)
    if not c:
        return
    edge = camera_store.edge_base_url(c)
    if not edge:
        return
    settings = c.get("settings", {})
    try:
        r = httpx.patch(f"{edge}/settings", json=settings, timeout=20.0)
        r.raise_for_status()
    except Exception as e:
        logger.warning("edge settings push failed cam_id=%s: %s", cam_id, e)


# =========================
# Camera Management
# =========================


@app.post("/cameras")
def add_camera_endpoint(cam: CameraCreate):
    try:
        return camera_store.add_camera(cam.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/cameras")
def list_cameras_endpoint():
    return camera_store.list_cameras()


@app.post("/cameras/select/{cam_id}")
def select_camera_endpoint(cam_id: int):
    sel = camera_store.select_camera(cam_id)
    if sel is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return sel


@app.get("/cameras/selected")
def get_selected_camera_endpoint():
    return camera_store.get_selected_camera()


@app.get("/cameras/{cam_id}/settings")
def get_camera_settings(cam_id: int):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    return c.get("settings", {})


@app.patch("/cameras/{cam_id}/settings")
def patch_camera_settings(cam_id: int, body: CameraSettingsPatch):
    patch: dict[str, Any] = body.model_dump(exclude_unset=True)
    if not patch:
        c = camera_store.get_camera(cam_id)
        if not c:
            raise HTTPException(status_code=404, detail="camera not found")
        return c.get("settings", {})
    try:
        cam = camera_store.update_camera_settings(cam_id, patch)
        _push_edge_settings(cam_id)
        return cam["settings"]
    except KeyError:
        raise HTTPException(status_code=404, detail="camera not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# =========================
# Discovery
# =========================


@app.get("/detect")
def detect():
    discovered = discover()
    existing_urls = [c["url"] for c in camera_store.list_cameras()]
    return [c for c in discovered if c["url"] not in existing_urls]


@app.get("/detect/edges")
def detect_edges():
    found = discover_edge_agents()
    cams = camera_store.list_cameras()
    existing_edge = {camera_store.edge_base_url(c) for c in cams if camera_store.edge_base_url(c)}
    existing_mqtt = {camera_store.mqtt_id_for_camera(c) for c in cams}
    out: list[dict] = []
    for e in found:
        if e.get("edge_base_url") in existing_edge:
            continue
        if e.get("mqtt_camera_id") in existing_mqtt:
            continue
        out.append(e)
    return out


# =========================
# Stream (legacy single-camera MJPEG)
# =========================


@app.get("/stream")
def stream():
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# =========================
# WebSocket recording state (from MQTT bridge)
# =========================


@app.websocket("/ws/recording")
async def ws_recording(ws: WebSocket):
    await ws.accept()
    bridge = mqtt_bridge.get_bridge()
    hub = bridge.ws_hub if bridge else None
    if hub:
        await hub.register(ws)
        try:
            await ws.send_json(bridge.snapshot())
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.unregister(ws)
    else:
        await ws.send_json({"cameras": {}})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass


# =========================
# Recordings (local controller disk or proxied edge)
# =========================


@app.get("/recordings/{cam_id}")
def list_recordings_endpoint(cam_id: int) -> List[dict[str, Any]]:
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    edge = camera_store.edge_base_url(c)
    if edge:
        try:
            r = httpx.get(f"{edge}/recordings", timeout=30.0)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError("edge returned non-list")
            return data
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"edge list failed: {e}") from e

    d = _recordings_dir(cam_id)
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return out


@app.get("/recordings/{cam_id}/files/{filename}")
def get_recording_file(cam_id: int, filename: str):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")

    edge = camera_store.edge_base_url(c)
    if edge:
        url = f"{edge}/recordings/files/{filename}"

        def gen():
            try:
                with httpx.stream("GET", url, timeout=120.0) as r:
                    r.raise_for_status()
                    for chunk in r.iter_bytes():
                        yield chunk
            except Exception as e:
                logger.warning("edge stream failed: %s", e)
                raise

        return StreamingResponse(gen(), media_type="video/mp4")

    path = _recordings_dir(cam_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)


@app.delete("/recordings/{cam_id}/files/{filename}")
def delete_recording_file(cam_id: int, filename: str):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")

    edge = camera_store.edge_base_url(c)
    if edge:
        try:
            r = httpx.delete(f"{edge}/recordings/files/{filename}", timeout=30.0)
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail="not found")
            r.raise_for_status()
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    path = _recordings_dir(cam_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    try:
        path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True}


# =========================
# Health / diagnostics
# =========================


@app.get("/system/recording")
def system_recording():
    ff = shutil.which("ffmpeg")
    bridge = mqtt_bridge.get_bridge()
    return {
        "recordings_root": str(RECORDINGS_ROOT.resolve()),
        "ffmpeg_path": ff,
        "ffmpeg_available": ff is not None,
        "opencv_ffmpeg_capture_options": os.environ.get(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS"
        ),
        "detector": get_detector_diagnostics(),
        "workers": recording_manager.worker_status(),
        "mqtt_bridge": bool(bridge),
        "mqtt_host_configured": bool(os.environ.get("CONTROLLER_MQTT_HOST", "").strip()),
    }


@app.get("/")
def root():
    return {"status": "running", "role": "controller"}
