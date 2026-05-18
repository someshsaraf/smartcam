"""Sync recording metadata from edge agents (or local disk) into recordings.db."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from . import camera_store
from .recordings_store import (
    count_recordings,
    list_recordings as db_list_recordings,
    reconcile_camera_from_edge_list,
    upsert_recording,
)
from ._shared_path import ensure_shared_on_path

ensure_shared_on_path()
from surveillance_shared.ffmpeg_mobile import mp4_listable_fast  # noqa: E402
from surveillance_shared.recording_thumbnails import thumbnail_exists_for  # noqa: E402

logger = logging.getLogger(__name__)

_EDGE_LIST_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_SYNC_LOCK = threading.Lock()


def _list_local_fast(recordings_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not recordings_dir.is_dir():
        return out
    for p in recordings_dir.glob("*.mp4"):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if not mp4_listable_fast(p):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            {
                "name": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "has_thumbnail": thumbnail_exists_for(p),
            }
        )
    out.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    return out


def fetch_edge_recording_list(edge_base_url: str) -> list[dict[str, Any]]:
    base = edge_base_url.rstrip("/")
    r = httpx.get(f"{base}/recordings", timeout=_EDGE_LIST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError("edge returned non-list")
    return data


def sync_camera_recordings(
    cam_id: int,
    *,
    recordings_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Pull clip list from edge (or local dir) and update SQLite catalog."""
    if not isinstance(cam_id, int) or cam_id < 0:
        raise ValueError("cam_id must be a non-negative int")
    cam = camera_store.get_camera(cam_id)
    if not cam:
        raise ValueError("camera not found")

    edge = camera_store.edge_base_url(cam)
    if edge:
        items = fetch_edge_recording_list(edge)
    else:
        if recordings_dir is None:
            raise ValueError("recordings_dir required for non-edge camera")
        items = _list_local_fast(recordings_dir)

    stats = reconcile_camera_from_edge_list(cam_id, items)
    return {
        "camera_id": int(cam_id),
        "source": "edge" if edge else "local",
        **stats,
        "total_in_db": count_recordings(cam_id),
    }


def sync_all_cameras(
    *,
    recordings_root: Path,
) -> list[dict[str, Any]]:
    """Sync every configured camera; safe to call from a background thread."""
    results: list[dict[str, Any]] = []
    for cam in camera_store.list_cameras():
        cid = int(cam["id"])
        try:
            edge = camera_store.edge_base_url(cam)
            if edge:
                results.append(sync_camera_recordings(cid))
            else:
                local_dir = recordings_root / str(cid)
                results.append(
                    sync_camera_recordings(cid, recordings_dir=local_dir)
                )
        except Exception as e:
            logger.warning("recordings sync failed cam_id=%s: %s", cid, e)
            results.append(
                {
                    "camera_id": cid,
                    "error": str(e),
                    "total_in_db": count_recordings(cid),
                }
            )
    return results


def sync_camera_recordings_background(
    cam_id: int,
    *,
    recordings_dir: Optional[Path] = None,
) -> None:
    """Refresh one camera's catalog from edge/local without blocking MQTT handler."""

    def _run() -> None:
        try:
            sync_camera_recordings(cam_id, recordings_dir=recordings_dir)
        except Exception as e:
            logger.warning("recordings sync failed cam_id=%s: %s", cam_id, e)

    threading.Thread(
        target=_run,
        name=f"recordings-sync-{cam_id}",
        daemon=True,
    ).start()


def sync_all_cameras_background(recordings_root: Path) -> None:
    def _run() -> None:
        with _SYNC_LOCK:
            try:
                results = sync_all_cameras(recordings_root=recordings_root)
                ok = sum(1 for r in results if "error" not in r)
                logger.info(
                    "recordings catalog sync done cameras=%d ok=%d",
                    len(results),
                    ok,
                )
            except Exception as e:
                logger.warning("recordings catalog sync failed: %s", e)

    threading.Thread(target=_run, name="recordings-sync", daemon=True).start()


def list_recordings_for_api(
    camera_id: int,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Rows for API (name/size/mtime only)."""
    rows = db_list_recordings(camera_id, limit=limit, offset=offset)
    return [
        {
            "name": r["name"],
            "size": r["size"],
            "mtime": r["mtime"],
            "has_thumbnail": bool(r.get("has_thumbnail")),
        }
        for r in rows
    ]


def upsert_from_mqtt_stop(
    camera_id: int,
    *,
    filename: str,
    recording_id: Optional[str] = None,
    size: int = 0,
    mtime: Optional[float] = None,
) -> None:
    """Register a new clip when edge publishes Stop with filename."""
    fn = str(filename or "").strip()
    if not fn:
        return
    mt = float(mtime if mtime is not None else time.time())
    upsert_recording(
        camera_id,
        fn,
        size=max(0, int(size)),
        mtime=mt,
        recording_id=recording_id,
    )
