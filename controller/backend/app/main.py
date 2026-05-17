from __future__ import annotations

from . import env_loader  # noqa: F401  # loads controller/backend/.env

import asyncio
import logging
import os
import threading
import time
import re
import shutil
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import camera_store, live_detection, mediamtx_manager, mqtt_bridge
from .discovery import discover, discover_edge_agents
from .detector import get_detector_diagnostics
from .mediamtx_manager import start_embedded as mediamtx_start_embedded
from .mediamtx_manager import status_dict as mediamtx_status_dict
from .mediamtx_manager import restart_embedded as mediamtx_restart_embedded
from .mediamtx_manager import stop_embedded as mediamtx_stop_embedded
from .mosquitto_manager import ensure_broker_started
from .mosquitto_manager import status_dict as mosquitto_status_dict
from .mosquitto_manager import stop_managed_broker
from ._shared_path import ensure_shared_on_path
from .events_store import list_events
from .motion_recording import (
    cache_motion_status,
    fetch_edge_motion_status,
    motion_status_idle,
)
from .recording_manager import RECORDINGS_ROOT, recording_manager
from .stream import generate_frames

ensure_shared_on_path()
from surveillance_shared.ffmpeg_mobile import (  # noqa: E402
    finalize_mp4_for_mobile,
    mp4_ios_playable,
    mp4_probe_ok,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    ensure_broker_started()
    bridge = mqtt_bridge.init_bridge_from_env(loop)
    recording_manager.start()
    mediamtx_start_embedded()
    _push_all_edge_settings()
    if bridge is not None:
        bridge.reconcile_recording_state()
    live_detection.get_service().start(loop)
    threading.Timer(4.0, live_detection.get_service().restart_workers).start()
    yield
    live_detection.get_service().stop()  # releases Hailo before other shutdown
    mediamtx_stop_embedded()
    recording_manager.stop()
    mqtt_bridge.shutdown_bridge()
    stop_managed_broker()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")

_CHUNK = 1024 * 1024

# Long read timeout for MP4 proxy; client must stay open until the stream finishes.
_RECORDING_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# Headers iOS Safari needs for HTML5 MP4 playback (byte-range requests).
_RECORDING_PASS_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-range",
        "accept-ranges",
        "content-disposition",
    }
)


def _iter_mp4_file(path: Path):
    """Chunked stream without fixed Content-Length (legacy; prefer FileResponse)."""
    with path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            yield chunk


def _recording_file_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Accept-Ranges": "bytes",
    }


async def _stream_edge_recording_body(
    resp: httpx.Response,
    client: httpx.AsyncClient,
):
    """Proxy edge recording bytes; keep httpx open until done; ignore seek aborts."""
    try:
        async for chunk in resp.aiter_bytes(chunk_size=_CHUNK):
            yield chunk
    except asyncio.CancelledError:
        raise
    except httpx.HTTPError as e:
        logger.debug("edge recording stream ended early: %s", e)
    finally:
        with suppress(Exception):
            await resp.aclose()
        with suppress(Exception):
            await client.aclose()


class CameraCreate(BaseModel):
    name: str
    location: str
    url: str
    edge_base_url: Optional[str] = None
    mediamtx_path: Optional[str] = None
    mqtt_camera_id: Optional[str] = None


