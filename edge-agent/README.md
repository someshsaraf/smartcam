# Pi 4 edge agent

Runs on each Raspberry Pi 4: MotionNet SSD detection, local MP4 recordings, MQTT events, HTTP API for the controller (Pi 5) to proxy files.

## Pi 5 vs Pi 4

- **Controller** code lives on the **Raspberry Pi 5** (`controller/` in the monorepo).
- **Edge agent** lives on each **Raspberry Pi 4**. There is **no** valid symlink from the Pi 4 to the Pi 5’s `shared` tree.

The edge repo includes a **concrete copy** of the Python package at **`edge-agent/shared/surveillance_shared/`** (not a link). After editing **`controller/shared/surveillance_shared/`** on your workstation, run:

```bash
edge-agent/scripts/sync-shared-from-controller.sh
```

and deploy/commit the updated files under `edge-agent/shared/`.

## Python import path

`app/_shared_bootstrap.py` adds a directory that **contains** `surveillance_shared/` to `sys.path`, in this order:

1. **`SURVEILLANCE_SHARED_PATH`** — optional explicit directory
2. **`edge-agent/shared`** — normal layout (this repo copy)
3. **`../controller/shared`** — only when a full monorepo checkout exists on the **same** host (developer laptop)
4. **`../shared`** — legacy layout

## Environment

| Variable | Meaning |
|----------|---------|
| `SURVEILLANCE_RTSP_URL` | Input stream (RTSP) for OpenCV + ffmpeg |
| `SURVEILLANCE_MQTT_HOST` | MQTT broker on the **Pi 5** — defaults to **`192.168.2.104`** if unset |
| `SURVEILLANCE_MQTT_PORT` | Default `1883` |
| `SURVEILLANCE_EDGE_CAMERA_ID` | Topic segment `surveillance/cameras/{id}/recording` |
| `SURVEILLANCE_RECORDINGS_DIR` | Output folder (SD card path) |
| `SURVEILLANCE_MODEL_DIR` | SSD models directory — default **`edge-agent/models/`** (populate with `scripts/fetch_ssd_models.sh`) |
| `SURVEILLANCE_MQTT_TOPIC_PREFIX` | Default `surveillance/cameras` |
| `SURVEILLANCE_SHARED_PATH` | Optional override for the folder containing `surveillance_shared/` |

## SSD models (Pi 4)

```bash
edge-agent/scripts/fetch_ssd_models.sh
```

Writes **`MobileNetSSD_deploy.prototxt`** and **`mobilenet_iter_73000.caffemodel`** into **`edge-agent/models/`**.

## Run

```bash
cd /path/to/edge-agent
./scripts/fetch_ssd_models.sh   # once, if models/ is empty
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## MQTT payload (JSON)

`status`: `Start` | `InProgress` | `Stop`; `recording_id`; `timestamp` (ISO UTC); `objects_detected` (strings); `local_path`; optional `filename`.
