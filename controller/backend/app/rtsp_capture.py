"""
RTSP capture helpers for OpenCV — apply FFmpeg options before first VideoCapture.

Same defaults as edge-agent `surveillance_shared.rtsp_env` (TCP transport).
"""

from __future__ import annotations

import os


def apply_rtsp_env() -> None:
    """Set ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` if not already set (operator-controlled)."""
    if os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "").strip():
        return
    custom = os.environ.get("SURVEILLANCE_OPENCV_FFMPEG_CAPTURE_OPTIONS", "").strip()
    if custom:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = custom
        return
    parts = [
        "rtsp_transport;tcp",
        "stimeout;8000000",
        "max_delay;500000",
    ]
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(parts)
