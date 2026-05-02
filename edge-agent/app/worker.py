"""
Pi 4 edge recording worker: motion (SSD + clips) or continuous (ffmpeg segment).
Publishes MQTT JSON on ``surveillance/cameras/{id}/recording``.
"""

from __future__ import annotations

import collections
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as mqtt

from . import _shared_bootstrap  # noqa: F401 — sys.path for surveillance_shared

import cv2

from surveillance_shared.detector import Detector  # noqa: E402
from surveillance_shared.rtsp_env import apply_rtsp_env  # noqa: E402

apply_rtsp_env()

SEGMENT_SECONDS = 600
BUFFER_FPS = 10.0
DETECT_EVERY_N_FRAMES = 3
COOLDOWN_SEC = 2.0


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        except Exception as e:
            print("[edge] mqtt publish failed:", e)

    def update_settings(self, s: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for k, v in s.items():
                if k in self._settings:
                    self._settings[k] = v
            if self._settings["recording_mode"] not in ("motion", "continuous"):
                self._settings["recording_mode"] = "motion"
            q = str(self._settings.get("quality", "medium")).lower()
            if q not in ("high", "medium", "low"):
                q = "medium"
            self._settings["quality"] = q
            self._settings["flip_180"] = bool(self._settings.get("flip_180", False))
            self._settings["pre_record_seconds"] = int(self._settings["pre_record_seconds"])
            self._settings["post_record_seconds"] = int(self._settings["post_record_seconds"])
            out = dict(self._settings)
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

    def stop(self) -> None:
        self._stop.set()
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
            if mode == "continuous":
                self._run_continuous_session(st)
            else:
                self._run_motion_session(st)
            time.sleep(0.5)

    def _run_continuous_session(self, st: dict[str, Any]) -> None:
        ff = shutil.which("ffmpeg")
        if not ff:
            print("[edge] ffmpeg missing")
            time.sleep(5)
            return
        rid = f"cont_{int(time.time())}"
        out_dir = self._recordings_root
        out_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(out_dir / "%Y-%m-%d_%H-%M-%S.mp4")
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
        cmd.extend([
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-f",
            "segment",
            "-segment_time",
            str(int(SEGMENT_SECONDS)),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            pattern,
        ])
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
        last_tick = 0.0
        while not self._stop.is_set():
            proc = self._ffmpeg_proc
            if proc is None:
                break
            if proc.poll() is not None:
                break
            cur = self.snapshot_settings()
            if cur.get("recording_mode") != "continuous":
                break
            now = time.time()
            if now - last_tick >= 1.0:
                self._publish(
                    status="InProgress",
                    recording_id=rid,
                    local_path=out_dir.resolve().as_posix(),
                )
                last_tick = now
            time.sleep(0.2)

        self._terminate_ffmpeg()
        self._publish(
            status="Stop",
            recording_id=rid,
            local_path=out_dir.resolve().as_posix(),
        )

    def _run_motion_session(self, st: dict[str, Any]) -> None:
        self._terminate_ffmpeg()
        ff = shutil.which("ffmpeg")
        if not ff:
            time.sleep(5)
            return

        detector = Detector()
        cap: Optional[cv2.VideoCapture] = None
        last_clip_end = 0.0

        while not self._stop.is_set():
            cur = self.snapshot_settings()
            if cur.get("recording_mode") == "continuous":
                if cap is not None:
                    cap.release()
                return

            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)

            pre_s = int(cur.get("pre_record_seconds", 10))
            post_s = int(cur.get("post_record_seconds", 50))
            flip = bool(cur.get("flip_180", False))
            maxlen = max(1, int(pre_s * BUFFER_FPS))
            buf: collections.deque[bytes] = collections.deque(maxlen=maxlen)
            frame_i = 0

            while not self._stop.is_set():
                cur = self.snapshot_settings()
                if cur.get("recording_mode") != "motion":
                    break

                if cap is None or not cap.isOpened():
                    break

                ok, frame = cap.read()
                if not ok or frame is None:
                    if cap is not None:
                        cap.release()
                    cap = None
                    break

                frame = _flip_if_needed(frame, flip)
                enc_ok, jpg = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
                )
                if not enc_ok:
                    continue
                buf.append(jpg.tobytes())

                now = time.time()
                interesting = False
                tags: list[str] = []
                if frame_i % DETECT_EVERY_N_FRAMES == 0:
                    interesting, tags = detector.detect_interesting_with_tags(frame)
                frame_i += 1

                if interesting and (now - last_clip_end) >= COOLDOWN_SEC:
                    pre_frames = list(buf)
                    self._materialize_event(
                        ff=ff,
                        pre_jpegs=pre_frames,
                        cap=cap,
                        post_seconds=post_s,
                        detector=detector,
                        flip=flip,
                        tags=tags,
                    )
                    last_clip_end = time.time()
                    buf.clear()

                time.sleep(max(0, 1.0 / BUFFER_FPS - 0.01))

    def _materialize_event(
        self,
        *,
        ff: str,
        pre_jpegs: list[bytes],
        cap: cv2.VideoCapture,
        post_seconds: int,
        detector: Detector,
        flip: bool,
        tags: list[str],
    ) -> None:
        rid = f"evt_{int(time.time() * 1000)}"
        out_dir = self._recordings_root
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / f"_tmp_{rid}"
        tmp.mkdir(parents=True, exist_ok=False)
        out_mp4 = out_dir / f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        try:
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

            end = time.time() + post_seconds
            post_i = 0
            last_tick = 0.0
            while time.time() < end and not self._stop.is_set():
                cur = self.snapshot_settings()
                if cur.get("recording_mode") != "motion":
                    break
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                frame = _flip_if_needed(frame, flip)
                now = time.time()
                if now - last_tick >= 1.0:
                    _, tags2 = detector.detect_interesting_with_tags(frame)
                    self._publish(
                        status="InProgress",
                        recording_id=rid,
                        objects_detected=tags2 or tags,
                        local_path=out_mp4.resolve().as_posix(),
                    )
                    last_tick = now
                if post_i % DETECT_EVERY_N_FRAMES == 0:
                    hit, _ = detector.detect_interesting_with_tags(frame)
                    if hit:
                        end = time.time() + post_seconds
                enc_ok, jpg = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
                )
                if enc_ok:
                    (tmp / f"{idx:05d}.jpg").write_bytes(jpg.tobytes())
                    idx += 1
                post_i += 1
                time.sleep(max(0, 1.0 / BUFFER_FPS - 0.01))

            if idx == 0:
                self._publish(status="Stop", recording_id=rid, local_path="")
                return

            cmd = [
                ff,
                "-y",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-framerate",
                str(int(BUFFER_FPS)),
                "-i",
                str(tmp / "%05d.jpg"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
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
                print("[edge] ffmpeg clip failed:", (r.stderr or "")[-400:])
                self._publish(status="Stop", recording_id=rid, local_path="")
                return

            self._publish(
                status="Stop",
                recording_id=rid,
                objects_detected=tags,
                local_path=out_mp4.resolve().as_posix(),
                filename=out_mp4.name,
            )
        except Exception as e:
            print("[edge] materialize failed:", e)
            self._publish(status="Stop", recording_id=rid, local_path="")
        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
