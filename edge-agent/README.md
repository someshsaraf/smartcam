# Pi 4 edge agent

## Description

Runs on **each Raspberry Pi 4** connected to a camera: MobileNet-SSD motion detection, local MP4 recordings, MQTT status events to the broker on the Pi 5 controller, and an HTTP API the controller uses to list and proxy recordings. It also advertises the device on the LAN via mDNS ([`app/main.py`](app/main.py), [`app/zeroconf_publish.py`](app/zeroconf_publish.py)).

### Pi 5 vs Pi 4

- **Controller** code lives on the **Raspberry Pi 5** (`controller/` in the monorepo).
- **Edge agent** runs on each **Pi 4**. Do **not** symlink the Pi 5 `shared` tree onto the Pi 4; that path does not exist on the camera host.

The edge tree includes a **concrete copy** of the Python package at **`shared/surveillance_shared/`** (not a link). After editing **`controller/shared/surveillance_shared/`** on a machine with the full repo, run:

```bash
edge-agent/scripts/sync-shared-from-controller.sh
```

Then deploy or commit the updated files under `edge-agent/shared/`.

### Python import path

[`app/_shared_bootstrap.py`](app/_shared_bootstrap.py) adds a directory that **contains** `surveillance_shared/` to `sys.path`, in this order:

1. **`SURVEILLANCE_SHARED_PATH`** — optional explicit directory
2. **`edge-agent/shared`** — normal layout (this repo copy)
3. **`../controller/shared`** — only when a full monorepo checkout exists on the **same** host (e.g. developer laptop)
4. **`../shared`** — legacy layout

## Installation

```bash
cd /path/to/edge-agent
```

**Models** — once, if `models/` is missing the SSD files:

```bash
./scripts/fetch_ssd_models.sh
```

Writes **`MobileNetSSD_deploy.prototxt`** and **`mobilenet_iter_73000.caffemodel`** into **`models/`**.

**Python**

```bash
pip install -r requirements.txt
```

**Configuration**

Copy [`.env_example`](.env_example) to `.env` as a reference. **`uvicorn` does not load `.env` automatically** — export variables in your shell, use `set -a; source .env; set +a` if the file is valid shell syntax, or run under a process manager that injects env from `.env`.

## Execution

```bash
cd /path/to/edge-agent
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Use a port consistent with **`SURVEILLANCE_EDGE_HTTP_PORT`** (default **8080**) so mDNS advertisement matches the bound port.

### Environment variables

| Variable | Meaning |
|----------|---------|
| `SURVEILLANCE_RTSP_URL` | Input RTSP URL for OpenCV + ffmpeg. If unset, the HTTP API and mDNS can still run; the recorder thread does not start without a stream. |
| `SURVEILLANCE_MQTT_HOST` | MQTT broker (typically on **Pi 5**); default **`192.168.2.104`** if unset. |
| `SURVEILLANCE_MQTT_PORT` | Default **1883**. |
| `SURVEILLANCE_MQTT_USER` / `SURVEILLANCE_MQTT_PASSWORD` | Optional broker credentials. |
| `SURVEILLANCE_MQTT_TOPIC_PREFIX` | Default **`surveillance/cameras`**. |
| `SURVEILLANCE_EDGE_CAMERA_ID` | Segment in `surveillance/cameras/{id}/recording`; must be unique per edge. Default **`camera1`**. |
| `SURVEILLANCE_RECORDINGS_DIR` | MP4 output directory; default `<edge-agent>/data/recordings`. |
| `SURVEILLANCE_MODEL_DIR` | SSD models directory; default **`edge-agent/models/`**. |
| `SURVEILLANCE_SHARED_PATH` | Optional folder containing `surveillance_shared/`. |
| `SURVEILLANCE_EDGE_HTTP_PORT` | Port used for discovery metadata; should match uvicorn `--port`. Default **8080**. |
| `SURVEILLANCE_EDGE_DISPLAY_NAME` | mDNS / discovery label; default **`Vigilance Edge`**. |
| `SURVEILLANCE_EDGE_LOCATION` | Optional location string for discovery. |
| `SURVEILLANCE_MEDIAMTX_PATH` | MediaMTX path segment for discovery/controller; defaults to `SURVEILLANCE_EDGE_CAMERA_ID`. |
| `SURVEILLANCE_CONTROLLER_IP` | Used to pick outbound interface toward the controller; default **`192.168.2.104`**. |
| `SURVEILLANCE_EDGE_IP` | Optional forced LAN IPv4 for Zeroconf when auto-detection is wrong. |

Optional OpenCV RTSP tuning and detector overrides (`OPENCV_FFMPEG_CAPTURE_OPTIONS`, `SURVEILLANCE_OPENCV_FFMPEG_CAPTURE_OPTIONS`, `SURVEILLANCE_SSD_PROTO`, `SURVEILLANCE_SSD_WEIGHTS`, `SURVEILLANCE_SSD_CONFIDENCE`, etc.) are documented in [`.env_example`](.env_example).

### MQTT payload (JSON)

`status`: `Start` | `InProgress` | `Stop`; `recording_id`; `timestamp` (ISO UTC); `objects_detected` (strings); `local_path`; optional `filename`.
