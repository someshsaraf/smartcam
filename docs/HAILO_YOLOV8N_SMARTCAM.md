# Hailo YOLOv8n SmartCam integration

This build adds a Hailo-backed detection path for the controller live overlays.

## What it does

The compiled `yolov8n.hef` is a standard COCO YOLOv8n model, so it detects `person`, not faces. The SmartCam backend now uses this pipeline:

```text
RTSP frame
  -> Hailo YOLOv8n person detection
  -> optional OpenCV face detection only inside person boxes
  -> existing /ws/detections overlay payload
```

This gives both person and face boxes while greatly reducing the old Haar false positives on doors, walls, and background textures.

## Files changed

- `controller/backend/app/hailo_yolov8_backend.py` - new Hailo YOLOv8n runtime wrapper.
- `controller/backend/app/face_backend.py` - now supports `SMARTCAM_FACE_BACKEND=hailo_person_face`.
- `controller/frontend/src/App.jsx` - overlay labels now show `person 91.2%` or `face 88.0%`.
- `controller/backend/.env` - Hailo configuration added.

## Required model file

Copy your generated HEF to:

```bash
controller/backend/models/yolov8n.hef
```

or set:

```env
SMARTCAM_HAILO_HEF_PATH=/absolute/path/to/yolov8n.hef
```

## Pi 5 runtime requirement

Install HailoRT on the Pi 5 so Python can import:

```python
from hailo_platform import HEF, VDevice
```

Verify:

```bash
python - <<'CHECK_HAILO'
import hailo_platform
print('hailo_platform OK')
CHECK_HAILO
```

## Recommended `.env`

```env
SMARTCAM_FACE_BACKEND=hailo_person_face
SMARTCAM_HAILO_HEF_PATH=models/yolov8n.hef
SMARTCAM_HAILO_INPUT_SIZE=640
SMARTCAM_PERSON_CONFIDENCE=0.45
SMARTCAM_SHOW_PERSON_BOXES=1
SMARTCAM_HAILO_FACE_SECOND_STAGE=opencv
```

To show only person boxes and skip face boxes:

```env
SMARTCAM_HAILO_FACE_SECOND_STAGE=none
```

## Important note about true face detection on Hailo

Your current HEF is standard COCO YOLOv8n. It does not contain a `face` class. The face boxes in this patch are detected by OpenCV only inside person regions. For true Hailo face detection later, compile a dedicated face model such as SCRFD/RetinaFace/YOLO-face and replace the second stage.
