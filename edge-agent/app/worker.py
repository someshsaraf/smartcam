"""
Pi 4 edge recording worker: controller-triggered motion clips (rolling buffer) or continuous ffmpeg.
Publishes MQTT JSON on ``surveillance/cameras/{id}/recording``.
"""

from __future__ import annotations

import collections
import json
import os
import signal
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt

from . import _shared_bootstrap  # noqa: F401 — sys.path for surveillance_shared
from .agent_debug import agent_log

import cv2

from surveillance_shared.ffmpeg_mobile import (  # noqa: E402
    finalize_mp4_for_mobile,
    h264_mobile_fragmented_mp4_args,
    h264_mobile_output_args,
    h264_mobile_video_args,
    mp4_ios_playable,
    mp4_probe_ok,
    remove_invalid_mp4,
)
from surveillance_shared.recording_thumbnails import (  # noqa: E402
    ensure_recording_thumbnail,
    write_recording_thumbnail_from_jpeg,
)
from surveillance_shared.rtsp_env import apply_rtsp_env  # noqa: E402

apply_rtsp_env()


def _write_clip_thumbnail(
    mp4_path: Path,
    *,
    detection_jpeg: Optional[bytes] = None,
    pre_jpegs: Optional[list[bytes]] = None,
    pre_seconds: int = 10,
) -> None:
    if not mp4_ios_playable(mp4_path):
        return
    jpeg = detection_jpeg
    if not jpeg and pre_jpegs:
        jpeg = pre_jpegs[-1]
    if jpeg and write_recording_thumbnail_from_jpeg(jpeg, mp4_path):
        return
    seek = max(0.1, float(max(1, int(pre_seconds))) - 0.5)
    if not ensure_recording_thumbnail(mp4_path, seek_seconds=seek):
        print("[edge] thumbnail failed:", mp4_path.name)

SEGMENT_SECONDS = 600
# Match LocalPublisher PRESETS (local_publisher.py) so RTSP reads keep up with the
# rpiCamera encoder; reading slower causes MediaMTX "reader is too slow, discarding frames".
QUALITY_CAPTURE_FPS = {"low": 15.0, "medium": 25.0, "high": 25.0}
COOLDOWN_SEC = 2.0
_DEFAULT_MOTION_BUFFER_SEC = 30.0


def _motion_buffer_seconds() -> float:
    raw = os.environ.get("EDGE_MOTION_BUFFER_SECONDS", "30").strip()
    try:
        v = float(raw)
    except ValueError:
        v = _DEFAULT_MOTION_BUFFER_SEC
    return max(10.0, min(120.0, v))


def _motion_clock_skew_max_sec() -> float:
    raw = os.environ.get("EDGE_MOTION_CLOCK_SKEW_MAX_SEC", "3").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 3.0
    return max(0.5, min(30.0, v))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fps_for_quality(quality: Any) -> float:
    q = str(quality or "medium").strip().lower()
    v = QUALITY_CAPTURE_FPS.get(q)
    if v is not None:
        return float(v)
    return 25.0


def _configure_rtsp_capture(cap: cv2.VideoCapture) -> None:
    """Shrink internal queue where supported so stale frames are dropped sooner."""
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass


def _flip_if_needed(frame: Any, flip: bool) -> Any:
    if not flip:
        return frame
    return cv2.flip(frame, -1)


