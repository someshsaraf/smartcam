"""JPEG thumbnails stored next to MP4 clips (``clip.mp4`` → ``clip.thumb.jpg``)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

THUMB_SUFFIX = ".thumb.jpg"


def recording_thumbnail_name(mp4_filename: str) -> str:
    """Basename of the thumbnail file for a recording MP4."""
    name = str(mp4_filename or "").strip()
    if not name:
        raise ValueError("mp4_filename required")
    if name.lower().endswith(".mp4"):
        return name[:-4] + THUMB_SUFFIX
    return name + THUMB_SUFFIX


def recording_thumbnail_path(mp4_path: Path) -> Path:
    if not isinstance(mp4_path, Path):
        raise TypeError("mp4_path must be a Path")
    return mp4_path.parent / recording_thumbnail_name(mp4_path.name)


def thumbnail_exists_for(mp4_path: Path) -> bool:
    thumb = recording_thumbnail_path(mp4_path)
    try:
        return thumb.is_file() and thumb.stat().st_size > 64
    except OSError:
        return False


def remove_recording_thumbnail(mp4_path: Path) -> None:
    thumb = recording_thumbnail_path(mp4_path)
    try:
        thumb.unlink(missing_ok=True)
    except OSError:
        pass


def write_recording_thumbnail_from_jpeg(
    jpeg_bytes: bytes,
    mp4_path: Path,
    *,
    width: int = 320,
) -> bool:
    """Save a JPEG blob as the clip thumbnail (e.g. person-detection frame)."""
    if not isinstance(mp4_path, Path):
        raise TypeError("mp4_path must be a Path")
    data = jpeg_bytes if isinstance(jpeg_bytes, (bytes, bytearray)) else b""
    if len(data) < 64:
        return False
    if width < 64 or width > 1920:
        raise ValueError("width out of range")
    out = recording_thumbnail_path(mp4_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            return False
        h, w = frame.shape[:2]
        if w > width:
            scale = float(width) / float(w)
            frame = cv2.resize(frame, (int(width), max(1, int(h * scale))))
        enc_ok, jpg = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
        )
        if not enc_ok:
            return False
        out.write_bytes(jpg.tobytes())
        return thumbnail_exists_for(mp4_path)
    except Exception:
        try:
            out.write_bytes(bytes(data))
            return thumbnail_exists_for(mp4_path)
        except OSError:
            return False


def write_recording_thumbnail(
    mp4_path: Path,
    *,
    width: int = 320,
    timeout: float = 20.0,
    seek_seconds: float = 0.25,
) -> bool:
    """Extract one JPEG frame from an MP4; returns True on success."""
    if not isinstance(mp4_path, Path):
        raise TypeError("mp4_path must be a Path")
    if not mp4_path.is_file():
        return False
    if width < 64 or width > 1920:
        raise ValueError("width out of range")
    seek = max(0.0, float(seek_seconds))
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    out = recording_thumbnail_path(mp4_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(seek),
        "-i",
        str(mp4_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-2",
        "-q:v",
        "5",
        str(out),
    ]
    try:
        r = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=float(timeout),
        )
    except subprocess.TimeoutExpired:
        return False
    if r.returncode != 0:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return thumbnail_exists_for(mp4_path)


def ensure_recording_thumbnail(
    mp4_path: Path,
    *,
    source_jpeg: bytes | None = None,
    **kwargs,
) -> bool:
    """Create thumbnail if missing; prefer ``source_jpeg`` (person-detection frame)."""
    if thumbnail_exists_for(mp4_path):
        return True
    if source_jpeg:
        if write_recording_thumbnail_from_jpeg(source_jpeg, mp4_path):
            return True
    return write_recording_thumbnail(mp4_path, **kwargs)
