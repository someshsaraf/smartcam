"""
Register an mDNS service so the Pi 5 controller can discover this edge agent on the LAN.

Service type: ``_vigilance-edge._tcp.local.``
"""

from __future__ import annotations

import os
import re
import socket
from typing import Optional

from zeroconf import ServiceInfo, Zeroconf

_SERVICE_TYPE = "_vigilance-edge._tcp.local."


def _controller_ip_for_route() -> str:
    return (
        os.environ.get("SURVEILLANCE_CONTROLLER_IP", "").strip() or "192.168.2.139"
    )


def get_lan_ipv4() -> str:
    """Public wrapper: preferred LAN IPv4 for mDNS and advertised RTSP URLs."""
    return _guess_lan_ipv4()


def _guess_lan_ipv4() -> str:
    ip_guess = os.environ.get("SURVEILLANCE_EDGE_IP", "").strip()
    if ip_guess:
        return ip_guess
    target = _controller_ip_for_route()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 1))
        out = s.getsockname()[0]
        return out if out and out != "0.0.0.0" else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _dns_safe_instance(name: str, cam_id: str) -> str:
    raw = f"{name}-{cam_id}".strip() or "edge"
    safe = re.sub(r"[^A-Za-z0-9-]+", "-", raw).strip("-")
    cid = re.sub(r"[^A-Za-z0-9-]", "-", cam_id)[:30]
    return (safe[:50] or "vigilance-edge") + "-" + cid


def _props(**kwargs: str) -> dict[bytes, bytes]:
    out: dict[bytes, bytes] = {}
    for k, v in kwargs.items():
        if not v:
            continue
        b = str(v).encode("utf-8")
        if len(b) > 220:
            b = b[:220]
        out[k.encode("utf-8")] = b
    return out


class EdgeZeroconfPublisher:
    """Registers ``_vigilance-edge._tcp`` with TXT id, name, location, rtsp, path, api_port."""

    def __init__(
        self,
        *,
        camera_id: str,
        display_name: str,
        location: str,
        rtsp_url: str,
        mediamtx_path: str,
        http_port: int,
        lan_ip: Optional[str] = None,
    ) -> None:
        if not camera_id or not str(camera_id).strip():
            raise ValueError("camera_id required")
        self._camera_id = str(camera_id).strip()
        self._display_name = (display_name or "Vigilance Edge").strip() or "Vigilance Edge"
        self._location = (location or "").strip()
        self._rtsp_url = (rtsp_url or "").strip()
        self._mediamtx_path = (mediamtx_path or self._camera_id).strip()
        self._http_port = int(http_port)
        self._lan_ip = lan_ip or _guess_lan_ipv4()
        self._zc: Optional[Zeroconf] = None
        self._info: Optional[ServiceInfo] = None

    def register(self) -> None:
        self.unregister()
        instance = _dns_safe_instance(self._display_name, self._camera_id)
        host_fqdn = socket.gethostname().split(".")[0] + ".local."
        props = _props(
            id=self._camera_id,
            name=self._display_name,
            location=self._location,
            rtsp=self._rtsp_url,
            path=self._mediamtx_path,
            api_port=str(self._http_port),
        )
        try:
            addr = socket.inet_aton(self._lan_ip)
        except OSError:
            addr = socket.inet_aton("127.0.0.1")
        self._info = ServiceInfo(
            _SERVICE_TYPE,
            f"{instance}.{_SERVICE_TYPE}",
            addresses=[addr],
            port=self._http_port,
            properties=props,
            server=host_fqdn,
        )
        self._zc = Zeroconf()
        self._zc.register_service(self._info)
        print(
            f"[edge] mDNS {_SERVICE_TYPE} instance={instance!r} "
            f"ip={self._lan_ip}:{self._http_port} id={self._camera_id!r}"
        )

    def unregister(self) -> None:
        if self._zc and self._info:
            try:
                self._zc.unregister_service(self._info)
            except Exception:
                pass
        if self._zc:
            try:
                self._zc.close()
            except Exception:
                pass
        self._zc = None
        self._info = None
