"""
Simple ONVIF / RTSP auto-discovery helper.

This module scans the local subnet for common RTSP ports and returns
camera metadata for use by the SmartCam controller UI.

Future improvements:
- Full ONVIF WS-Discovery
- ONVIF media profile fetch
- PTZ support
- Camera manufacturer detection
"""

from concurrent.futures import ThreadPoolExecutor
import socket


COMMON_RTSP_PORTS = [554, 8554]


def _probe(ip: str):
    for port in COMMON_RTSP_PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.25)

        try:
            result = sock.connect_ex((ip, port))
            if result == 0:
                return {
                    "name": f"Camera-{ip.split('.')[-1]}",
                    "ip": ip,
                    "status": "online",
                    "rtsp_url": f"rtsp://{ip}/stream1",
                    "type": "ONVIF/RTSP"
                }
        except Exception:
            pass
        finally:
            sock.close()

    return None


def auto_discover(subnet_prefix="192.168.2"):
    cameras = []

    with ThreadPoolExecutor(max_workers=64) as executor:
        results = executor.map(
            _probe,
            [f"{subnet_prefix}.{i}" for i in range(1, 255)]
        )

    for result in results:
        if result:
            cameras.append(result)

    return cameras
