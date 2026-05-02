"""
Apply FFmpeg options for OpenCV RTSP capture **before** ``import cv2``.

Those ``[h264] corrupted macroblock`` / ``error while decoding MB`` messages usually mean
lost or reordered RTP packets (often UDP over Wi‑Fi). Forcing RTSP over TCP greatly reduces
that class of decode errors.

Concurrency: process-wide env vars; call ``apply_rtsp_env()`` once per process before the
first ``cv2.VideoCapture`` open.

Security: values come only from this process environment (operator-controlled).
"""

from __future__ import annotations

import os


def apply_rtsp_env() -> None:
    """
    Set ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` if not already set.

    Precedence:

    1. ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` — do nothing if already set (operator override).
    2. ``SURVEILLANCE_OPENCV_FFMPEG_CAPTURE_OPTIONS`` — full OpenCV pipe-separated string.
    3. Built-in defaults: RTSP/TCP, socket timeout, bounded demuxer delay.

    Pipe-separated entries use ``key;value`` pairs (OpenCV convention), e.g.
    ``rtsp_transport;tcp|stimeout;8000000``.
    """
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
