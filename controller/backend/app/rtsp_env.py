"""Thin shim for shared RTSP FFmpeg defaults."""

from __future__ import annotations

from . import _shared_path

_shared_path.ensure_shared_on_path()

from surveillance_shared.rtsp_env import apply_rtsp_env

__all__ = ["apply_rtsp_env"]
