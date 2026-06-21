"""Quick RTSP reachability probe (ffmpeg) for diagnostics."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Tuple


def probe_rtsp_url(url: str, timeout_sec: float = 8.0) -> Tuple[bool, str]:
    """
    Return (ok, detail). Uses ffmpeg with TCP transport (VIGI-friendly).
    """
    u = str(url or "").strip()
    if not u.startswith(("rtsp://", "rtsps://")):
        return False, "not an rtsp URL"
    ff = shutil.which("ffmpeg")
    if not ff:
        return False, "ffmpeg not installed"
    timeout_sec = max(3.0, min(float(timeout_sec), 20.0))
    cmd = [
        ff,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        u,
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env={**os.environ},
        )
        err = (proc.stderr or proc.stdout or "").strip()
        if proc.returncode == 0:
            return True, "ffmpeg opened stream"
        low = err.lower()
        if "401" in low or "unauthorized" in low:
            return False, "RTSP 401 Unauthorized — wrong username/password or stream path"
        if "404" in low or "not found" in low:
            return False, "RTSP 404 — wrong stream path (try /stream1)"
        return False, err[:400] or f"ffmpeg exit {proc.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_sec:.0f}s"
    except Exception as e:
        return False, str(e)[:400]
