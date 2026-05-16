"""LAN discovery: RTSP cameras (legacy) and Vigilance edge agents (mDNS)."""

from __future__ import annotations

import time
from typing import Any

from zeroconf import Zeroconf, ServiceBrowser

EDGE_SERVICE = "_vigilance-edge._tcp.local."
LEGACY_CAMERA_SERVICE = "_camera._tcp.local."


def _service_type_matches(found: Any, expected: str) -> bool:
    """Compare mDNS service types tolerating trailing dots / case from Zeroconf callbacks."""
    if found is None:
        return False
    a = str(found).strip().rstrip(".").lower()
    b = expected.strip().rstrip(".").lower()
    return a == b


def _decode_props(raw: dict[Any, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not raw:
        return out
    for k, v in raw.items():
        kb = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else str(k)
        if isinstance(v, bytes):
            out[kb] = v.decode("utf-8", errors="replace")
        else:
            out[kb] = str(v)
    return out


class _CollectListener:
    def __init__(self, bucket: list[dict[str, Any]]) -> None:
        self._bucket = bucket

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        ip = ".".join(map(str, info.addresses[0]))
        props = _decode_props(info.properties or {})
        self._bucket.append(
            {"_ip": ip, "_port": info.port, "_props": props, "_type": str(type_)}
        )

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Required by newer zeroconf; re-resolve on update."""
        self.add_service(zc, type_, name)


def discover_rtsp_cameras(*, wait_seconds: float = 3.0) -> list[dict[str, str]]:
    """Legacy ``_camera._tcp`` entries (RTSP URLs)."""
    found: list[dict[str, Any]] = []
    zc = Zeroconf()
    try:
        ServiceBrowser(zc, LEGACY_CAMERA_SERVICE, _CollectListener(found))
        time.sleep(wait_seconds)
    finally:
        zc.close()

    out: list[dict[str, str]] = []
    for row in found:
        if not _service_type_matches(row.get("_type"), LEGACY_CAMERA_SERVICE):
            continue
        p = row["_props"]
        path = p.get("path", "")
        name = p.get("name", "")
        loc = p.get("location", "")
        ip = row["_ip"]
        url = f"rtsp://{ip}:8554{path}" if path else f"rtsp://{ip}:8554/"
        out.append({"name": name, "location": loc, "url": url})
    # Dedupe by url
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for c in out:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        deduped.append(c)
    return deduped


def discover_edge_agents(*, wait_seconds: float = 3.0) -> list[dict[str, Any]]:
    """
    Discover Pi 4 edge HTTP APIs advertising ``_vigilance-edge._tcp``.

    Returns dicts suitable for POST /cameras (with edge_base_url, mqtt_camera_id, etc.).
    """
    found: list[dict[str, Any]] = []
    zc = Zeroconf()
    try:
        ServiceBrowser(zc, EDGE_SERVICE, _CollectListener(found))
        time.sleep(wait_seconds)
    finally:
        zc.close()

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for row in found:
        if not _service_type_matches(row.get("_type"), EDGE_SERVICE):
            continue
        p = row["_props"]
        ip = row["_ip"]
        port = int(row["_port"])
        cam_id = (p.get("id") or "").strip() or "camera1"
        name = (p.get("name") or "Edge camera").strip()
        location = (p.get("location") or "").strip()
        rtsp = (p.get("rtsp") or "").strip()
        path = (p.get("path") or cam_id).strip().lstrip("/")
        key = (ip, port, cam_id)
        if key in seen:
            continue
        seen.add(key)
        edge_base_url = f"http://{ip}:{port}"
        stream_url = rtsp if rtsp else ""
        incomplete = not bool(stream_url.strip())
        out.append(
            {
                "kind": "edge_agent",
                "name": name,
                "location": location,
                "url": stream_url,
                "incomplete": incomplete,
                "edge_base_url": edge_base_url,
                "mqtt_camera_id": cam_id,
                "mediamtx_path": path,
            }
        )
    return out


def discover() -> list[dict[str, str]]:
    """Backward-compatible alias: legacy RTSP camera browse."""
    return discover_rtsp_cameras()
