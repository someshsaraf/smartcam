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
from .rtsp_probe import probe_rtsp_url

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
    ok_probe, probe_detail = probe_rtsp_url(src)
    if not ok_probe:
        logger.warning(
            "[mediamtx] RTSP probe failed for %s before push: %s — URL=%s",
            path_key,
            probe_detail,
            src.split("@")[-1] if "@" in src else src[:80],
        )
    else:
        logger.info("[mediamtx] RTSP probe OK for %s", path_key)
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


def fetch_mediamtx_path_source(path_key: str) -> Optional[str]:
    """Read running MediaMTX path ``source`` via control API (localhost)."""
    if not path_key or not str(path_key).strip():
        return None
    base = os.environ.get("SMARTCAM_MEDIAMTX_API", "http://127.0.0.1:9997").strip().rstrip("/")
    if not base:
        return None
    seg = quote(str(path_key).strip(), safe="")
    try:
        import httpx

        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{base}/v3/config/paths/get/{seg}")
            if r.status_code != 200:
                return None
            data = r.json()
            if not isinstance(data, dict):
                return None
            src = data.get("source")
            return str(src).strip() if src else None
    except Exception:
        return None


def read_generated_yaml_path_source(path_key: str) -> Optional[str]:
    """Best-effort read of ``source`` for ``path_key`` from mediamtx.generated.yml."""
    if not path_key or not str(path_key).strip():
        return None
    yaml_path = os.path.join(BACKEND_ROOT, "data", "mediamtx.generated.yml")
    if not os.path.isfile(yaml_path):
        return None
    key = str(path_key).strip()
    in_path = False
    try:
        with open(yaml_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key}:"):
                    in_path = True
                    continue
                if in_path:
                    if stripped.startswith("source:"):
                        raw = stripped.split(":", 1)[1].strip()
                        if raw.startswith('"') and raw.endswith('"'):
                            import json as _json

                            try:
                                return str(_json.loads(raw)).strip()
                            except _json.JSONDecodeError:
                                return raw.strip('"')
                        return raw
                    if stripped and not line.startswith(" ") and not line.startswith("\t"):
                        break
    except OSError:
        return None
    return None


def sync_all_cameras_to_mediamtx() -> None:
    """Regenerate YAML and push every camera RTSP path to the running MediaMTX."""
    ok, msg = regenerate_controller_mediamtx_yaml()
    if ok:
        logger.info("[mediamtx] yaml: %s", msg)
    else:
        logger.warning("[mediamtx] yaml regen failed: %s", msg)
    try:
        from . import camera_store

        for row in camera_store.list_cameras():
            if isinstance(row, dict):
                push_mediamtx_path_for_camera(dict(row))
    except Exception as e:
        logger.debug("[mediamtx] sync_all push skipped: %s", e)


def sync_after_camera_mutation(updated_camera: Optional[Dict[str, Any]] = None) -> None:
    """Call after POST/PATCH camera or DELETE (pass None)."""
    ok, msg = regenerate_controller_mediamtx_yaml()
    if ok:
        logger.info("[mediamtx] yaml: %s", msg)
    else:
        logger.warning("[mediamtx] yaml regen failed: %s", msg)
    if updated_camera is not None:
        push_mediamtx_path_for_camera(updated_camera)
