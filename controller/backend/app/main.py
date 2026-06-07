"""
Minimal SmartCam controller API so the Vigilance UI can list cameras from
`camera_store` (JSON file, env, or bootstrap). Extend with MediaMTX, recordings,
and detection routes on the full Pi image.

Run from `controller/backend` with:
  export PYTHONPATH="$(pwd)/../shared:$(pwd)"   # or ../shared only if present
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import camera_store
from .mediamtx_paths import mediamtx_path_key

DEFAULT_SETTINGS: Dict[str, Any] = {
    "recording_mode": "motion",
    "pre_record_seconds": 10,
    "post_record_seconds": 50,
    "quality": "medium",
    "flip_180": False,
}

# Per-camera overrides (not persisted to disk in this minimal app).
_settings_overrides: Dict[int, Dict[str, Any]] = {}


def _serialize_camera(row: Dict[str, Any]) -> Dict[str, Any]:
    cid = int(row["id"])
    out = dict(row)
    merged = {**DEFAULT_SETTINGS, **(row.get("settings") or {}), **(_settings_overrides.get(cid, {}))}
    out["settings"] = merged
    return out


def _next_camera_id() -> int:
    rows = camera_store.list_cameras()
    if not rows:
        return 0
    return max(int(c["id"]) for c in rows) + 1


app = FastAPI(title="SmartCam controller", version="0.1.0")

_cors = os.environ.get("SMARTCAM_CORS_ORIGINS", "*").strip()
if _cors == "*":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/")
def root() -> Dict[str, Any]:
    """Always JSON — use `curl -s http://127.0.0.1:8000/` to verify something is listening."""
    return {
        "service": "smartcam-controller",
        "ok": True,
        "docs": "/docs",
        "health": "/health",
        "cameras": "/cameras",
    }


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/cameras", response_model=List[Dict[str, Any]])
def list_cameras() -> List[Dict[str, Any]]:
    return [_serialize_camera(dict(c)) for c in camera_store.list_cameras()]


@app.post("/cameras")
def create_camera(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    cid = body.get("id")
    if cid is None:
        cid = _next_camera_id()
    cid = int(cid)
    url = str(body.get("url") or body.get("main_stream") or body.get("mainStream") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing url or main_stream (RTSP)")
    row: Dict[str, Any] = {
        "id": cid,
        "name": str(body.get("name") or f"Camera {cid}").strip() or f"Camera {cid}",
        "url": url,
        "edge_base_url": str(body.get("edge_base_url") or "").strip() or None,
        "mqtt_camera_id": body.get("mqtt_camera_id"),
        "mediamtx_path": body.get("mediamtx_path"),
        "status": body.get("status") or "online",
    }
    for k in ("ip", "type", "manufacturer", "model", "resolution", "ai_enabled", "sub_stream", "main_stream"):
        if k in body and body[k] is not None:
            row[k] = body[k]
    camera_store.add_camera(row)
    return _serialize_camera(camera_store.get_camera(cid) or row)


@app.delete("/cameras/{camera_id}")
def delete_camera(camera_id: int) -> Dict[str, str]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera_store.remove_camera(camera_id)
    _settings_overrides.pop(int(camera_id), None)
    return {"status": "deleted"}


@app.patch("/cameras/{camera_id}")
def patch_camera(camera_id: int, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    updates = {k: v for k, v in body.items() if k in ("name", "url", "edge_base_url", "main_stream", "mediamtx_path", "mqtt_camera_id") and v is not None}
    if "edge_base_url" in updates and updates["edge_base_url"] == "":
        updates["edge_base_url"] = None
    if "main_stream" in updates and not str(body.get("url") or "").strip():
        updates["url"] = str(updates.pop("main_stream")).strip()
    if updates:
        camera_store.update_camera(camera_id, updates)
    cam = camera_store.get_camera(camera_id)
    return _serialize_camera(dict(cam or {}))


@app.get("/cameras/{camera_id}/settings")
def get_settings(camera_id: int) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {**DEFAULT_SETTINGS, **(_settings_overrides.get(int(camera_id), {}))}


@app.patch("/cameras/{camera_id}/settings")
def patch_settings(camera_id: int, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    prev = _settings_overrides.setdefault(int(camera_id), {})
    for k in DEFAULT_SETTINGS:
        if k in body:
            prev[k] = body[k]
    return {**DEFAULT_SETTINGS, **prev}


@app.get("/cameras/{camera_id}/events")
def list_events(camera_id: int) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"events": []}


@app.get("/recordings")
def list_recordings(limit: int = 1000) -> Dict[str, Any]:
    return {"recordings": []}


@app.post("/recordings/sync")
def recordings_sync() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/detect/edges")
def detect_edges() -> List[Any]:
    return []


@app.get("/cameras/{camera_id}/recordings/manual/status")
def manual_status(camera_id: int) -> Dict[str, bool]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"active": False}


@app.post("/cameras/{camera_id}/recordings/manual/{path}")
def manual_record(camera_id: int, path: str) -> Dict[str, Any]:
    if path not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="path must be start or stop")
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"ok": True, "active": path == "start"}


@app.get("/cameras/{camera_id}/stream_health")
def stream_health(camera_id: int, probe_rtsp: bool = True) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {
        "ok": None,
        "summary": [
            "Minimal build: no RTSP probe. HLS uses GET /cameras/{id}/hls/index.m3u8 → "
            "307 redirect to MediaMTX (:8888). Set SMARTCAM_HLS_ORIGIN if HLS is not on the API host."
        ],
    }


def _hls_public_base(request: Request) -> str:
    """Origin for MediaMTX HLS (browser follows 307 here)."""
    env = os.environ.get("SMARTCAM_HLS_ORIGIN", "").strip().rstrip("/")
    if env:
        return env
    port_raw = os.environ.get("SMARTCAM_HLS_PORT", "8888").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 8888
    if port < 1 or port > 65535:
        port = 8888
    u = request.url
    host = u.hostname or "127.0.0.1"
    scheme = u.scheme or "http"
    return f"{scheme}://{host}:{port}"


@app.get("/cameras/{camera_id}/hls/index.m3u8")
def hls_playlist_redirect(camera_id: int, request: Request) -> RedirectResponse:
    """Redirect to MediaMTX so hls.js can use same-origin API URL first, then follow to :8888."""
    row = camera_store.get_camera(camera_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    cam = dict(row)
    path = mediamtx_path_key(cam)
    target = f"{_hls_public_base(request)}/{path}/index.m3u8"
    return RedirectResponse(url=target, status_code=307)


@app.websocket("/ws/recording")
async def ws_recording(ws: WebSocket) -> None:
    await ws.accept()
    try:
        await ws.send_json({"cameras": {}})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        return


@app.websocket("/ws/detections")
async def ws_detections(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json(
        {
            "type": "hello",
            "recording_sample_ms": 500,
            "person_trigger_min_frames": 3,
            "face_count": 0,
            "backend": "minimal",
            "inference_delay_ms": 0,
            "hailo_ready": False,
            "hailo_error": None,
        }
    )
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        return