class EdgeRecorder:
    def __init__(
        self,
        *,
        camera_mqtt_id: str,
        rtsp_url: str,
        recordings_root: Path,
        mqtt_host: str,
        mqtt_port: int = 1883,
        mqtt_user: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        topic_prefix: str = "surveillance/cameras",
        model_dir: Optional[Path] = None,
        on_settings_changed: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        if not camera_mqtt_id or not str(camera_mqtt_id).strip():
            raise ValueError("camera_mqtt_id required")
        if not rtsp_url or not str(rtsp_url).strip():
            raise ValueError("rtsp_url required")
        self._camera_mqtt_id = str(camera_mqtt_id).strip()
        self._rtsp_url = str(rtsp_url).strip()
        self._recordings_root = Path(recordings_root)
        self._mqtt_host = mqtt_host.strip()
        self._mqtt_port = int(mqtt_port)
        self._mqtt_user = mqtt_user
        self._mqtt_password = mqtt_password
        self._prefix = topic_prefix.strip().rstrip("/")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._settings: dict[str, Any] = {
            "recording_mode": "motion",
            "pre_record_seconds": 10,
            "post_record_seconds": 50,
            "quality": "medium",
            "flip_180": False,
        }
        self._thread: Optional[threading.Thread] = None
        self._mqtt: Optional[mqtt.Client] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._manual_lock = threading.Lock()
        self._manual_proc: Optional[subprocess.Popen] = None
        self._manual_out_path: Optional[Path] = None
        self._manual_rid: str = ""
        self._pm_lock = threading.Lock()
        self._pm_buf_lock = threading.Lock()
        self._pm_active = False
        self._pm_status: dict[str, Any] = {}
        self._pm_buf: collections.deque[tuple[float, bytes]] = collections.deque()
        self._pm_buffer_thread: Optional[threading.Thread] = None
        self._last_external_clip_trigger = 0.0
        self._external_clip_cooldown_until = 0.0
        self._clip_busy_lock = threading.Lock()
        self._clip_busy = False
        self._clip_thumb_by_rid: dict[str, bytes] = {}
        self._clip_thumb_lock = threading.Lock()
        self._on_settings_changed = on_settings_changed
        if model_dir:
            import os
            os.environ["SURVEILLANCE_MODEL_DIR"] = str(model_dir)

    def _topic_recording(self) -> str:
        return f"{self._prefix}/{self._camera_mqtt_id}/recording"

    def _ensure_mqtt(self) -> mqtt.Client:
        if self._mqtt is not None:
            return self._mqtt
        c = mqtt.Client()
        if self._mqtt_user:
            c.username_pw_set(self._mqtt_user, self._mqtt_password or "")
        c.connect(self._mqtt_host, self._mqtt_port, keepalive=30)
        c.loop_start()
        self._mqtt = c
        return c

    def _publish(
        self,
        *,
        status: str,
        recording_id: str,
        objects_detected: Optional[list[str]] = None,
        local_path: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> None:
        try:
            c = self._ensure_mqtt()
            payload = {
                "status": status,
                "recording_id": recording_id,
                "timestamp": _utc_iso(),
                "objects_detected": objects_detected or [],
                "local_path": local_path or "",
                "filename": filename or "",
            }
            c.publish(self._topic_recording(), json.dumps(payload), qos=0)
            agent_log(
                "H2",
                "worker.py:_publish",
                "mqtt_publish",
                {
                    "status": status,
                    "recording_id": recording_id,
                    "topic": self._topic_recording(),
                },
            )
        except Exception as e:
            print("[edge] mqtt publish failed:", e)
            agent_log(
                "H2",
                "worker.py:_publish",
                "mqtt_publish_failed",
                {"status": status, "recording_id": recording_id, "error": str(e)},
            )

    def update_settings(self, s: dict[str, Any]) -> dict[str, Any]:
        prev = self.snapshot_settings()
        with self._lock:
            for k, v in s.items():
                if k in self._settings:
                    self._settings[k] = v
            if self._settings["recording_mode"] not in ("motion", "continuous", "off"):
                self._settings["recording_mode"] = "motion"
            q = str(self._settings.get("quality", "medium")).lower()
            if q not in ("high", "medium", "low"):
                q = "medium"
            self._settings["quality"] = q
            self._settings["flip_180"] = bool(self._settings.get("flip_180", False))
            self._settings["pre_record_seconds"] = int(self._settings["pre_record_seconds"])
            self._settings["post_record_seconds"] = int(self._settings["post_record_seconds"])
            out = dict(self._settings)
        if prev.get("recording_mode") == "off" and out.get("recording_mode") != "off":
            threading.Thread(
                target=self.stop_manual_recording,
                name="edge-stop-manual-on-mode-change",
                daemon=True,
            ).start()
        cb = self._on_settings_changed
        if cb and (
            out.get("flip_180") != prev.get("flip_180")
            or out.get("quality") != prev.get("quality")
        ):
            cb(out)
        return out

    def snapshot_settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def _terminate_ffmpeg(self) -> None:
        proc = self._ffmpeg_proc
        self._ffmpeg_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="edge-recorder", daemon=True)
        self._thread.start()
        if self._pm_buffer_thread is None or not self._pm_buffer_thread.is_alive():
            self._pm_buffer_thread = threading.Thread(
                target=self._run_motion_clip_buffer,
                name="edge-motion-clip-buffer",
                daemon=True,
            )
            self._pm_buffer_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.stop_manual_recording()
        with self._pm_lock:
            self._pm_active = False
            self._pm_status = {}
        self._terminate_ffmpeg()
        if self._mqtt:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
            self._mqtt = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)

    def _run(self) -> None:
        while not self._stop.is_set():
            st = self.snapshot_settings()
            mode = st.get("recording_mode", "motion")
            if mode == "off":
                self._run_off_session()
            elif mode == "continuous":
                self._run_continuous_session(st)
            else:
                self._run_motion_idle_session()
            time.sleep(0.5)

    def _run_off_session(self) -> None:
        """No automatic recording; operator uses HTTP manual start/stop."""
        self._terminate_ffmpeg()
        while not self._stop.is_set():
            cur = self.snapshot_settings()
            if cur.get("recording_mode") != "off":
                break
            time.sleep(0.25)

    def manual_recording_active(self) -> bool:
        with self._manual_lock:
            p = self._manual_proc
            return p is not None and p.poll() is None

    def manual_recording_status(self) -> dict[str, Any]:
        with self._manual_lock:
            proc = self._manual_proc
            path = self._manual_out_path
            active = proc is not None and proc.poll() is None
            fn = path.name if path is not None and active else None
        return {"active": active, "filename": fn}

    def motion_clip_busy(self) -> bool:
        with self._pm_lock:
            if self._pm_active:
                return True
        with self._manual_lock:
            proc = self._manual_proc
            if proc is not None and proc.poll() is None:
                return True
        with self._clip_busy_lock:
            if self._clip_busy:
                return True
        return False

    def motion_clip_status(self) -> dict[str, Any]:
        with self._pm_lock:
            phase = str(self._pm_status.get("phase") or "idle")
            if not self._pm_active and phase not in ("materializing",):
                return {
                    "active": False,
                    "phase": "idle",
                    "remaining_seconds": 0,
                    "pre_seconds": 0,
                    "post_seconds": 0,
                    "recording_id": "",
                    "filename": None,
                    "objects_detected": [],
                }
            st = dict(self._pm_status)
            ends = float(st.get("ends_at") or 0.0)
            if phase == "materializing":
                st["remaining_seconds"] = 0
            else:
                st["remaining_seconds"] = max(0, int(ends - time.time()))
            st["active"] = True
            return st

    def trigger_motion_clip(
        self,
        *,
        person_detected_at: Optional[float] = None,
        duration_seconds: Optional[int] = None,
        pre_roll_seconds: Optional[int] = None,
        pre_seconds: Optional[int] = None,
        post_seconds: Optional[int] = None,
        objects_detected: Optional[list[str]] = None,
        thumbnail_jpeg: Optional[bytes] = None,
    ) -> dict[str, Any]:
        settings = self.snapshot_settings()
        if settings.get("recording_mode") != "motion":
            return {
                "accepted": False,
                "reason": "recording_mode_not_motion",
                **self.motion_clip_status(),
            }

        pre_s = int(
            pre_roll_seconds
            if pre_roll_seconds is not None
            else (pre_seconds if pre_seconds is not None else settings.get("pre_record_seconds", 10))
        )
        post_s = int(
            post_seconds if post_seconds is not None else settings.get("post_record_seconds", 50)
        )
        pre_s = max(1, min(120, pre_s))
        post_s = max(1, min(600, post_s))
        duration_s = int(duration_seconds if duration_seconds is not None else (pre_s + post_s))
        duration_s = max(pre_s + 1, min(600, duration_s))

        detected_at = float(person_detected_at if person_detected_at is not None else time.time())
        recv = time.time()
        if abs(recv - detected_at) > _motion_clock_skew_max_sec():
            print(
                "[edge] motion clip: controller/edge clock skew",
                round(abs(recv - detected_at), 2),
                "s; using receive time for person_detected_at",
            )
            detected_at = recv

        tags: list[str] = []
        if isinstance(objects_detected, list):
            for t in objects_detected:
                if isinstance(t, str) and t.strip():
                    tags.append(t.strip().lower())
        if not tags:
            tags = ["person"]

        if self.manual_recording_active():
            return {
                "accepted": False,
                "reason": "manual_recording_active",
                **self.motion_clip_status(),
            }
        with self._clip_busy_lock:
            if self._clip_busy:
                return {
                    "accepted": False,
                    "reason": "recording_in_progress",
                    **self.motion_clip_status(),
                }

        now = time.time()
        with self._pm_lock:
            if now - self._last_external_clip_trigger < COOLDOWN_SEC:
                return {
                    "accepted": False,
                    "reason": "cooldown",
                    **self.motion_clip_status(),
                }
            if self._pm_active:
                return {
                    "accepted": False,
                    "reason": "motion_clip_already_active",
                    **self.motion_clip_status(),
                }
            self._last_external_clip_trigger = now
            rid = f"evt_{int(time.time() * 1000)}"
            self._pm_active = True
            clip_start = detected_at - float(pre_s)
            clip_end = clip_start + float(duration_s)
            self._pm_status = {
                "active": True,
                "recording_id": rid,
                "phase": "starting",
                "pre_seconds": pre_s,
                "post_seconds": max(0, duration_s - pre_s),
                "duration_seconds": duration_s,
                "person_detected_at": detected_at,
                "clip_start_at": clip_start,
                "ends_at": clip_end,
                "remaining_seconds": max(0, int(clip_end - now)),
                "filename": None,
                "objects_detected": tags,
            }
        if thumbnail_jpeg:
            with self._clip_thumb_lock:
                self._clip_thumb_by_rid[rid] = bytes(thumbnail_jpeg)

        threading.Thread(
            target=self._start_external_motion_clip,
            args=(rid, detected_at, pre_s, duration_s, tags),
            name=f"motion-clip-{rid}",
            daemon=True,
        ).start()
        return {"accepted": True, **self.motion_clip_status()}

    def _start_external_motion_clip(
        self,
        rid: str,
        person_detected_at: float,
        pre_roll_seconds: int,
        duration_seconds: int,
        tags: list[str],
    ) -> None:
        """Time-window clip off the HTTP thread (buffer slice can be large)."""
        self._run_external_motion_clip(
            rid,
            person_detected_at,
            pre_roll_seconds,
            duration_seconds,
            tags,
        )

    def _buffer_frames_in_range(self, start: float, until: float) -> list[bytes]:
        if until < start:
            return []
        with self._pm_buf_lock:
            frames = [blob for ts, blob in self._pm_buf if start <= ts <= until]
        return frames

    def _clear_motion_buffer(self) -> None:
        with self._pm_buf_lock:
            self._pm_buf.clear()

    def _run_motion_clip_buffer(self) -> None:
        """Rolling JPEG buffer (default 30s) for controller-triggered pre-roll."""
        cap: Optional[cv2.VideoCapture] = None
        while not self._stop.is_set():
            st = self.snapshot_settings()
            if st.get("recording_mode") != "motion":
                if cap is not None:
                    cap.release()
                    cap = None
                time.sleep(0.5)
                continue
            flip = bool(st.get("flip_180", False))
            fps = _fps_for_quality(st.get("quality"))
            buffer_sec = _motion_buffer_seconds()
            maxlen = max(1, int(buffer_sec * fps))
            period = 1.0 / fps

            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
                _configure_rtsp_capture(cap)
                if not cap.isOpened():
                    time.sleep(2.0)
                    continue

            loop_t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                cap = None
                time.sleep(1.0)
                continue

            frame = _flip_if_needed(frame, flip)
            with self._pm_lock:
                recording = self._pm_active
            if not recording:
                enc_ok, jpg = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
                )
                if enc_ok:
                    now_ts = time.time()
                    blob = jpg.tobytes()
                    cutoff = now_ts - buffer_sec
                    with self._pm_buf_lock:
                        if self._pm_buf.maxlen != maxlen:
                            self._pm_buf = collections.deque(
                                list(self._pm_buf)[-maxlen:],
                                maxlen=maxlen,
                            )
                        while self._pm_buf and self._pm_buf[0][0] < cutoff:
                            self._pm_buf.popleft()
                        self._pm_buf.append((now_ts, blob))

            elapsed = time.perf_counter() - loop_t0
            time.sleep(max(0.0, period - elapsed))

        if cap is not None:
            cap.release()

    def _run_external_motion_clip(
        self,
        rid: str,
        person_detected_at: float,
        pre_roll_seconds: int,
        duration_seconds: int,
        tags: list[str],
    ) -> None:
        ff = shutil.which("ffmpeg")
        out_dir = self._recordings_root
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / f"_tmp_{rid}"
        out_mp4 = out_dir / f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        flip = bool(self.snapshot_settings().get("flip_180", False))
        read_fps = _fps_for_quality(self.snapshot_settings().get("quality"))
        clip_start = float(person_detected_at) - float(pre_roll_seconds)
        clip_end = clip_start + float(duration_seconds)
        pre_jpegs = self._buffer_frames_in_range(clip_start, float(person_detected_at))

        try:
            if not ff:
                print("[edge] motion clip: ffmpeg missing")
                return

            tmp.mkdir(parents=True, exist_ok=True)
            idx = 0
            for blob in pre_jpegs:
                (tmp / f"{idx:05d}.jpg").write_bytes(blob)
                idx += 1

            self._publish(
                status="Start",
                recording_id=rid,
                objects_detected=tags,
                local_path=out_mp4.resolve().as_posix(),
            )
            agent_log(
                "H1",
                "worker.py:_run_external_motion_clip",
                "clip_start",
                {
                    "rid": rid,
                    "pre_roll_s": pre_roll_seconds,
                    "duration_s": duration_seconds,
                    "detected_at": person_detected_at,
                    "buffered_frames": len(pre_jpegs),
                },
            )

            cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
            _configure_rtsp_capture(cap)
            end = clip_end
            with self._pm_lock:
                self._pm_status["phase"] = "post_roll"
                self._pm_status["ends_at"] = end
                self._pm_status["remaining_seconds"] = max(0, int(end - time.time()))
            while time.time() < end and not self._stop.is_set():
                with self._pm_lock:
                    self._pm_status["remaining_seconds"] = max(0, int(end - time.time()))

                if not cap.isOpened():
                    break
                loop_t0 = time.perf_counter()
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                frame = _flip_if_needed(frame, flip)
                enc_ok, jpg = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
                )
                if enc_ok:
                    (tmp / f"{idx:05d}.jpg").write_bytes(jpg.tobytes())
                    idx += 1
                read_fps = _fps_for_quality(self.snapshot_settings().get("quality"))
                elapsed = time.perf_counter() - loop_t0
                time.sleep(max(0.0, (1.0 / read_fps) - elapsed))
            if cap is not None:
                cap.release()

            with self._pm_lock:
                self._pm_status["phase"] = "materializing"
                self._pm_status["remaining_seconds"] = 0
                self._pm_status["ends_at"] = time.time()

            self._publish(status="Stop", recording_id=rid, local_path="")
            agent_log(
                "H1",
                "worker.py:_run_external_motion_clip",
                "capture_stop_published",
                {"rid": rid, "frames": idx, "duration_s": duration_seconds},
            )

            if idx == 0:
                print("[edge] motion clip: no frames captured (pre+post empty)")
                return

            cmd = [
                ff,
                "-y",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-framerate",
                str(max(1, int(round(read_fps)))),
                "-i",
                str(tmp / "%05d.jpg"),
                *h264_mobile_output_args(preset="veryfast"),
                str(out_mp4),
            ]
            r = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
            )
            if r.returncode != 0:
                print("[edge] motion clip ffmpeg failed:", (r.stderr or "")[-400:])
                remove_invalid_mp4(out_mp4)
                self._publish(status="Stop", recording_id=rid, local_path="")
                return
            if not finalize_mp4_for_mobile(out_mp4):
                print("[edge] motion clip finalize failed:", out_mp4.name)
            if not mp4_ios_playable(out_mp4):
                print("[edge] motion clip not playable, removing:", out_mp4.name)
                remove_invalid_mp4(out_mp4)
                self._publish(status="Stop", recording_id=rid, local_path="")
                return

            detection_jpeg: Optional[bytes] = None
            with self._clip_thumb_lock:
                detection_jpeg = self._clip_thumb_by_rid.pop(rid, None)
            _write_clip_thumbnail(
                out_mp4,
                detection_jpeg=detection_jpeg,
                pre_jpegs=pre_jpegs,
                pre_seconds=pre_roll_seconds,
            )

            with self._pm_lock:
                self._pm_status["filename"] = out_mp4.name

            self._publish(
                status="Stop",
                recording_id=rid,
                objects_detected=tags,
                local_path=out_mp4.resolve().as_posix(),
                filename=out_mp4.name,
            )
        except Exception as e:
            print("[edge] motion clip failed:", e)
            self._publish(status="Stop", recording_id=rid, local_path="")
        finally:
            with self._clip_thumb_lock:
                self._clip_thumb_by_rid.pop(rid, None)
            saved_fn: Optional[str] = None
            with self._pm_lock:
                saved_fn = self._pm_status.get("filename")
                self._pm_status = {
                    "active": False,
                    "phase": "idle",
                    "remaining_seconds": 0,
                    "pre_seconds": pre_roll_seconds,
                    "post_seconds": max(0, duration_seconds - pre_roll_seconds),
                    "duration_seconds": duration_seconds,
                    "recording_id": rid,
                    "filename": saved_fn,
                    "objects_detected": tags,
                }
                self._pm_active = False
            self._clear_motion_buffer()
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
            with self._pm_lock:
                self._external_clip_cooldown_until = time.time() + COOLDOWN_SEC

    def start_manual_recording(self) -> dict[str, Any]:
        ff = shutil.which("ffmpeg")
        if not ff:
            raise ValueError("ffmpeg not found on PATH")
        st = self.snapshot_settings()
        if st.get("recording_mode") != "off":
            raise ValueError("set recording mode to Off before manual recording")
        with self._manual_lock:
            if self._manual_proc is not None and self._manual_proc.poll() is None:
                raise ValueError("manual recording already active")
            self._recordings_root.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = self._recordings_root / f"manual_{ts}.mp4"
            cmd = [
                ff,
                "-y",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-rtsp_transport",
                "tcp",
                "-i",
                self._rtsp_url,
            ]
            if st.get("flip_180"):
                cmd.extend(["-vf", "vflip,hflip"])
            cmd.extend(h264_mobile_fragmented_mp4_args(preset="veryfast"))
            cmd.append(str(out_path))
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                raise ValueError(f"ffmpeg failed to start: {e}") from e
            self._manual_proc = proc
            self._manual_out_path = out_path
            self._manual_rid = f"manual_{ts}"
        self._publish(
            status="Start",
            recording_id=self._manual_rid,
            local_path=out_path.resolve().as_posix(),
        )
        return {"active": True, "filename": out_path.name, "recording_id": self._manual_rid}

    def stop_manual_recording(self) -> dict[str, Any]:
        with self._manual_lock:
            proc = self._manual_proc
            out_path = self._manual_out_path
            rid = self._manual_rid
            self._manual_proc = None
            self._manual_out_path = None
            self._manual_rid = ""
        if proc is None:
            return {"active": False, "filename": None}
        filename = out_path.name if out_path else ""
        try:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=20)
        except Exception:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        playable_name = ""
        if out_path is not None and out_path.is_file():
            time.sleep(1.0)
            ok = finalize_mp4_for_mobile(out_path)
            if not ok:
                time.sleep(1.5)
                ok = finalize_mp4_for_mobile(out_path)
            if ok and mp4_ios_playable(out_path):
                playable_name = out_path.name
                pre_s = int(self.snapshot_settings().get("pre_record_seconds", 10))
                _write_clip_thumbnail(out_path, pre_seconds=pre_s)
            else:
                print("[edge] manual clip unusable, removing:", out_path.name)
                remove_invalid_mp4(out_path)
                filename = ""
        self._publish(
            status="Stop",
            recording_id=rid or "manual",
            local_path=out_path.resolve().as_posix() if out_path and playable_name else "",
            filename=playable_name or None,
        )
        return {"active": False, "filename": playable_name or None}

    def _run_continuous_session(self, st: dict[str, Any]) -> None:
        ff = shutil.which("ffmpeg")
        if not ff:
            print("[edge] ffmpeg missing")
            time.sleep(5)
            return
        rid = f"cont_{int(time.time())}"
        out_dir = self._recordings_root
        out_dir.mkdir(parents=True, exist_ok=True)
        list_path = out_dir / "_continuous_segment_list.txt"
        try:
            if list_path.is_file():
                list_path.unlink()
        except OSError:
            pass
        pattern = "%Y-%m-%d_%H-%M-%S.mp4"
        cmd = [
            ff,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._rtsp_url,
        ]
        if st.get("flip_180"):
            cmd.extend(["-vf", "vflip,hflip"])
        cmd.extend(h264_mobile_video_args(preset="veryfast"))
        cmd.extend(
            [
            "-f",
            "segment",
            "-segment_time",
            str(int(SEGMENT_SECONDS)),
            "-segment_list",
            list_path.name,
            "-segment_list_type",
            "flat",
            "-segment_list_size",
            "1000",
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            pattern,
            ]
        )
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(out_dir),
            )
        except OSError as e:
            print("[edge] ffmpeg start failed:", e)
            time.sleep(5)
            return

        self._publish(
            status="Start",
            recording_id=rid,
            local_path=out_dir.resolve().as_posix(),
        )
        list_line_count = 0
        last_filename = ""
        while not self._stop.is_set():
            proc = self._ffmpeg_proc
            if proc is None:
                break
            if proc.poll() is not None:
                break
            cur = self.snapshot_settings()
            if cur.get("recording_mode") != "continuous":
                break
            if list_path.is_file():
                try:
                    lines = list_path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                while list_line_count < len(lines):
                    raw = lines[list_line_count].strip()
                    list_line_count += 1
                    if not raw:
                        continue
                    seg_path = Path(raw)
                    if not seg_path.is_absolute():
                        seg_path = (out_dir / raw).resolve()
                    else:
                        seg_path = seg_path.resolve()
                    if last_filename and last_filename != seg_path.name:
                        prev = out_dir / last_filename
                        if prev.is_file():
                            finalize_mp4_for_mobile(prev)
                            if not mp4_ios_playable(prev):
                                remove_invalid_mp4(prev)
                    last_filename = seg_path.name
                    if seg_path.is_file() and mp4_ios_playable(seg_path):
                        self._publish(
                            status="InProgress",
                            recording_id=rid,
                            local_path=seg_path.as_posix(),
                            filename=last_filename,
                        )
            time.sleep(0.2)

        self._terminate_ffmpeg()
        stop_filename = ""
        if last_filename:
            last_seg = out_dir / last_filename
            if last_seg.is_file():
                time.sleep(0.5)
                finalize_mp4_for_mobile(last_seg)
                if mp4_ios_playable(last_seg):
                    stop_filename = last_filename
                else:
                    remove_invalid_mp4(last_seg)
        self._publish(
            status="Stop",
            recording_id=rid,
            local_path=out_dir.resolve().as_posix(),
            filename=stop_filename or None,
        )

    def _run_motion_idle_session(self) -> None:
        """Motion mode: only the rolling buffer thread runs; clips come from the controller."""
        self._terminate_ffmpeg()
        while not self._stop.is_set():
            cur = self.snapshot_settings()
            if cur.get("recording_mode") != "motion":
                return
            time.sleep(0.5)
