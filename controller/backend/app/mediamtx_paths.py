"""
MediaMTX path keys for a camera — must match frontend `streamPathForCamera`
(App.jsx) and the generated `data/mediamtx.generated.yml` paths.
"""

from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import urlparse, urlunparse

from .rtsp_url_build import effective_rtsp_url


def rtsp_url(cam: Dict[str, Any]) -> str:
    return effective_rtsp_url(cam if isinstance(cam, dict) else {})


def redact_rtsp_url_for_debug(url: str) -> str:
    """
    Same RTSP URL with password replaced by *** for logs/UI.
    Usernames are kept so you can confirm which account MediaMTX uses.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("rtsp://", "rtsps://")):
        return u
    try:
        p = urlparse(u)
        if p.password is None and p.username is None:
            return u
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        auth = p.username or ""
        if p.password is not None:
            netloc = f"{auth}:***@{host}{port}" if auth else f"***@{host}{port}"
        else:
            netloc = f"{auth}@{host}{port}" if auth else f"{host}{port}"
        return urlunparse((p.scheme, netloc, p.path or "", p.params, p.query, p.fragment))
    except Exception:
        return "<unparseable rtsp url>"


def rtsp_url_has_userinfo(url: str) -> bool:
    """
    True if the RTSP URL includes a username (VIGI and most IP cameras return 401
    to anonymous RTSP; MediaMTX passes the URL through unchanged).
    """
    u = (url or "").strip()
    if not u.startswith(("rtsp://", "rtsps://")):
        return False
    try:
        return bool(urlparse(u).username)
    except Exception:
        return False


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