class CameraPatch(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    edge_base_url: Optional[str] = None


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


_EDGE_SETTINGS_TIMEOUT = httpx.Timeout(5.0, read=45.0)


def _push_edge_settings(cam_id: int) -> bool:
    c = camera_store.get_camera(cam_id)
    if not c:
        return False
    edge = camera_store.edge_base_url(c)
    if not edge:
        return False
    settings = c.get("settings", {})
    try:
        r = httpx.patch(
            f"{edge.rstrip('/')}/settings",
            json=settings,
            timeout=_EDGE_SETTINGS_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning("edge settings push failed cam_id=%s: %s", cam_id, e)
        return False


def _schedule_edge_settings_push(cam_id: int, *, retries: int = 2) -> None:
    """Push settings to the Pi without blocking the HTTP handler."""

    def _run() -> None:
        for attempt in range(max(1, retries)):
            if _push_edge_settings(cam_id):
                return
            if attempt + 1 < retries:
                time.sleep(3.0)

    threading.Thread(
        target=_run,
        name=f"edge-settings-push-{cam_id}",
        daemon=True,
    ).start()


def _push_all_edge_settings() -> None:
    """Sync cameras.json settings to each Pi edge (avoids mode mismatch at startup)."""
    for c in camera_store.list_cameras():
        if camera_store.edge_base_url(c):
            _schedule_edge_settings_push(int(c["id"]))


# =========================
# Camera Management
# =========================


@app.post("/cameras")
def add_camera_endpoint(cam: CameraCreate):
    try:
        created = camera_store.add_camera(cam.model_dump(exclude_unset=True))
        if camera_store.edge_base_url(created):
            _schedule_edge_settings_push(int(created["id"]))
        return created
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/cameras")
def list_cameras_endpoint():
    return camera_store.list_cameras()


@app.delete("/cameras/{cam_id}")
def delete_camera_endpoint(cam_id: int):
    if not camera_store.delete_camera(cam_id):
        raise HTTPException(status_code=404, detail="camera not found")
    return {"ok": True}


@app.post("/cameras/select/{cam_id}")
def select_camera_endpoint(cam_id: int):
    sel = camera_store.select_camera(cam_id)
    if sel is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return sel


@app.get("/cameras/selected")
def get_selected_camera_endpoint():
    return camera_store.get_selected_camera()


@app.patch("/cameras/{cam_id}")
def patch_camera(cam_id: int, body: CameraPatch):
    """Update RTSP URL / edge HTTP base (e.g. when a Pi 4 gets a new LAN IP)."""
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        c = camera_store.get_camera(cam_id)
        if not c:
            raise HTTPException(status_code=404, detail="camera not found")
        return c
    try:
        cam = camera_store.update_camera(cam_id, patch)
        if "edge_base_url" in patch:
            _schedule_edge_settings_push(cam_id)
        return cam
    except KeyError:
        raise HTTPException(status_code=404, detail="camera not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/cameras/{cam_id}/stream_health")
def camera_stream_health(cam_id: int, probe_rtsp: bool = True):
    """
    Why HLS may be missing: edge publisher, MediaMTX process, upstream RTSP pull.
    Set probe_rtsp=false for a fast check (skips ffprobe).
    """
    cam = camera_store.get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        path_map = mediamtx_manager.path_map_for_cameras()
        path_key = path_map.get(int(cam_id), camera_store.mediamtx_path_for_camera(cam))
        rtsp_url = cam.get("url") if isinstance(cam.get("url"), str) else ""
        edge_url = camera_store.edge_base_url(cam)
        edge_health: dict[str, Any] = {"reachable": False}
        if edge_url:
            try:
                r = httpx.get(f"{edge_url}/health", timeout=5.0)
                edge_health["reachable"] = r.is_success
                if r.is_success:
                    edge_health["body"] = r.json()
            except Exception as e:
                edge_health["error"] = str(e)
        mtx = mediamtx_manager.status_dict()
        hls = mediamtx_manager.probe_hls_local(path_key)
        rtsp_probe = (
            mediamtx_manager.probe_rtsp(rtsp_url, timeout_sec=4.0)
            if probe_rtsp and rtsp_url
            else None
        )
        hls_proxy = f"/cameras/{cam_id}/hls/index.m3u8"
        summary: list[str] = []
        pub = (edge_health.get("body") or {}).get("publisher_running")
        if pub is False:
            summary.append("Edge RTSP publisher is not running.")
        if rtsp_probe and rtsp_probe.get("reachable") is False:
            summary.append("Controller cannot reach edge RTSP (ffprobe failed).")
        if mtx.get("process_running") is False:
            summary.append("Controller MediaMTX is not running.")
        elif hls.get("reachable") is False:
            summary.append("MediaMTX HLS playlist not ready (upstream RTSP may be down).")
        return {
            "camera_id": cam_id,
            "rtsp_url": rtsp_url,
            "rtsp_probe": rtsp_probe,
            "mediamtx_path": path_key,
            "edge_base_url": edge_url,
            "edge_health": edge_health,
            "mediamtx": mtx,
            "hls_local": hls,
            "hls_proxy_path": hls_proxy,
            "summary": summary,
            "checks": [
                "On edge Pi: curl -s http://127.0.0.1:8080/health (publisher_running true)",
                f"On controller: ffprobe -rtsp_transport tcp {rtsp_url}",
                f"On controller: curl -sI {hls.get('url', '')}",
                f"UI HLS (proxied): GET /cameras/{cam_id}/hls/index.m3u8",
                "Restart backend after edge RTSP is up (MediaMTX regenerates config)",
            ],
        }
    except Exception as e:
        logger.exception("stream_health failed for camera %s", cam_id)
        return {
            "camera_id": cam_id,
            "error": str(e),
            "summary": [f"stream_health failed: {e}"],
            "hint": "Check backend logs; ensure git pull and restart uvicorn on the controller.",
        }


def _rewrite_hls_playlist(content: bytes, path_key: str) -> bytes:
    """
    MediaMTX may emit absolute URIs (/camera1/seg.ts). hls.js resolves those against
    the site root, not /cameras/<id>/hls/, so rewrite to path-relative names.
    """
    pk = (path_key or "camera1").strip().strip("/")
    if not pk:
        return content
    out: list[str] = []
    for line in content.decode("utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            out.append(line)
            continue
        uri = s
        if uri.startswith("http://") or uri.startswith("https://"):
            for marker in (f"/{pk}/", f"{pk}/"):
                idx = uri.find(marker)
                if idx >= 0:
                    uri = uri[idx + len(marker) :]
                    break
        elif uri.startswith("/"):
            parts = uri.lstrip("/").split("/", 1)
            if parts and parts[0] == pk:
                uri = parts[1] if len(parts) > 1 else ""
        if uri:
            out.append(uri)
    return "\n".join(out).encode("utf-8")


@app.api_route(
    "/cameras/{cam_id}/hls/{asset_path:path}",
    methods=["GET", "HEAD"],
)
async def proxy_camera_hls(cam_id: int, asset_path: str, request: Request):
    """
    Same-origin HLS for the React UI (avoids cross-port CORS to :8888).
    Proxies to controller MediaMTX loopback.
    """
    if not mediamtx_manager.hls_enabled():
        raise HTTPException(status_code=503, detail="HLS disabled on controller")
    cam = camera_store.get_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")
    path_map = mediamtx_manager.path_map_for_cameras()
    path_key = path_map.get(int(cam_id), camera_store.mediamtx_path_for_camera(cam))
    safe_asset = asset_path.lstrip("/")
    if not safe_asset or ".." in safe_asset.split("/"):
        raise HTTPException(status_code=400, detail="invalid asset path")
    upstream = f"{mediamtx_manager.hls_local_origin()}/{path_key}/{safe_asset}"

    method = request.method
    if method == "HEAD" and safe_asset.endswith(".m3u8"):
        # MediaMTX often has no useful HEAD on playlists; use GET for existence.
        method = "GET"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            upstream_req = client.build_request(method, upstream)
            resp = await client.send(upstream_req, stream=True)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"HLS upstream unreachable ({upstream}): {e}",
        ) from e

    if resp.status_code >= 400:
        detail = (await resp.aread())[:500].decode("utf-8", errors="replace")
        await resp.aclose()
        raise HTTPException(status_code=resp.status_code, detail=detail or "HLS upstream error")

    media_type = resp.headers.get(
        "content-type",
        "application/vnd.apple.mpegurl" if safe_asset.endswith(".m3u8") else "video/mp2t",
    )

    if safe_asset.endswith(".m3u8"):
        raw = await resp.aread()
        await resp.aclose()
        body_bytes = _rewrite_hls_playlist(raw, path_key)
        headers = {
            "cache-control": resp.headers.get("cache-control", "no-cache"),
            "content-length": str(len(body_bytes)),
        }
        if request.method == "HEAD":
            return Response(status_code=200, media_type=media_type, headers=headers)
        return Response(content=body_bytes, media_type=media_type, headers=headers)

    pass_headers = {}
    for key in ("cache-control", "content-length"):
        if key in resp.headers:
            pass_headers[key] = resp.headers[key]

    async def body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    if request.method == "HEAD":
        await resp.aclose()
        return Response(status_code=resp.status_code, headers=pass_headers)

    return StreamingResponse(body(), media_type=media_type, headers=pass_headers)


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
        _schedule_edge_settings_push(cam_id)
        bridge = mqtt_bridge.get_bridge()
        if bridge is not None:
            bridge.reconcile_recording_state()
        return cam["settings"]
    except KeyError:
        raise HTTPException(status_code=404, detail="camera not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _edge_manual_proxy(edge: str, subpath: str) -> dict[str, Any]:
    url = f"{edge}/recordings/manual/{subpath}"
    try:
        r = httpx.post(url, timeout=120.0)
        if r.status_code >= 400:
            detail: Any = r.text
            try:
                body = r.json()
                if isinstance(body, dict) and body.get("detail") is not None:
                    detail = body["detail"]
            except Exception:
                pass
            raise HTTPException(status_code=r.status_code, detail=str(detail))
        return r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/cameras/{cam_id}/recordings/manual/start")
def camera_manual_record_start(cam_id: int):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    edge = camera_store.edge_base_url(c)
    if not edge:
        raise HTTPException(
            status_code=400,
            detail="Manual recording requires a Pi edge camera (edge_base_url).",
        )
    st = c.get("settings") or {}
    if st.get("recording_mode") != "off":
        raise HTTPException(
            status_code=400,
            detail="Set recording mode to Off in camera settings before using manual recording.",
        )
    _push_edge_settings(cam_id)
    return _edge_manual_proxy(edge, "start")


@app.post("/cameras/{cam_id}/recordings/manual/stop")
def camera_manual_record_stop(cam_id: int):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    edge = camera_store.edge_base_url(c)
    if not edge:
        raise HTTPException(
            status_code=400,
            detail="Manual recording requires a Pi edge camera (edge_base_url).",
        )
    return _edge_manual_proxy(edge, "stop")


_MOTION_TRIGGER_TIMEOUT = httpx.Timeout(3.0, read=15.0)


def _edge_motion_clip_trigger_proxy(
    edge: str, body: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    url = f"{edge.rstrip('/')}/recordings/motion/trigger"
    try:
        r = httpx.post(
            url,
            json=body if isinstance(body, dict) else {},
            timeout=_MOTION_TRIGGER_TIMEOUT,
        )
        if r.status_code >= 400:
            detail: Any = r.text
            try:
                payload = r.json()
                if isinstance(payload, dict) and payload.get("detail") is not None:
                    detail = payload["detail"]
            except Exception:
                pass
            raise HTTPException(status_code=r.status_code, detail=str(detail))
        data = r.json()
        if isinstance(data, dict):
            return data
        return {"accepted": False}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/cameras/{cam_id}/recordings/motion/trigger")
def camera_motion_clip_trigger(cam_id: int, body: Optional[dict[str, Any]] = None):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    st = c.get("settings") or {}
    if st.get("recording_mode") != "motion":
        raise HTTPException(
            status_code=400,
            detail="Motion clips from person detection require recording mode Motion.",
        )
    edge = camera_store.edge_base_url(c)
    if not edge:
        raise HTTPException(
            status_code=400,
            detail="Motion recording on the Pi requires edge_base_url.",
        )
    data = _edge_motion_clip_trigger_proxy(edge, body)
    if isinstance(data, dict) and data.get("accepted"):
        cache_motion_status(cam_id, data)
    return data


@app.get("/cameras/{cam_id}/recordings/motion/status")
def camera_motion_clip_status(cam_id: int):
    """Always HTTP 200 — never 502 when the Pi edge is slow or offline."""
    try:
        c = camera_store.get_camera(cam_id)
        if not c:
            raise HTTPException(status_code=404, detail="camera not found")
        edge = camera_store.edge_base_url(c)
        if not edge:
            return motion_status_idle()
        return fetch_edge_motion_status(edge, int(cam_id))
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("motion clip status error cam_id=%s: %s", cam_id, e)
        return {**motion_status_idle(), "phase": "edge_unreachable"}


@app.get("/cameras/{cam_id}/recordings/manual/status")
def camera_manual_record_status(cam_id: int):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    edge = camera_store.edge_base_url(c)
    if not edge:
        return {"active": False, "filename": None}
    try:
        r = httpx.get(f"{edge}/recordings/manual/status", timeout=15.0)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"active": False, "filename": None}


@app.get("/cameras/{cam_id}/events")
def camera_events(cam_id: int, limit: int = 200, offset: int = 0):
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        rows = list_events(int(cam_id), limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"camera_id": int(cam_id), "events": rows, "count": len(rows)}


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


@app.websocket("/ws/detections")
async def ws_detections(ws: WebSocket):
    """Live face boxes (normalized) for dashboard overlays — Phase 1."""
    await ws.accept()
    svc = live_detection.get_service()
    hub = svc.ws_hub
    await hub.register(ws)
    try:
        await ws.send_json({"type": "hello", **svc.status()})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(ws)


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
        if not _SAFE_NAME.match(p.name):
            continue
        if not mp4_probe_ok(p):
            continue
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return out


@app.get("/recordings/{cam_id}/files/{filename}")
async def get_recording_file(cam_id: int, filename: str, request: Request):
    """Stream clip bytes (200/206). Never redirect — Safari/iOS breaks on 307 for video."""
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")

    edge = camera_store.edge_base_url(c)
    if edge:
        url = f"{edge.rstrip('/')}/recordings/files/{filename}"
        forward_headers: dict[str, str] = {}
        range_h = request.headers.get("range")
        if range_h:
            forward_headers["Range"] = range_h
        client = httpx.AsyncClient(timeout=_RECORDING_HTTP_TIMEOUT)
        try:
            req = client.build_request("GET", url, headers=forward_headers)
            r = await client.send(req, stream=True)
            if r.status_code >= 400:
                body = await r.aread()
                await r.aclose()
                await client.aclose()
                detail = body.decode(errors="replace")[:500] if body else r.reason_phrase
                raise HTTPException(status_code=r.status_code, detail=detail)
            pass_headers = {
                k: v
                for k, v in r.headers.items()
                if k.lower() in _RECORDING_PASS_HEADERS
            }
            if "accept-ranges" not in {k.lower() for k in pass_headers}:
                pass_headers["Accept-Ranges"] = "bytes"
            return StreamingResponse(
                _stream_edge_recording_body(r, client),
                status_code=r.status_code,
                headers=pass_headers,
                media_type=r.headers.get("content-type", "video/mp4"),
            )
        except HTTPException:
            with suppress(Exception):
                await client.aclose()
            raise
        except httpx.HTTPError as e:
            with suppress(Exception):
                await client.aclose()
            logger.warning("edge recording stream failed: %s", e)
            raise HTTPException(status_code=502, detail=str(e)) from e
        except Exception as e:
            with suppress(Exception):
                await client.aclose()
            logger.warning("edge recording stream failed: %s", e)
            raise HTTPException(status_code=502, detail=str(e)) from e

    path = _recordings_dir(cam_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers=_recording_file_headers(filename),
    )


@app.post("/recordings/{cam_id}/files/{filename}/finalize-mobile")
async def finalize_recording_for_mobile(cam_id: int, filename: str):
    """Re-mux/re-encode a clip so iOS/Android browsers can play it in <video>."""
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")

    edge = camera_store.edge_base_url(c)
    if edge:
        url = f"{edge}/recordings/files/{filename}/finalize-mobile"
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(url)
                if r.status_code >= 400:
                    detail = r.text
                    try:
                        body = r.json()
                        if isinstance(body, dict) and body.get("detail") is not None:
                            detail = str(body["detail"])
                    except Exception:
                        pass
                    raise HTTPException(status_code=r.status_code, detail=detail)
                return r.json()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    path = _recordings_dir(cam_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if not mp4_probe_ok(path):
        raise HTTPException(
            status_code=422,
            detail="recording incomplete or corrupt; re-record this clip",
        )
    if not finalize_mp4_for_mobile(path):
        raise HTTPException(status_code=422, detail="could not repair clip for mobile playback")
    if not mp4_ios_playable(path):
        raise HTTPException(status_code=422, detail="clip is not in iOS-compatible MP4 layout")
    return {"ok": True, "filename": filename}


def _delete_all_on_edge(edge: str) -> dict[str, Any]:
    """Bulk delete on edge; fall back to per-file DELETE if /recordings/all is missing."""
    r = httpx.delete(f"{edge}/recordings/all", timeout=120.0)
    if r.status_code not in (404, 405):
        r.raise_for_status()
        data = r.json() if r.content else {}
        if isinstance(data, dict):
            return data
        return {"ok": True, "deleted": 0}

    # Older edge agents: delete each known clip, then any remaining .mp4 on disk.
    names: set[str] = set()
    lr = httpx.get(f"{edge}/recordings", timeout=60.0)
    lr.raise_for_status()
    raw = lr.json()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                names.add(str(item["name"]))
    nr = httpx.get(f"{edge}/recordings/names", timeout=60.0)
    if nr.status_code == 200:
        extra = nr.json()
        if isinstance(extra, list):
            for name in extra:
                if isinstance(name, str) and _SAFE_NAME.match(name):
                    names.add(name)

    deleted = 0
    failed: list[str] = []
    for name in sorted(names):
        dr = httpx.delete(f"{edge}/recordings/files/{name}", timeout=30.0)
        if dr.status_code in (200, 204):
            deleted += 1
        elif dr.status_code == 404:
            continue
        else:
            failed.append(name)
    return {"ok": len(failed) == 0, "deleted": deleted, "failed": failed}


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


def _delete_all_local(cam_id: int) -> dict[str, Any]:
    d = _recordings_dir(cam_id)
    deleted = 0
    failed: list[str] = []
    if d.is_dir():
        for p in d.glob("*.mp4"):
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            if not _SAFE_NAME.match(p.name):
                continue
            try:
                p.unlink()
                deleted += 1
            except OSError:
                failed.append(p.name)
    return {"ok": len(failed) == 0, "deleted": deleted, "failed": failed}


@app.delete("/recordings/{cam_id}/all")
@app.delete("/recordings/{cam_id}/files")
def delete_all_recording_files(cam_id: int):
    """Delete every clip for this camera (edge proxy or local recordings dir)."""
    c = camera_store.get_camera(cam_id)
    if not c:
        raise HTTPException(status_code=404, detail="camera not found")

    edge = camera_store.edge_base_url(c)
    if edge:
        try:
            return _delete_all_on_edge(edge)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    return _delete_all_local(cam_id)


# =========================
# Health / diagnostics
# =========================


@app.get("/system/mediamtx")
def system_mediamtx():
    """Whether embedded MediaMTX is running (live iframe targets port player_port)."""
    return mediamtx_status_dict()


@app.post("/system/mediamtx/restart")
def system_mediamtx_restart():
    """Regenerate config from cameras.json and restart embedded MediaMTX."""
    return mediamtx_restart_embedded()


@app.get("/system/mosquitto")
def system_mosquitto():
    """MQTT broker managed with the API (LAN listener for edge agents)."""
    return mosquitto_status_dict()


@app.get("/system/live_detection")
def system_live_detection():
    """Phase 1: controller-side face detection workers + WS fan-out."""
    return live_detection.get_service().status()


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
        "mosquitto": mosquitto_status_dict(),
    }


@app.get("/")
def root():
    return {"status": "running", "role": "controller"}
