"""
ONVIF WS-Discovery + GetStreamUri for Pi edge agents.

Used when no explicit ``SURVEILLANCE_RTSP_URL`` is set and the built-in Pi camera
publisher is not active. Credentials come from ``SURVEILLANCE_ONVIF_USER`` /
``SURVEILLANCE_ONVIF_PASS`` (same values as controller ``SMARTCAM_EDGE_ONVIF_*``).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

_MULTICAST_ADDR = "239.255.255.250"
_DISCOVERY_PORT = 3702


def _clamp_timeout(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _nonempty_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _local_xml_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _parse_probe_match_xaddrs(xml_bytes: bytes) -> Tuple[List[str], Optional[str]]:
    xaddrs: List[str] = []
    scopes_text: Optional[str] = None
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return [], None
    for elem in root.iter():
        tag = _local_xml_tag(elem.tag)
        if tag == "XAddrs" and elem.text:
            parts = [p.strip() for p in re.split(r"\s+", elem.text.strip()) if p.strip()]
            xaddrs.extend(parts)
        elif tag == "Scopes" and elem.text and scopes_text is None:
            scopes_text = elem.text.strip()
    seen: set[str] = set()
    uniq: List[str] = []
    for u in xaddrs:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq, scopes_text


def _scopes_friendly_name(scopes: Optional[str]) -> str:
    if not scopes:
        return ""
    for part in scopes.split():
        if "name/" in part.lower():
            try:
                seg = part.split("/name/")[-1].split("/")[0]
                from urllib.parse import unquote

                return unquote(seg).replace("_", " ").strip()
            except Exception:
                continue
    return ""


def _parse_xaddr_http_base(xaddr: str) -> Optional[Tuple[str, int]]:
    t = (xaddr or "").strip()
    if not t.startswith(("http://", "https://")):
        return None
    try:
        p = urlparse(t)
        host = (p.hostname or "").strip()
        if not host:
            return None
        port = int(p.port or (443 if p.scheme == "https" else 80))
        return host, port
    except Exception:
        return None


def _is_private_or_loopback_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        )
    except ValueError:
        return True


def ws_discovery_probe(*, timeout_sec: float = 3.0) -> List[Dict[str, Any]]:
    timeout_sec = _clamp_timeout(timeout_sec, 3.0, 0.5, 15.0)
    msg_id = str(uuid.uuid4())
    probe = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<s:Header>
<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
<a:MessageID>urn:uuid:{msg_id}</a:MessageID>
<a:To>urn:schemas-xmlsoap-org:ws:2005/04:discovery</a:To>
</s:Header>
<s:Body>
<d:Probe>
<d:Types>dn:NetworkVideoTransmitter</d:Types>
</d:Probe>
</s:Body>
</s:Envelope>""".encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.35)
    sock.bind(("", 0))
    deadline = time.monotonic() + timeout_sec
    seen_hosts: set[str] = set()
    devices: List[Dict[str, Any]] = []

    try:
        sock.sendto(probe, (_MULTICAST_ADDR, _DISCOVERY_PORT))
    except OSError as e:
        logger.warning("[onvif] WS-Discovery send failed: %s", e)
        sock.close()
        return []

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(min(0.4, remaining))
        try:
            data, _addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        xaddrs, scopes = _parse_probe_match_xaddrs(data)
        if not xaddrs:
            continue
        primary = xaddrs[0]
        parsed = _parse_xaddr_http_base(primary)
        if not parsed:
            continue
        host, _port = parsed
        if not _is_private_or_loopback_host(host):
            continue
        key = host.lower()
        if key in seen_hosts:
            continue
        seen_hosts.add(key)
        friendly = _scopes_friendly_name(scopes) or host
        devices.append(
            {
                "host": host,
                "xaddrs": xaddrs,
                "scopes": scopes,
                "name_hint": friendly,
            }
        )
    sock.close()
    return devices


def _rtsp_userinfo(username: str, password: str) -> str:
    u = quote(str(username or ""), safe="")
    p = quote(str(password or ""), safe="")
    return f"{u}:{p}"


def _inject_rtsp_credentials(uri: str, username: str, password: str) -> str:
    u = (uri or "").strip()
    if not u.startswith(("rtsp://", "rtsps://")):
        return u
    try:
        p = urlparse(u)
        if p.username:
            return u
        host = p.hostname
        if not host:
            return u
        port = f":{p.port}" if p.port else ""
        tail = p.path or ""
        if p.query:
            tail += f"?{p.query}"
        ui = _rtsp_userinfo(username, password)
        scheme = "rtsps" if u.startswith("rtsps") else "rtsp"
        return f"{scheme}://{ui}@{host}{port}{tail}"
    except Exception:
        return u


def _parse_onvif_service_url(xaddr: str) -> Optional[Tuple[str, int, bool]]:
    t = (xaddr or "").strip()
    if not t.startswith(("http://", "https://")):
        return None
    try:
        p = urlparse(t)
        host = (p.hostname or "").strip()
        if not host:
            return None
        scheme = (p.scheme or "http").lower()
        use_tls = scheme == "https"
        default = 443 if use_tls else 80
        port = int(p.port or default)
        return host, port, use_tls
    except Exception:
        return None


def _collect_onvif_endpoints(xaddrs: List[str]) -> List[Tuple[str, int, bool]]:
    out: List[Tuple[str, int, bool]] = []
    seen: set[Tuple[str, int, bool]] = set()
    for xa in xaddrs or []:
        pr = _parse_onvif_service_url(str(xa).strip())
        if not pr:
            continue
        h, po, tls = pr
        key = (h.lower(), po, tls)
        if key in seen:
            continue
        seen.add(key)
        out.append(pr)
    return out


