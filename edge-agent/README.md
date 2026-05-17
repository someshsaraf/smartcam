# Pi 4 edge agent

## Description

Runs on **each Raspberry Pi 4** with a camera: MobileNet-SSD motion detection, local MP4 recordings, MQTT events to the Pi 5 broker, HTTP API for the controller, and mDNS discovery ([`app/main.py`](app/main.py), [`app/zeroconf_publish.py`](app/zeroconf_publish.py)).

**Documentation:** [`docs/`](../docs/) — [`docs/SETUP_PI4.md`](../docs/SETUP_PI4.md), [`docs/SETUP_PI5.md`](../docs/SETUP_PI5.md), [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

**Controller UI:** started on the Pi 5 with [`../start.sh controller`](../start.sh) — see [controller README](../controller/README.md).

**Shared code:** `surveillance_shared` lives under [`shared/surveillance_shared/`](shared/surveillance_shared/). After editing [`controller/shared/`](../controller/shared/), run [`scripts/sync-shared-from-controller.sh`](scripts/sync-shared-from-controller.sh) and deploy the edge copy.

**Camera input**

- **USB / RTSP camera:** set **`SURVEILLANCE_RTSP_URL`** in [`.env`](.env).
- **Pi Camera Module:** set **`SURVEILLANCE_PI_CAMERA=1`**. `./start.sh edge --install` may run [`scripts/install-rpi-mediamtx.sh`](scripts/install-rpi-mediamtx.sh) for a local RTSP publisher on port **8554** (`rpiCamera`).

## Prerequisites (host)

- **Python 3** on the Pi 4
- **ffmpeg** on `PATH` (recording)
- Full **smartcam** repo checkout (script lives at repo root)

## Configuration

Edit **[`.env`](.env)** (see [`.env_example`](.env_example)) — LAN IPs, `SURVEILLANCE_EDGE_CAMERA_ID`, MQTT broker on the Pi 5, optional RTSP URL. Loaded on API start by [`app/env_loader.py`](app/env_loader.py); do not `source .env`.

## Install and run

All commands from the **repository root**:

```bash
cd /path/to/smartcam

# First time — venv, pip, SSD models; optional edge MediaMTX for Pi camera:
./start.sh edge --install

# Start edge API (default :8080); Ctrl+C to stop:
./start.sh edge
```

Aliases: **`edge-agent`**, **`pi4`**.

Optional: **`SMARTCAM_EDGE_PORT`** (default `8080`; script also reads **`SURVEILLANCE_EDGE_HTTP_PORT`** from `.env`).

- Health: `http://<pi4-lan-ip>:8080/health`

## Environment variables

| Variable | Meaning |
|----------|---------|
| `SURVEILLANCE_RTSP_URL` | Input RTSP for recorder; empty + `SURVEILLANCE_PI_CAMERA=1` uses loopback publisher |
| `SURVEILLANCE_PI_CAMERA` | `1` = supervise local MediaMTX + `rpiCamera` |
| `SURVEILLANCE_MEDIAMTX_BIN` | Optional `mediamtx` path |
| `SURVEILLANCE_PUBLISHER_PORT` | Local RTSP port (default `8554`) |
| `SURVEILLANCE_MQTT_HOST` | Pi 5 broker address |
| `SURVEILLANCE_MQTT_PORT` | Default `1883` |
| `SURVEILLANCE_MQTT_USER` / `SURVEILLANCE_MQTT_PASSWORD` | Optional |
| `SURVEILLANCE_MQTT_TOPIC_PREFIX` | Default `surveillance/cameras` |
| `SURVEILLANCE_EDGE_CAMERA_ID` | MQTT segment; unique per edge (default `camera1`) |
| `SURVEILLANCE_RECORDINGS_DIR` | MP4 directory (default `data/recordings`) |
| `SURVEILLANCE_MODEL_DIR` | SSD models (default `models/`) |
| `SURVEILLANCE_SHARED_PATH` | Optional `surveillance_shared` parent dir |
| `SURVEILLANCE_EDGE_HTTP_PORT` | HTTP / mDNS port (default `8080`) |
| `SURVEILLANCE_EDGE_DISPLAY_NAME` | mDNS label |
| `SURVEILLANCE_EDGE_LOCATION` | Optional discovery location |
| `SURVEILLANCE_MEDIAMTX_PATH` | Path for controller RTSP pull (defaults to camera id) |
| `SURVEILLANCE_CONTROLLER_IP` | Outbound interface hint toward Pi 5 |
| `SURVEILLANCE_EDGE_IP` | Optional fixed LAN IPv4 for mDNS |

## MQTT payload (JSON)

`status`: `Start` | `InProgress` | `Stop`; `recording_id`; `timestamp` (ISO UTC); `objects_detected`; `local_path`; optional `filename`.
