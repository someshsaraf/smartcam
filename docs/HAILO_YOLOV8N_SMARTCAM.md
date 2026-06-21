# Hailo YOLOv8n SmartCam integration

Controller detection pipeline:

```text
RTSP frame
  -> MOG2 motion gate
  -> Hailo YOLOv8n (OpenCV SSD fallback when Hailo unavailable)
  -> ByteTrack
  -> SMARTCAM_EVENT_CONFIRM_FRAMES consecutive validation
  -> motion clip / WebSocket overlay / event_store
```

## Files

- `controller/backend/app/mog2_motion_gate.py` — MOG2 foreground gate
- `controller/backend/app/hailo_yolov8_backend.py` — Hailo YOLOv8n runtime
- `controller/backend/app/byte_tracker.py` — ByteTrack-style association
- `controller/backend/app/detection_pipeline.py` — per-camera orchestrator
- `controller/backend/app/person_rtsp_supervisor.py` — RTSP workers

## Required model file

Copy your compiled HEF to:

```bash
controller/backend/models/yolov8n.hef
```

or set:

```env
SMARTCAM_HAILO_HEF_PATH=/absolute/path/to/yolov8n.hef
```

## Recommended `.env`

```env
SMARTCAM_PERSON_CONFIDENCE=0.25
SMARTCAM_ANIMAL_CONFIDENCE=0.20
SMARTCAM_DETECTION_FPS=5
SMARTCAM_EVENT_CONFIRM_FRAMES=2
SMARTCAM_HAILO_HEF_PATH=models/yolov8n.hef
SMARTCAM_HAILO_INPUT_SIZE=640
```
