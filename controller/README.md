# Controller (Raspberry Pi 5 + Hailo)

## Description

The SmartCam **controller** runs on the **Raspberry Pi 5** (Hailo-capable host). It provides the FastAPI backend (MQTT bridge with Mosquitto, WebSocket updates, LAN discovery, proxy to edge agents, aggregated recordings), a React dashboard, and the authoritative Python package [`shared/surveillance_shared`](shared/surveillance_shared).

**Documentation:** [`docs/`](../docs/) — [`docs/SETUP_PI5.md`](../docs/SETUP_PI5.md), [`docs/SETUP_PI4.md`](../docs/SETUP_PI4.md), [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

**Layout**

| Path | Role |
|------|------|
| [`backend/`](backend/) | FastAPI app, `.env`, Hailo models, MediaMTX binary |
| [`frontend/`](frontend/) | React + Vite UI |
| [`shared/`](shared/) | Canonical `surveillance_shared` (sync to Pi 4 via [`edge-agent/scripts/sync-shared-from-controller.sh`](../edge-agent/scripts/sync-shared-from-controller.sh)) |
| [`../start.sh`](../start.sh) | **Install and run** backend + frontend from repo root |

Do not use legacy **`ENVCAM/`** venvs from other machines.

## Prerequisites (host)

Install once on the Pi 5 (not handled by `start.sh`):

- **Python 3**, **Node.js 20+**, **npm**, **ffmpeg** on `PATH`
- **Mosquitto** — [`scripts/install-mosquitto.sh`](scripts/install-mosquitto.sh) or `apt install mosquitto`
- **Hailo** — `sudo apt install hailo-all python3-hailort` (see [`docs/SETUP_PI5.md`](../docs/SETUP_PI5.md))

## Configuration

Edit before first run:

- [`backend/.env`](backend/.env) — MQTT, MediaMTX, Hailo, inference delay (loaded by [`app/env_loader.py`](backend/app/env_loader.py); do not `source .env`)
- [`frontend/.env`](frontend/.env) — `VITE_API_URL`, HLS/WebRTC bases (restart required after changes; `start.sh` handles that)

Set **`CONTROLLER_MQTT_HOST`** to this Pi’s **LAN IPv4** (not `127.0.0.1`).

## Install and run

All commands from the **repository root**:

```bash
cd /path/to/smartcam

# First time — venv, pip, npm, MediaMTX binary, Hailo check:
./start.sh controller --install

# Start API (:8000) + UI (:5173); Ctrl+C stops both:
./start.sh controller
```

| Option | Effect |
|--------|--------|
| `--install` / `-i` | Install dependencies |
| `--backend-only` | API only |
| `--frontend-only` | Vite only |

Optional: **`SMARTCAM_API_PORT`** (default `8000`), **`SMARTCAM_UI_PORT`** (default `5173`).

- Dashboard: `http://<pi5-lan-ip>:5173/`
- API docs: `http://<pi5-lan-ip>:8000/docs`

## Operations

**MediaMTX (live tiles)**

- Backend writes [`backend/data/mediamtx.generated.yml`](backend/data/mediamtx.generated.yml) from [`backend/data/cameras.json`](backend/data/cameras.json) and runs embedded MediaMTX (see [`backend/app/mediamtx_manager.py`](backend/app/mediamtx_manager.py)).
- Live tiles default to **WebRTC** (port **8889**, low latency). HLS fallback: port **8888**. Set **`VITE_MEDIAMTX_BASE`** / **`VITE_HLS_BASE`** / **`VITE_LIVE_WEBRTC`** in [`frontend/.env`](frontend/.env).
- **Person-triggered clips** are independent of the player: Hailo on controller RTSP calls the edge `POST /recordings/motion/trigger` when camera **recording mode** is **Motion** (`SMARTCAM_DETECTION_OVERLAY_DELAY_MS=0` recommended with WebRTC).
- Diagnostics: `GET /system/mediamtx`

**Edge cameras (mDNS)**

- Use the UI **Detect cameras** or `GET /detect/edges`. Add edges with **`edge_base_url`** set so recording and RTSP stay on the Pi 4; the controller proxies recordings and pulls RTSP for tiles.

**MQTT**

| Variable | Purpose |
|----------|---------|
| `CONTROLLER_MQTT_HOST` | Broker address on this Pi |
| `CONTROLLER_MQTT_PORT` | Default `1883` |
| `CONTROLLER_MQTT_TOPIC_PREFIX` | Default `surveillance/cameras` |
| `CONTROLLER_MOSQUITTO_DISABLED` | `1` = do not start embedded broker |
| `CONTROLLER_MOSQUITTO_USE_SYSTEM` | `1` = use system `mosquitto` service |

Diagnostics: `GET /system/mosquitto`, `GET /system/recording`

**Frontend env (Vite)**

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Controller API, e.g. `http://<pi5>:8000` |
| `VITE_HLS_BASE` | HLS, default same host port `8888` |
| `VITE_MEDIAMTX_BASE` | WebRTC reader, default port `8889` |
| `VITE_LIVE_WEBRTC` | `1` (default) = WebRTC tiles; `0` = HLS + synced overlays |
| `VITE_WS_RECORDING_URL` / `VITE_WS_DETECTIONS_URL` | Optional; default derived from `VITE_API_URL` |
