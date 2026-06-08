"""
Regenerate data/mediamtx.generated.yml after camera_store changes so MediaMTX
picks up new RTSP URLs (hot-reload on file write). Optionally push the path
via the MediaMTX control API on localhost.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from .mediamtx_paths import mediamtx_path_key, rtsp_url

logger = logging.getLogger(__name__)

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def regenerate_controller_mediamtx_yaml() -> Tuple[bool, str]:
    """
    Run scripts/generate_controller_mediamtx_yaml.py (same as start.sh).
    Returns (ok, message_or_error).
    """
    script = os.path.join(BACKEND_ROOT, "scripts", "generate_controller_mediamtx_yaml.py")
    if not os.path.isfile(script):
        return False, f"missing script: {script}"
    try:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=45,
            env={**os.environ},
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            msg = err or out or f"exit {proc.returncode}"
            return False, msg[:800]
        tail = out.splitlines()[-1] if out else "ok"
        return True, tail
    except subprocess.TimeoutExpired:
        return False, "yaml generation timed out"
    except Exception as e:
        return False, str(e)[:800]


def push_mediamtx_path_for_camera(cam: Dict[str, Any]) -> None:
    """
    Best-effort: POST MediaMTX v3 config path replace/add so the running server
    picks up the new RTSP source without waiting on file-watch latency.

    Disabled when SMARTCAM_MEDIAMTX_API_PUSH is 0/false/no.
    """
    raw = os.environ.get("SMARTCAM_MEDIAMTX_API_PUSH", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return

    if not isinstance(cam, dict):
        return
    src = rtsp_url(cam).strip()
    if not src.startswith(("rtsp://", "rtsps://")):
        return
    path_key = mediamtx_path_key(cam)
    if not path_key:
        return

    base = os.environ.get("SMARTCAM_MEDIAMTX_API", "http://127.0.0.1:9997").strip().rstrip("/")
    if not base:
        return

    payload = {"source": src}
    seg = quote(path_key, safe="")
    try:
        import httpx

        with httpx.Client(timeout=4.0) as client:
            url = f"{base}/v3/config/paths/replace/{seg}"
            r = client.post(url, json=payload)
            if r.status_code == 404:
                r = client.post(f"{base}/v3/config/paths/add/{seg}", json=payload)
            if r.status_code >= 400:
                logger.info(
                    "[mediamtx] API push %s HTTP %s — file regen may still hot-reload",
                    path_key,
                    r.status_code,
                )
            else:
                logger.info("[mediamtx] API pushed path %s (HTTP %s)", path_key, r.status_code)
    except Exception as e:
        logger.debug("[mediamtx] API push skipped: %s", e)


def sync_after_camera_mutation(updated_camera: Optional[Dict[str, Any]] = None) -> None:
    """Call after POST/PATCH camera or DELETE (pass None)."""
    ok, msg = regenerate_controller_mediamtx_yaml()
    if ok:
        logger.info("[mediamtx] yaml: %s", msg)
    else:
        logger.warning("[mediamtx] yaml regen failed: %s", msg)
    if updated_camera is not None:
        push_mediamtx_path_for_camera(updated_camera)
