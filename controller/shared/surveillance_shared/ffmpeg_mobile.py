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
    """Single-file MP4 output (motion clips from JPEGs)."""
    return [*h264_mobile_video_args(preset=preset), "-movflags", "+faststart"]


def h264_mobile_fragmented_mp4_args(*, preset: str = "veryfast") -> list[str]:
    """
    Live RTSP → MP4 while recording; moov is written incrementally so SIGINT
    does not leave a file with mdat but no moov.
    """
    return [
        *h264_mobile_video_args(preset=preset),
        "-f",
        "mp4",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
    ]


def mp4_probe_ok(path: Path, *, timeout: float = 30.0) -> bool:
    """True when ffprobe can read the file (moov present and decodable)."""
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < 256:
            return False
    except OSError:
        return False
    probe = shutil.which("ffprobe")
    if not probe:
        # Without ffprobe, require a moov atom in the file.
        try:
            with path.open("rb") as f:
                head = f.read(65536)
                if b"ftyp" not in head[:32]:
                    return False
                tail_off = max(0, path.stat().st_size - 65536)
                f.seek(tail_off)
                tail = f.read()
            return b"moov" in head or b"moov" in tail
        except OSError:
            return False
    try:
        r = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def finalize_mp4_for_mobile(path: Path, *, timeout: float = 300.0) -> bool:
    """
    Ensure MP4 has moov at the start and a baseline H.264 stream mobile browsers accept.
    Returns True only when ffprobe confirms the file is readable after the call.
    """
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < 256:
            return False
    except OSError:
        return False

    ff = shutil.which("ffmpeg")
    if not ff:
        return mp4_probe_ok(path, timeout=timeout)

    tmp = path.with_name(f".{path.name}.mobile.tmp.mp4")

    def _cleanup_tmp() -> None:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass

    def _commit_tmp() -> bool:
        if not tmp.is_file():
            return False
        try:
            if tmp.stat().st_size < 256:
                return False
        except OSError:
            return False
        if not mp4_probe_ok(tmp, timeout=timeout):
            return False
        tmp.replace(path)
        return mp4_probe_ok(path, timeout=timeout)

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
        if r.returncode == 0 and _commit_tmp():
            return True
        if r.returncode != 0 and r.stderr:
            print("[ffmpeg_mobile] remux failed:", r.stderr.strip()[-300:])
    except (OSError, subprocess.TimeoutExpired) as e:
        print("[ffmpeg_mobile] remux error:", e)
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
        if r.returncode == 0 and _commit_tmp():
            return True
        if r.returncode != 0 and r.stderr:
            print("[ffmpeg_mobile] re-encode failed:", r.stderr.strip()[-300:])
    except (OSError, subprocess.TimeoutExpired) as e:
        print("[ffmpeg_mobile] re-encode error:", e)
    finally:
        _cleanup_tmp()

    return mp4_probe_ok(path, timeout=timeout)


def remove_invalid_mp4(path: Path) -> None:
    """Delete corrupt/partial MP4 if present."""
    try:
        if path.is_file() and not mp4_probe_ok(path):
            path.unlink()
    except OSError:
        pass
