"""H.264/MP4 settings for iOS Safari and Android Chrome (<video> playback)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def h264_mobile_video_args(*, preset: str = "veryfast") -> list[str]:
    """libx264 video encode flags (no muxer flags)."""
    return [
        "-c:v",
        "libx264",
        "-profile:v",
        "baseline",
        "-level",
        "3.1",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        preset,
        "-an",
    ]


def h264_mobile_output_args(*, preset: str = "veryfast") -> list[str]:
    """Single-file MP4 output (motion/manual clips)."""
    return [*h264_mobile_video_args(preset=preset), "-movflags", "+faststart"]


def h264_mobile_live_mux_flags() -> list[str]:
    """Mux flags while ffmpeg is still writing (stop will remux to faststart)."""
    return ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]


def finalize_mp4_for_mobile(path: Path, *, timeout: float = 300.0) -> bool:
    """
    Ensure MP4 has moov at the start and a baseline H.264 stream mobile browsers accept.
    Returns True if path is a valid output file after the call.
    """
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < 64:
            return False
    except OSError:
        return False

    ff = shutil.which("ffmpeg")
    if not ff:
        return False

    tmp = path.with_name(f".{path.name}.mobile.tmp.mp4")

    def _cleanup_tmp() -> None:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass

    # Fast path: remux with stream copy when bitstream is already compatible.
    try:
        r = subprocess.run(
            [
                ff,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size >= 64:
            tmp.replace(path)
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        _cleanup_tmp()

    # Fallback: re-encode to baseline H.264 (fixes High/HEVC or truncated moov).
    try:
        r = subprocess.run(
            [
                ff,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                *h264_mobile_video_args(preset="fast"),
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size >= 64:
            tmp.replace(path)
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        _cleanup_tmp()

    return path.is_file() and path.stat().st_size >= 64
