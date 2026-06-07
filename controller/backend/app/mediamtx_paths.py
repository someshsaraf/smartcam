"""
MediaMTX path keys for a camera — must match frontend `streamPathForCamera`
(App.jsx) and the generated `data/mediamtx.generated.yml` paths.
"""

from __future__ import annotations

import re
from typing import Any, Dict


def rtsp_url(cam: Dict[str, Any]) -> str:
    return (
        str(cam.get("url") or "").strip()
        or str(cam.get("main_stream") or "").strip()
        or str(cam.get("mainStream") or "").strip()
    )


def _sanitize_path_segment(s: str) -> str:
    t = s.strip().lstrip("/")
    if not t:
        return "cam"
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in t)
    return out or "cam"


def mediamtx_path_key(cam: Dict[str, Any]) -> str:
    """HLS/WebRTC path segment under MediaMTX (e.g. cam0, front-door)."""
    raw = str(cam.get("mediamtx_path") or "").strip()
    if raw:
        return _sanitize_path_segment(raw)
    url = rtsp_url(cam)
    parts = [p for p in url.split("/") if p]
    last = parts[-1] if parts else ""
    cid = cam.get("id")
    if cid is not None and str(cid).strip() != "" and re.match(r"^stream\d*$", last, re.I):
        return f"cam{int(cid)}"
    if last:
        return _sanitize_path_segment(last)
    if cid is not None:
        return f"cam{int(cid)}"
    return "camera"
