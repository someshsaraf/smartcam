"""
LAN camera discovery: ONVIF WS-Discovery + RTSP URI via ONVIF media service,
and mDNS browse for SmartCam ``_vigilance-edge._tcp`` Pi agents.

Security: discovery is LAN-oriented; credentials are never echoed in responses.
Input validation on all public entrypoints.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import camera_store

logger = logging.getLogger(__name__)

_VIGILANCE_EDGE_TYPE = "_vigilance-edge._tcp.local."
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
    """Return (xaddrs list split by spaces, scopes text if found)."""
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
    # de-dupe preserving order
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
        # hostname — allow (LAN DNS)
        return True


def ws_discovery_probe(*, timeout_sec: float = 3.0) -> List[Dict[str, Any]]:
    """
    ONVIF WS-Discovery multicast probe. Returns one entry per unique first XAddr host.
    No credentials required.
    """
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
<a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
</s:Header>
<s:Body>
<d:Probe>
<d:Types>dn:NetworkVideoTransmitter</d:Types>
</d:Probe>
</s:Body>
</s:Envelope>""".encode(
        "utf-8"
    )

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
        logger.warning("[discover] WS-Discovery send failed: %s", e)
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
        ui = camera_store._rtsp_userinfo(username, password)
        scheme = "rtsps" if u.startswith("rtsps") else "rtsp"
        return f"{scheme}://{ui}@{host}{port}{tail}"
    except Exception:
        return u


def _friendly_onvif_error(message: str) -> str:
    m = (message or "").lower()
    if any(
        x in m
        for x in (
            "authority",
            "not authorized",
            "unauthorized",
            "sender not authorized",
            "failedauthentication",
        )
    ):
        return (
            f"{message.strip()} — ONVIF rejected credentials or access. "
            "Confirm the camera’s ONVIF username/password (TP-Link VIGI: use the camera account "
            "from the VIGI app / label, not only the cloud password), that ONVIF is enabled, "
            "and the account is not locked."
        )
    return message.strip()


def _parse_onvif_service_url(xaddr: str) -> Optional[Tuple[str, int, bool]]:
    """Return (host, port, use_tls) for an ONVIF device or media service URL."""
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
    """Deduplicated (host, port, use_tls) from all XAddrs (order preserved)."""
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