def _lan_onvif_fallback_endpoints(
    device_host: str,
    existing: List[Tuple[str, int, bool]],
) -> List[Tuple[str, int, bool]]:
    host = (device_host or "").strip()
    if not host or not _is_private_or_loopback_host(host.split("%")[0]):
        return []
    seen = {(h.lower(), p, t) for h, p, t in existing}
    extras: List[Tuple[str, int, bool]] = []
    for port, use_tls in ((80, False), (8080, False), (8888, False), (443, True), (2020, False)):
        key = (host.lower(), port, use_tls)
        if key in seen:
            continue
        seen.add(key)
        extras.append((host, port, use_tls))
    return extras


def _onvif_main_rtsp_uri(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    encrypt: bool = False,
) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    meta: Dict[str, str] = {}
    try:
        from onvif import ONVIFCamera
    except ImportError:
        return (
            None,
            "onvif-zeep is not installed; pip install onvif-zeep on the edge agent",
            meta,
        )
    if not host or not isinstance(port, int) or port < 1 or port > 65535:
        return None, "invalid ONVIF host/port", meta
    user = _nonempty_str(username, "admin")
    pwd = str(password) if password is not None else ""
    try:
        cam = ONVIFCamera(
            host,
            port,
            user,
            pwd,
            encrypt=bool(encrypt),
            no_cache=True,
            adjust_time=True,
        )
        dev = cam.create_devicemgmt_service()
        try:
            info = dev.GetDeviceInformation()
            meta["manufacturer"] = str(getattr(info, "Manufacturer", "") or "")
            meta["model"] = str(getattr(info, "Model", "") or "")
            meta["firmware"] = str(getattr(info, "FirmwareVersion", "") or "")
        except Exception:
            pass
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            return None, "no ONVIF media profiles", meta
        token = profiles[0].token
        req = {
            "ProfileToken": token,
            "StreamSetup": {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            },
        }
        uri_result = media.GetStreamUri(req)
        raw_uri = str(getattr(uri_result, "Uri", "") or "").strip()
        if not raw_uri:
            return None, "empty stream URI from camera", meta
        if pwd:
            raw_uri = _inject_rtsp_credentials(raw_uri, user, pwd)
        return raw_uri, None, meta
    except Exception as e:
        logger.info("[onvif] probe failed for %s:%s tls=%s: %s", host, port, encrypt, e)
        return None, str(e)[:800], meta


def _onvif_main_rtsp_from_xaddrs(
    xaddrs: List[str],
    username: str,
    password: str,
    *,
    device_host: str = "",
) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    endpoints = _collect_onvif_endpoints(xaddrs)
    dh = (device_host or "").strip()
    if dh:
        endpoints = endpoints + _lan_onvif_fallback_endpoints(dh, endpoints)
    if not endpoints and dh:
        endpoints = [(dh, 2020, False)]
    if not endpoints:
        return None, "no valid ONVIF HTTP(S) URLs in XAddrs", {}
    last_err = "no ONVIF endpoint responded"
    merged_meta: Dict[str, str] = {}
    for host, port, use_tls in endpoints:
        uri, err, meta = _onvif_main_rtsp_uri(
            host, port, username, password, encrypt=use_tls
        )
        merged_meta.update({k: v for k, v in meta.items() if v})
        if uri:
            return uri, None, merged_meta
        last_err = err or last_err
    return None, last_err, merged_meta


def discover_onvif_main_stream(
    username: str,
    password: str,
    *,
    ws_timeout_sec: float = 3.0,
    per_device_timeout_sec: float = 14.0,
    max_devices: int = 8,
) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    WS-Discovery on the LAN, then GetStreamUri on each device until one succeeds.

    Returns (rtsp_url, error_message, meta dict with host/name/model).
    """
    if not str(password or "").strip():
        return None, "ONVIF password is required", {}
    ws_timeout_sec = _clamp_timeout(ws_timeout_sec, 3.0, 0.5, 15.0)
    per_device_timeout_sec = _clamp_timeout(per_device_timeout_sec, 14.0, 6.0, 55.0)
    max_devices = max(1, min(16, int(max_devices)))
    user = _nonempty_str(username, "admin")
    pwd = str(password)

    raw = ws_discovery_probe(timeout_sec=ws_timeout_sec)[:max_devices]
    if not raw:
        return None, "no ONVIF devices found on LAN (WS-Discovery)", {}

    last_err = "no stream URI from ONVIF devices"
    for dev in raw:
        host = str(dev.get("host") or "").strip()
        xaddrs = list(dev.get("xaddrs") or [])
        if not xaddrs and host:
            xaddrs = [f"http://{host}:2020/onvif/device_service"]
        result_holder: Dict[str, Any] = {}
        err_holder: List[str] = []

        def _job() -> None:
            try:
                uri, err, meta = _onvif_main_rtsp_from_xaddrs(
                    xaddrs,
                    user,
                    pwd,
                    device_host=host,
                )
                result_holder["uri"] = uri
                result_holder["err"] = err
                result_holder["meta"] = meta
            except Exception as e:
                err_holder.append(str(e))

        th = threading.Thread(target=_job, name=f"onvif-probe-{host}", daemon=True)
        th.start()
        th.join(timeout=per_device_timeout_sec)
        if th.is_alive():
            last_err = f"ONVIF timeout after {per_device_timeout_sec:.0f}s for {host}"
            continue
        if err_holder:
            last_err = err_holder[0]
            continue
        uri = result_holder.get("uri")
        if uri:
            meta = dict(result_holder.get("meta") or {})
            meta["host"] = host
            meta["name"] = str(dev.get("name_hint") or host)
            return str(uri), None, meta
        last_err = str(result_holder.get("err") or last_err)
    return None, last_err, {}
