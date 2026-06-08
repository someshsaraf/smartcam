"""
SmartCam controller API: cameras from `camera_store`, MediaMTX HLS redirect,
OpenCV person detection → `/ws/detections`, manual RTSP recording (ffmpeg on-controller
or proxy to Pi edge when the stream host matches the edge), and aggregated recordings listing + file download.

Run from `controller/backend` with:
  export PYTHONPATH="$(pwd)/../shared:$(pwd)"   # or ../shared only if present
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

import httpx

from . import camera_store
from .camera_discovery import discover_vigilance_edges, run_camera_discovery
from .detections_hub import get_detections_hub
from .ffmpeg_mobile import finalize_mp4_for_mobile, mp4_ios_playable, mp4_listable_fast
from .manual_recording import (
    manual_status_local,
    recordings_dir_for,
    resolve_recording_file,
    start_manual_local,
    stop_manual_local,
)
from .mediamtx_paths import mediamtx_path_key, rtsp_url, rtsp_url_has_userinfo
from .opencv_person_detector import person_detector_diagnostics
from .person_rtsp_supervisor import (
    get_supervisor_thread,
    person_detection_enabled,
    start_person_detection_background,
)
from .recording_thumbnails import (
    ensure_recording_thumbnail,
    recording_thumbnail_path,
    remove_recording_thumbnail,
)
from .recordings_catalog import list_merged_recordings

DEFAULT_SETTINGS: Dict[str, Any] = {
    "recording_mode": "motion",
    "pre_record_seconds": 10,
    "post_record_seconds": 50,
    "quality": "medium",
    "flip_180": False,
}


def _serialize_camera(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    merged = {**DEFAULT_SETTINGS, **(row.get("settings") or {})}
    out["settings"] = merged
    return out


def _next_camera_id() -> int:
    rows = camera_store.list_cameras()
    if not rows:
        return 0
    return max(int(c["id"]) for c in rows) + 1


def _effective_settings(camera_id: int) -> Dict[str, Any]:
    row = camera_store.get_camera(camera_id)
    if row is None:
        return dict(DEFAULT_SETTINGS)
    return {**DEFAULT_SETTINGS, **(dict(row).get("settings") or {})}


def _camera_and_edge(camera_id: int) -> tuple[Dict[str, Any], str | None]:
    row = camera_store.get_camera(camera_id)
    if row is None:
        return {}, None
    d = dict(row)
    u = str(d.get("edge_base_url") or "").strip().rstrip("/")
    if u.startswith(("http://", "https://")):
        return d, u
    return d, None


logger = logging.getLogger(__name__)


def _edge_connect_failed(exc: BaseException) -> bool:
    """True when the edge URL could not be reached (e.g. ECONNREFUSED), not HTTP errors."""
    if isinstance(exc, httpx.ConnectError):
        return True
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, OSError) and getattr(cur, "errno", None) == errno.ECONNREFUSED:
            return True
        cur = cur.__cause__ if cur.__cause__ is not None else cur.__context__
    return False


def _edge_unreachable_detail(edge: str, exc: Exception) -> str:
    return (
        f"Cannot reach edge at {edge}: {exc!s}. "
        "Start the edge agent, fix edge_base_url or the network, or clear edge_base_url "
        "to record on the controller only (requires ffmpeg and a camera RTSP URL)."
    )


def _has_rtsp_for_local(cam: Dict[str, Any]) -> bool:
    u = rtsp_url(cam).strip()
    return u.startswith(("rtsp://", "rtsps://"))


def _url_hostname(u: str) -> str | None:
    t = (u or "").strip()
    if not t:
        return None
    try:
        h = urlparse(t).hostname
        return str(h).strip().lower() if h else None
    except Exception:
        return None


def _manual_proxy_to_edge(cam: Dict[str, Any], edge: str) -> bool:
    """
    When edge_base_url is set, normally manual start/stop is proxied to the edge agent.

    If the camera row's RTSP URL points at a *different* host than the edge (typical LAN
    commercial camera such as VIGI while edge_base_url is metadata for another Pi), run
    manual capture on the controller with ffmpeg instead of calling the edge.

    If there is no RTSP URL on the row, keep proxying so the edge uses its own stream.
    Loopback RTSP hosts always proxy to edge when an edge URL exists (edge-local streams).
    """
    rtsp = rtsp_url(cam).strip()
    if not rtsp.startswith(("rtsp://", "rtsps://")):
        return True
    rtsp_h = _url_hostname(rtsp)
    edge_h = _url_hostname(edge)
    if not rtsp_h or not edge_h:
        return True
    if rtsp_h in ("127.0.0.1", "localhost", "::1"):
        return True
    return rtsp_h == edge_h


def _httpx_error_detail(r: httpx.Response) -> str:
    try:
        data = r.json()
    except Exception:
        return ((r.text or "").strip() or f"HTTP {r.status_code}")[:4000]
    if isinstance(data, dict):
        d = data.get("detail")
        if isinstance(d, str):
            return d
        if isinstance(d, list):
            return str(d)
        if d is not None:
            return str(d)
        return str(data)[:4000]
    return str(data)[:4000]


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = get_detections_hub()
    hub.attach_loop(asyncio.get_running_loop())
    stop_ev = threading.Event()
    start_person_detection_background(hub, stop_ev)
    yield
    stop_ev.set()
    sup = get_supervisor_thread()
    if sup is not None and sup.is_alive():
        sup.join(timeout=15.0)


app = FastAPI(title="SmartCam controller", version="0.1.0", lifespan=lifespan)

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
    camera_store.persist_cameras_to_json()
    return _serialize_camera(camera_store.get_camera(cid) or row)


@app.delete("/cameras/{camera_id}")
def delete_camera(camera_id: int) -> Dict[str, str]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera_store.remove_camera(camera_id)
    camera_store.persist_cameras_to_json()
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
        camera_store.persist_cameras_to_json()
    cam = camera_store.get_camera(camera_id)
    return _serialize_camera(dict(cam or {}))


@app.get("/cameras/{camera_id}/settings")
def get_settings(camera_id: int) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return _effective_settings(int(camera_id))


@app.patch("/cameras/{camera_id}/settings")
def patch_settings(camera_id: int, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    row = dict(camera_store.get_camera(camera_id) or {})
    cur = dict(row.get("settings") or {})
    for k in DEFAULT_SETTINGS:
        if k in body:
            cur[k] = body[k]
    camera_store.update_camera(camera_id, {"settings": cur})
    if not camera_store.persist_cameras_to_json():
        raise HTTPException(
            status_code=500,
            detail="Settings updated in memory but could not write cameras.json "
            "(check SMARTCAM_CAMERAS_JSON path and permissions).",
        )
    return _effective_settings(int(camera_id))


@app.get("/cameras/{camera_id}/events")
def list_events(camera_id: int) -> Dict[str, Any]:
    """
    Person / timeline events (stub: always empty).

    Returns 200 with an empty list even when ``camera_id`` is unknown so UIs do not
    spam 404s during reload races; persist only valid ids in ``cameras.json``.
    """
    if camera_store.get_camera(camera_id) is None:
        return {"events": [], "unknown_camera": True}
    return {"events": []}


@app.get("/recordings")
def list_recordings(limit: int = 1000) -> Dict[str, Any]:
    return {"recordings": list_merged_recordings(limit)}


@app.post("/recordings/sync")
def recordings_sync() -> Dict[str, str]:
    """Catalog is built on demand; sync is a no-op for compatibility with the UI."""
    return {"status": "ok"}


@app.get("/detect/edges")
def detect_edges() -> List[Any]:
    """mDNS browse for Pi edge agents (same entries as ``devices`` where ``kind`` == ``edge``)."""
    try:
        return discover_vigilance_edges(browse_sec=4.0)
    except Exception as e:
        logger.warning("[detect/edges] %s", e)
        return []


@app.post("/cameras/discover")
def discover_cameras(body: Optional[Dict[str, Any]] = Body(None)) -> Dict[str, Any]:
    """
    ONVIF WS-Discovery + stream URI (VIGI / ONVIF cameras), plus mDNS for Vigilance edge agents.

    Optional JSON body: ``username``, ``password``, ``timeout_seconds``, ``scan_onvif``, ``scan_edges``.
    Password is required on most cameras to resolve RTSP URLs; omit or empty to list devices only.
    """
    if body is not None and not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    return run_camera_discovery(body if isinstance(body, dict) else {})


@app.get("/cameras/{camera_id}/recordings/manual/status")
def manual_status(camera_id: int) -> Dict[str, Any]:
    row = camera_store.get_camera(camera_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    cam = dict(row)
    _, edge = _camera_and_edge(camera_id)
    if edge and _manual_proxy_to_edge(cam, edge):
        try:
            r = httpx.get(f"{edge}/recordings/manual/status", timeout=10.0)
        except httpx.RequestError as e:
            if _edge_connect_failed(e):
                return manual_status_local(int(camera_id))
            raise HTTPException(
                status_code=502, detail=_edge_unreachable_detail(edge, e)
            ) from e
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=_httpx_error_detail(r))
        try:
            return r.json()
        except Exception:
            return {"active": False, "filename": None}
    return manual_status_local(int(camera_id))


@app.post("/cameras/{camera_id}/recordings/manual/{path}")
def manual_record(camera_id: int, path: str) -> Dict[str, Any]:
    if path not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="path must be start or stop")
    row = camera_store.get_camera(camera_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    cam = dict(row)
    settings = _effective_settings(int(camera_id))
    _, edge = _camera_and_edge(camera_id)
    proxy_edge = bool(edge) and _manual_proxy_to_edge(cam, edge)
    use_local = not proxy_edge
    if proxy_edge:
        url = f"{edge}/recordings/manual/{path}"
        try:
            r = httpx.post(url, timeout=120.0)
        except httpx.RequestError as e:
            if _has_rtsp_for_local(cam) and _edge_connect_failed(e):
                logger.warning(
                    "edge %s unreachable for manual %s; using controller-local ffmpeg",
                    edge,
                    path,
                )
                use_local = True
            else:
                raise HTTPException(
                    status_code=502, detail=_edge_unreachable_detail(edge, e)
                ) from e
        else:
            if r.status_code >= 400:
                raise HTTPException(
                    status_code=r.status_code,
                    detail=_httpx_error_detail(r),
                )
            try:
                return r.json()
            except Exception:
                return {"ok": True, "active": path == "start"}
    if use_local:
        try:
            if path == "start":
                return start_manual_local(
                    int(camera_id),
                    cam,
                    recording_mode=str(settings.get("recording_mode", "motion")),
                    flip_180=bool(settings.get("flip_180")),
                )
            return stop_manual_local(int(camera_id))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/recordings/{camera_id}/files/{filename}")
def get_recording_file(camera_id: int, filename: str, playback: bool = False):
    path = resolve_recording_file(int(camera_id), filename)
    if path is not None:
        if not mp4_listable_fast(path):
            raise HTTPException(
                status_code=422, detail="recording incomplete or corrupt"
            )
        if playback and not mp4_ios_playable(path):
            if not finalize_mp4_for_mobile(path):
                raise HTTPException(
                    status_code=422,
                    detail="could not repair clip for mobile playback",
                )
        return FileResponse(
            path,
            media_type="video/mp4",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    _, edge = _camera_and_edge(camera_id)
    if not edge:
        raise HTTPException(status_code=404, detail="not found")
    q = "?playback=1" if playback else ""
    return RedirectResponse(
        url=f"{edge}/recordings/files/{filename}{q}",
        status_code=307,
    )


@app.get("/recordings/{camera_id}/files/{filename}/thumbnail")
def get_recording_thumbnail(camera_id: int, filename: str):
    path = resolve_recording_file(int(camera_id), filename)
    if path is not None:
        thumb = recording_thumbnail_path(path)
        if thumb.is_file():
            return FileResponse(thumb, media_type="image/jpeg")
        if ensure_recording_thumbnail(path, seek_seconds=1.0) and thumb.is_file():
            return FileResponse(thumb, media_type="image/jpeg")
        raise HTTPException(status_code=404, detail="thumbnail unavailable")
    _, edge = _camera_and_edge(camera_id)
    if not edge:
        raise HTTPException(status_code=404, detail="not found")
    return RedirectResponse(
        url=f"{edge}/recordings/files/{filename}/thumbnail",
        status_code=307,
    )


@app.post("/recordings/{camera_id}/files/{filename}/finalize-mobile")
def finalize_recording_mobile(camera_id: int, filename: str) -> Dict[str, Any]:
    path = resolve_recording_file(int(camera_id), filename)
    if path is not None:
        if finalize_mp4_for_mobile(path):
            return {"ok": True}
        raise HTTPException(
            status_code=422,
            detail="could not repair clip for mobile playback",
        )
    _, edge = _camera_and_edge(camera_id)
    if not edge:
        raise HTTPException(status_code=404, detail="not found")
    try:
        r = httpx.post(
            f"{edge}/recordings/files/{filename}/finalize-mobile",
            timeout=300.0,
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_httpx_error_detail(r))
    try:
        return r.json()
    except Exception:
        return {"ok": True}


@app.delete("/recordings/{camera_id}/files/{filename}")
def delete_recording_file(camera_id: int, filename: str) -> Dict[str, str]:
    path = resolve_recording_file(int(camera_id), filename)
    if path is not None:
        remove_recording_thumbnail(path)
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"status": "deleted"}
    _, edge = _camera_and_edge(camera_id)
    if not edge:
        raise HTTPException(status_code=404, detail="not found")
    try:
        r = httpx.delete(f"{edge}/recordings/files/{filename}", timeout=30.0)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_httpx_error_detail(r))
    return {"status": "deleted"}


@app.delete("/recordings/{camera_id}/all")
def delete_all_recordings(camera_id: int) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    deleted = 0
    root = recordings_dir_for(int(camera_id))
    if root.is_dir():
        for p in root.glob("*.mp4"):
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            try:
                remove_recording_thumbnail(p)
                p.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                pass
    _, edge = _camera_and_edge(camera_id)
    if edge:
        try:
            r = httpx.delete(f"{edge}/recordings/all", timeout=60.0)
            if r.status_code == 200:
                try:
                    body = r.json()
                    if isinstance(body, dict) and isinstance(body.get("deleted"), int):
                        deleted += int(body["deleted"])
                except Exception:
                    pass
        except httpx.RequestError:
            pass
    return {"ok": True, "deleted": deleted}


@app.get("/cameras/{camera_id}/stream_health")
def stream_health(camera_id: int, probe_rtsp: bool = True) -> Dict[str, Any]:
    if camera_store.get_camera(camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    row = dict(camera_store.get_camera(camera_id) or {})
    ru = rtsp_url(row)
    path_key = mediamtx_path_key(row)
    warnings: List[str] = []
    if ru.startswith(("rtsp://", "rtsps://")) and not rtsp_url_has_userinfo(ru):
        warnings.append(
            "RTSP URL has no username. TP-Link VIGI and most IP cameras return 401 to MediaMTX "
            "until the URL includes credentials, e.g. rtsp://admin:password@192.168.x.x:554/stream1 "
            "(special characters in the password must be percent-encoded in the URL)."
        )
    diag = person_detector_diagnostics()
    ssd = bool(diag.get("model_load_ok"))
    lines = [
        "HLS: GET /cameras/{id}/hls/index.m3u8 → 307 to MediaMTX (:8888). "
        "Set SMARTCAM_HLS_ORIGIN if the HLS host differs from the API host.",
    ]
    if person_detection_enabled() and ssd:
        lines.append(
            "Person overlays: OpenCV MobileNet-SSD workers read each camera RTSP and "
            "broadcast on /ws/detections."
        )
    elif person_detection_enabled() and not ssd:
        lines.append(
            "Person detection enabled but SSD weights missing — run "
            "controller/backend/scripts/fetch_ssd_models.sh"
        )
    return {
        "ok": None,
        "summary": lines,
        "mediamtx_path": path_key,
        "rtsp_has_userinfo": rtsp_url_has_userinfo(ru),
        "warnings": warnings,
    }


@app.get("/detector/person/status")
def person_detector_status() -> Dict[str, Any]:
    """SSD model paths, load status, and WebSocket client count."""
    hub = get_detections_hub()
    diag = person_detector_diagnostics()
    return {
        "person_detection_enabled": person_detection_enabled(),
        "websocket_clients": hub.client_count(),
        **diag,
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
    hub = get_detections_hub()
    diag = person_detector_diagnostics()
    ssd_ready = bool(diag.get("model_load_ok"))
    await ws.accept()
    await hub.add(ws)
    try:
        await ws.send_json(
            {
                "type": "hello",
                "recording_sample_ms": 500,
                "person_trigger_min_frames": 3,
                "face_count": 0,
                "backend": "opencv_ssd" if ssd_ready else "minimal",
                "inference_delay_ms": 0,
                "hailo_ready": False,
                "hailo_error": None,
                "opencv_ssd_ready": ssd_ready,
                "person_detect_enabled": person_detection_enabled(),
                "person_pipeline": diag.get("pipeline"),
            }
        )
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)
