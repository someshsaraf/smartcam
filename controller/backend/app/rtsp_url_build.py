"""
Canonical RTSP URL construction for camera_store and MediaMTX.

Stores plain ``rtsp_user`` / ``rtsp_pass`` on the camera row when known; rebuilds
the pull URL at read time so MediaMTX never keeps a stale encoded ``url`` field.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote, unquote, urlparse


def rtsp_userinfo(username: str, password: str) -> str:
    """Percent-encode credentials once (never double-encode an already-encoded password)."""
    u = quote(str(username or ""), safe="")
    raw_pw = unquote(str(password or ""))
    p = quote(raw_pw, safe="")
    return f"{u}:{p}"


def build_rtsp_url(
    username: str,
    password: str,
    host: str,
    path: str = "/stream1",
    *,
    port: int = 554,
    scheme: str = "rtsp",
) -> str:
    if not host or not str(host).strip():
        raise ValueError("host is required")
    ui = rtsp_userinfo(username, password)
    norm_path = path if str(path).startswith("/") else f"/{path}"
    return f"{scheme}://{ui}@{host.strip()}:{int(port)}{norm_path}"


def infer_stream_path(cam: Dict[str, Any], fallback: str = "/stream1") -> str:
    for field in ("url", "main_stream", "mainStream"):
        u = str(cam.get(field) or "").strip()
        if not u.startswith(("rtsp://", "rtsps://")):
            continue
        try:
            path = urlparse(u).path
            if path:
                return path
        except Exception:
            continue
    return fallback


def extract_rtsp_credentials_from_url(url: str) -> Dict[str, str]:
    """Parse username/password/host from an RTSP URL (plain text, not encoded)."""
    u = str(url or "").strip()
    out: Dict[str, str] = {}
    if not u.startswith(("rtsp://", "rtsps://")):
        return out
    try:
        p = urlparse(u)
        if p.username:
            out["rtsp_user"] = unquote(p.username)
        if p.password is not None:
            out["rtsp_pass"] = unquote(p.password)
        if p.hostname:
            out["ip"] = p.hostname
    except Exception:
        pass
    return out


def effective_rtsp_url(cam: Dict[str, Any]) -> str:
    """
    Preferred RTSP pull URL for MediaMTX.

    When ``rtsp_user`` + ``rtsp_pass`` + host are known, rebuild from those fields
    so password updates in .env always win over a stale ``url`` string.
    """
    if not isinstance(cam, dict):
        return ""

    stored = (
        str(cam.get("url") or "").strip()
        or str(cam.get("main_stream") or "").strip()
        or str(cam.get("mainStream") or "").strip()
    )

    user = str(cam.get("rtsp_user") or cam.get("rtsp_username") or "").strip()
    passwd: Optional[str] = cam.get("rtsp_pass")
    if passwd is None:
        passwd = cam.get("rtsp_password")
    ip = str(cam.get("ip") or "").strip()

    if stored.startswith(("rtsp://", "rtsps://")):
        parsed_creds = extract_rtsp_credentials_from_url(stored)
        if not user:
            user = parsed_creds.get("rtsp_user", "")
        if passwd is None or passwd == "":
            passwd = parsed_creds.get("rtsp_pass")
        if not ip:
            ip = parsed_creds.get("ip", "")

    if user and passwd is not None and str(passwd) != "" and ip:
        path = infer_stream_path(cam)
        port = 554
        scheme = "rtsp"
        if stored.startswith(("rtsp://", "rtsps://")):
            try:
                p = urlparse(stored)
                if p.port:
                    port = int(p.port)
                if p.scheme == "rtsps":
                    scheme = "rtsps"
            except Exception:
                pass
        try:
            return build_rtsp_url(user, str(passwd), ip, path, port=port, scheme=scheme)
        except ValueError:
            pass

    return stored
