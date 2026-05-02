"""
Per-camera recording: continuous (ffmpeg segment) or motion (pre/post JPEG ring + SSD).

Requires ffmpeg in PATH for muxing and for continuous copy mode.
"""

from __future__ import annotations

import collections
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .rtsp_env import apply_rtsp_env

apply_rtsp_env()

import cv2

from .camera_store import add_change_listener, list_cameras, remove_change_listener
from .detector import Detector

RECORDINGS_ROOT = Path(__file__).resolve().parent.parent / "data" / "recordings"
SEGMENT_SECONDS = 600
BUFFER_FPS = 10.0
DETECT_EVERY_N_FRAMES = 3
COOLDOWN_SEC = 2.0

_first_motion_trigger_logged: set[int] = set()
_first_motion_lock = threading.Lock()


def _log_first_motion_trigger(cam_id: int) -> None:
    with _first_motion_lock:
        if cam_id in _first_motion_trigger_logged:
            return
        _first_motion_trigger_logged.add(cam_id)
    print(
        f"[recording] first motion trigger for camera id={cam_id} (clip assembly starting)"
    )


def recordings_dir_for_camera(cam_id: int) -> Path:
    p = RECORDINGS_ROOT / str(int(cam_id))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _which_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


class _CameraWorker:
    def __init__(self, cam: dict[str, Any]) -> None:
        self._stop = threading.Event()
        self._cam_lock = threading.Lock()
        self._cam = dict(cam)
        self._thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None

    def snapshot(self) -> dict[str, Any]:
        with self._cam_lock:
            return dict(self._cam)

    def update(self, cam: dict[str, Any]) -> None:
        with self._cam_lock:
            self._cam = dict(cam)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"rec-{self._cam['id']}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate_ffmpeg()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)

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

    def _run(self) -> None:
        while not self._stop.is_set():
            cam = self.snapshot()
            mode = cam.get("settings", {}).get("recording_mode", "motion")
            if mode == "continuous":
                self._run_continuous_session(cam)
            else:
                self._run_motion_session(cam)
            time.sleep(0.5)

    def _run_continuous_session(self, cam: dict[str, Any]) -> None:
        ff = _which_ffmpeg()
        if not ff:
            print("[recording] ffmpeg not found; continuous recording disabled")
            time.sleep(5)
            return
        url = cam.get("url")
        if not url:
            time.sleep(2)
            return
        out_dir = recordings_dir_for_camera(int(cam["id"]))
        pattern = str(out_dir / "%Y-%m-%d_%H-%M-%S.mp4")
        cmd = [
            ff,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(int(SEGMENT_SECONDS)),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            pattern,
        ]
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            print("[recording] ffmpeg start failed:", e)
            time.sleep(5)
            return

        while not self._stop.is_set():
            proc = self._ffmpeg_proc
            if proc is None:
                break
            rc = proc.poll()
            if rc is not None:
                break
            cur = self.snapshot()
            st = cur.get("settings", {})
            if st.get("recording_mode") != "continuous" or cur.get("url") != url:
                break
            time.sleep(0.8)

        self._terminate_ffmpeg()

    def _run_motion_session(self, cam: dict[str, Any]) -> None:
        self._terminate_ffmpeg()
        ff = _which_ffmpeg()
        if not ff:
            print("[recording] ffmpeg not found; motion recording clips disabled")
            time.sleep(5)
            return

        url = cam.get("url")
        if not url:
            time.sleep(2)
            return

        detector = Detector()
        cap: cv2.VideoCapture | None = None
        last_clip_end = 0.0
        opened_url: str | None = None

        while not self._stop.is_set():
            cur = self.snapshot()
            st = cur.get("settings", {})
            if st.get("recording_mode") == "continuous":
                if cap is not None:
                    cap.release()
                    cap = None
                return

            target_url = cur.get("url")
            if not target_url:
                time.sleep(1)
                continue

            if target_url != opened_url:
                if cap is not None:
                    cap.release()
                    cap = None
                opened_url = target_url

            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(target_url, cv2.CAP_FFMPEG)

            pre_s = int(st.get("pre_record_seconds", 10))
            post_s = int(st.get("post_record_seconds", 50))
            maxlen = max(1, int(pre_s * BUFFER_FPS))
            buf: collections.deque[bytes] = collections.deque(maxlen=maxlen)

            frame_i = 0
            while not self._stop.is_set():
                cur = self.snapshot()
                st2 = cur.get("settings", {})
                if st2.get("recording_mode") == "continuous" or cur.get("url") != target_url:
                    break

                if cap is None or not cap.isOpened():
                    time.sleep(0.5)
                    break

                ok, frame = cap.read()
                if not ok or frame is None:
                    if cap is not None:
                        cap.release()
                    cap = None
                    time.sleep(0.5)
                    break

                enc_ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if not enc_ok:
                    continue
                jpg_bytes = jpg.tobytes()
                buf.append(jpg_bytes)

                now = time.time()
                interesting = False
                if frame_i % DETECT_EVERY_N_FRAMES == 0:
                    interesting = detector.detect_interesting(frame)
                frame_i += 1

                if interesting and (now - last_clip_end) >= COOLDOWN_SEC:
                    _log_first_motion_trigger(int(cur["id"]))
                    pre_frames = list(buf)
                    self._materialize_event(
                        ff=ff,
                        cam_id=int(cur["id"]),
                        pre_jpegs=pre_frames,
                        cap=cap,
                        post_seconds=post_s,
                        detector=detector,
                    )
                    last_clip_end = time.time()
                    buf.clear()

                time.sleep(max(0, 1.0 / BUFFER_FPS - 0.01))

    def _materialize_event(
        self,
        *,
        ff: str,
        cam_id: int,
        pre_jpegs: list[bytes],
        cap: cv2.VideoCapture,
        post_seconds: int,
        detector: Detector,
    ) -> None:
        out_dir = recordings_dir_for_camera(cam_id)
        tmp = out_dir / f"_tmp_{int(time.time() * 1000)}"
        tmp.mkdir(parents=True, exist_ok=False)
        try:
            idx = 0
            for blob in pre_jpegs:
                (tmp / f"{idx:05d}.jpg").write_bytes(blob)
                idx += 1

            end = time.time() + post_seconds
            cur_url = self.snapshot().get("url")
            post_i = 0
            while time.time() < end and not self._stop.is_set():
                cur = self.snapshot()
                if cur.get("settings", {}).get("recording_mode") != "motion":
                    break
                if cur.get("url") != cur_url:
                    break
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if post_i % DETECT_EVERY_N_FRAMES == 0 and detector.detect_interesting(frame):
                    end = time.time() + post_seconds
                enc_ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if not enc_ok:
                    post_i += 1
                    continue
                (tmp / f"{idx:05d}.jpg").write_bytes(jpg.tobytes())
                idx += 1
                post_i += 1
                time.sleep(max(0, 1.0 / BUFFER_FPS - 0.01))

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_mp4 = out_dir / f"evt_{ts}.mp4"
            # If no frames, skip
            if idx == 0:
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
                print("[recording] ffmpeg clip failed:", (r.stderr or "")[-500:])
        except Exception as e:
            print("[recording] event failed:", e)
        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass


class RecordingManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: Dict[int, _CameraWorker] = {}

    def worker_status(self) -> List[Dict[str, Any]]:
        """Snapshot for GET /system/recording (thread-safe)."""
        out: List[Dict[str, Any]] = []
        with self._lock:
            for cid, w in sorted(self._workers.items(), key=lambda x: x[0]):
                cam = w.snapshot()
                st = cam.get("settings", {})
                alive = bool(w._thread and w._thread.is_alive())
                out.append(
                    {
                        "camera_id": int(cid),
                        "recording_mode": st.get("recording_mode", "motion"),
                        "thread_alive": alive,
                    }
                )
        return out

    def start(self) -> None:
        add_change_listener(self.sync)
        self.sync()

    def stop(self) -> None:
        remove_change_listener(self.sync)
        with self._lock:
            for w in list(self._workers.values()):
                w.stop()
            self._workers.clear()

    def sync(self) -> None:
        cams = list_cameras()
        want = {
            int(c["id"]): c
            for c in cams
            if not (isinstance(c.get("edge_base_url"), str) and c["edge_base_url"].strip())
        }
        with self._lock:
            for cid in list(self._workers.keys()):
                if cid not in want:
                    self._workers.pop(cid).stop()
            for cid, cam in want.items():
                if cid not in self._workers:
                    w = _CameraWorker(cam)
                    w.start()
                    self._workers[cid] = w
                else:
                    self._workers[cid].update(cam)


recording_manager = RecordingManager()
