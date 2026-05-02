import time

from .rtsp_env import apply_rtsp_env

apply_rtsp_env()

import cv2

from .camera_store import get_selected_camera

def generate_frames():
    cap = None

    while True:
        cam = get_selected_camera()

        if not cam:
            time.sleep(1)
            continue

        url = cam["url"]

        if cap is None or not cap.isOpened():
            print("[STREAM] Opening:", url)
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

        ret, frame = cap.read()

        if not ret:
            print("[STREAM] Failed, reconnecting...")
            cap.release()
            cap = None
            time.sleep(1)
            continue

        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" +
            frame_bytes +
            b"\r\n"
        )