def _onvif_main_rtsp_uri(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    encrypt: bool = False,
) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    """
    Returns (rtsp_uri_with_credentials_or_none, error_message, meta).
    meta keys: manufacturer, model, firmware (best-effort).
    ``encrypt`` must be True when using HTTPS ONVIF (often port 443).
    """
    meta: Dict[str, str] = {}
    try:
        from onvif import ONVIFCamera
    except ImportError:
        return (
            None,
            "onvif-zeep is not installed; pip install onvif-zeep on the controller",
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
        logger.info(
            "[discover] ONVIF probe failed for %s:%s tls=%s: %s",
            host,
            port,
            encrypt,
            e,
        )
        return None, _friendly_onvif_error(str(e))[:800], meta


def _onvif_main_rtsp_from_xaddrs(
    xaddrs: List[str],
    username: str,
    password: str,
) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    """Try each ONVIF HTTP(S) endpoint until one yields a stream URI."""
    endpoints = _collect_onvif_endpoints(xaddrs)
    if not endpoints:
        return None, "no valid ONVIF HTTP(S) URLs in XAddrs", {}
    last_err = "no ONVIF endpoint responded"
    merged_meta: Dict[str, str] = {}
    for host, port, use_tls in endpoints:
        uri, err, meta = _onvif_main_rtsp_uri(
            host, port, username, password, encrypt=use_tls
        )
        merged_meta.update(meta)
        if uri:
            return uri, None, meta
        last_err = err or last_err
    return None, last_err, merged_meta


def discover_onvif_cameras(
    *,
    username: str,
    password: str,
    ws_timeout_sec: float = 3.0,
    per_device_timeout_sec: float = 4.0,
    max_devices: int = 24,
) -> List[Dict[str, Any]]:
    """
    WS-Discovery then optional ONVIF GetStreamUri per device (needs password for most VIGI).
    """
    ws_timeout_sec = _clamp_timeout(ws_timeout_sec, 3.0, 0.5, 15.0)
    per_device_timeout_sec = _clamp_timeout(per_device_timeout_sec, 4.0, 1.0, 20.0)
    max_devices = max(1, min(64, int(max_devices)))

    raw = ws_discovery_probe(timeout_sec=ws_timeout_sec)[:max_devices]
    out: List[Dict[str, Any]] = []
    pwd = str(password) if password is not None else ""

    for dev in raw:
        xaddrs = dev.get("xaddrs") or []
        primary = xaddrs[0] if xaddrs else ""
        parsed = _parse_xaddr_http_base(primary)
        if not parsed:
            continue
        host, _ = parsed
        name_hint = str(dev.get("name_hint") or host)

        row: Dict[str, Any] = {
            "kind": "onvif",
            "name": name_hint,
            "host": host,
            "xaddrs": xaddrs,
            "incomplete": True,
            "edge_base_url": None,
        }

        if not pwd:
            row["detail"] = "Set password in discover request to resolve RTSP URL"
            out.append(row)
            continue

        # Run ONVIF with wall-clock timeout (ONVIFCamera has no socket timeout param)
        result_holder: Dict[str, Any] = {}
        err_holder: List[str] = []

        def _job() -> None:
            try:
                uri, err, meta = _onvif_main_rtsp_from_xaddrs(
                    xaddrs,
                    username,
                    password,
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
            row["detail"] = f"ONVIF timeout after {per_device_timeout_sec:.0f}s"
            out.append(row)
            continue
        if err_holder:
            row["detail"] = err_holder[0][:500]
            out.append(row)
            continue

        uri = result_holder.get("uri")
        err = result_holder.get("err")
        meta = result_holder.get("meta") or {}
        if err or not uri:
            row["detail"] = (err or "no RTSP URI")[:500]
            out.append(row)
            continue

        row["incomplete"] = False
        row["main_stream"] = uri
        row["url"] = uri
        row["manufacturer"] = meta.get("manufacturer") or ""
        row["model"] = meta.get("model") or ""
        if meta.get("firmware"):
            row["firmware"] = meta["firmware"]
        disp = (meta.get("manufacturer") or "").strip()
        mod = (meta.get("model") or "").strip()
        if disp or mod:
            row["name"] = f"{disp} {mod}".strip() or name_hint
        out.append(row)

    return out


def discover_vigilance_edges(*, browse_sec: float = 4.0) -> List[Dict[str, Any]]:
    """mDNS browse for ``_vigilance-edge._tcp`` (Pi edge agents)."""
    browse_sec = _clamp_timeout(browse_sec, 4.0, 1.0, 30.0)
    try:
        from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
    except ImportError:
        return []

    found: List[Dict[str, Any]] = []
    lock = threading.Lock()

    class _Listener(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            try:
                info: Optional[ServiceInfo] = zc.get_service_info(
                    type_, name, timeout=3500
                )
                if info is None:
                    return
                props: Dict[str, str] = {}
                if info.properties:
                    for k, v in info.properties.items():
                        kk = (
                            k.decode("utf-8", errors="replace")
                            if isinstance(k, bytes)
                            else str(k)
                        )
                        vv = (
                            v.decode("utf-8", errors="replace")
                            if isinstance(v, bytes)
                            else str(v)
                        )
                        props[kk] = vv
                addrs = info.parsed_addresses()
                ip = addrs[0] if addrs else ""
                if not ip:
                    return
                port = int(info.port or 8080)
                api = f"http://{ip}:{port}".rstrip("/")
                rtsp = (props.get("rtsp") or "").strip()
                display = (props.get("name") or name.split(".")[0] or "Edge").strip()
                edge_id = (props.get("id") or "").strip()
                with lock:
                    for ex in found:
                        if ex.get("edge_base_url") == api:
                            return
                    found.append(
                        {
                            "kind": "edge",
                            "name": display,
                            "edge_base_url": api,
                            "mqtt_camera_id": edge_id or None,
                            "main_stream": rtsp or None,
                            "incomplete": not bool(rtsp),
                            "host": ip,
                        }
                    )
            except Exception as e:
                logger.debug("[discover] edge mDNS add_service: %s", e)

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            return

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            return

    zc = Zeroconf()
    listener = _Listener()
    ServiceBrowser(zc, _VIGILANCE_EDGE_TYPE, listener)
    time.sleep(browse_sec)
    try:
        zc.close()
    except Exception:
        pass
    return list(found)


def run_camera_discovery(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Validate ``body`` and run selected discovery strategies.

    Body keys (all optional):
    - ``username`` / ``password``: ONVIF credentials (password required for RTSP URIs).
    - ``timeout_seconds``: split between WS-Discovery and per-device ONVIF (scaled).
    - ``scan_onvif`` (default true) / ``scan_edges`` (default true).
    """
    body = body if isinstance(body, dict) else {}

    scan_onvif = body.get("scan_onvif", True)
    scan_edges = body.get("scan_edges", True)
    if isinstance(scan_onvif, str):
        scan_onvif = scan_onvif.lower() not in ("0", "false", "no")
    if isinstance(scan_edges, str):
        scan_edges = scan_edges.lower() not in ("0", "false", "no")

    total_timeout = _clamp_timeout(body.get("timeout_seconds"), 6.0, 2.0, 45.0)
    username = _nonempty_str(body.get("username"), "admin")
    password = str(body.get("password") or "")

    env_disable = os.environ.get("SMARTCAM_DISCOVERY", "").strip().lower() in (
        "0",
        "false",
        "no",
    )
    if env_disable:
        return {
            "onvif": [],
            "edges": [],
            "disabled": True,
            "message": "SMARTCAM_DISCOVERY is disabled",
        }

    ws_t = min(8.0, max(1.5, total_timeout * 0.45))
    per_dev = min(12.0, max(2.0, total_timeout * 0.35))
    browse = min(12.0, max(2.0, total_timeout * 0.55))

    onvif_list: List[Dict[str, Any]] = []
    edges_list: List[Dict[str, Any]] = []
    errors: List[str] = []

    if scan_onvif:
        try:
            onvif_list = discover_onvif_cameras(
                username=username,
                password=password,
                ws_timeout_sec=ws_t,
                per_device_timeout_sec=per_dev,
            )
        except Exception as e:
            logger.exception("[discover] ONVIF discovery failed")
            errors.append(f"onvif: {e}")

    if scan_edges:
        try:
            edges_list = discover_vigilance_edges(browse_sec=browse)
        except Exception as e:
            logger.exception("[discover] edge mDNS failed")
            errors.append(f"edges: {e}")

    # Back-compat: some callers expect a flat ``devices`` list (edges first).
    devices = [*edges_list, *onvif_list]
    return {
        "edges": edges_list,
        "onvif": onvif_list,
        "devices": devices,
        "errors": errors,
    }
