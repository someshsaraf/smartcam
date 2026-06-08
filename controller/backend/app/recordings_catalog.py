"""Aggregate recording index: controller ``data/recordings/{id}/`` + edge HTTP catalogs."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from . import camera_store
from .manual_recording import list_local_recordings_for_camera

logger = logging.getLogger(__name__)


def _edge_base(cam: Dict[str, Any]) -> str:
    u = str(cam.get("edge_base_url") or "").strip().rstrip("/")
    if not u.startswith(("http://", "https://")):
        return ""
    return u


def list_merged_recordings(limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 1000
    lim = max(1, min(lim, 10000))
    rows: List[Dict[str, Any]] = []
    for cam in camera_store.list_cameras():
        if not isinstance(cam, dict):
            continue
        try:
            cid = int(cam["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(cam.get("name") or f"Camera {cid}")
        rows.extend(list_local_recordings_for_camera(cid, name))
        edge = _edge_base(cam)
        if not edge:
            continue
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{edge}/recordings")
                if r.status_code != 200:
                    continue
                data = r.json()
                if not isinstance(data, list):
                    continue
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    fn = str(item.get("name") or "")
                    if not fn.endswith(".mp4"):
                        continue
                    rows.append(
                        {
                            "camId": cid,
                            "camName": name,
                            "edgeBaseUrl": edge,
                            "name": fn,
                            "size": int(item.get("size") or 0),
                            "mtime": float(item.get("mtime") or 0),
                            "hasThumbnail": bool(item.get("hasThumbnail")),
                        }
                    )
        except Exception as e:
            logger.debug("list edge recordings %s: %s", edge, e)
    rows.sort(key=lambda x: float(x.get("mtime") or 0), reverse=True)
    return rows[:lim]
