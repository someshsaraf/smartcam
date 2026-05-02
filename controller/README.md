# Controller (Raspberry Pi 5 + Hailo)

## Description

The Smartcam **controller** runs on the **Raspberry Pi 5** (Hailo-capable host). It provides the FastAPI backend (MQTT bridge with Mosquitto, WebSocket updates, LAN discovery, proxy to edge agents, aggregated recordings), a React dashboard, and the authoritative Python package [`shared/surveillance_shared`](shared/surveillance_shared) (MobileNet-SSD detector helpers and RTSP/OpenCV environment).

Layout:

- **`backend/`** — FastAPI application (`uvicorn app.main:app`).
- **`frontend/`** — React + Vite UI.
- **`shared/`** — canonical **`surveillance_shared`** for the Pi 5 backend. Each Pi 4 edge device keeps a **real file copy** under `edge-agent/shared/surveillance_shared/` (not a symlink). After editing `controller/shared/surveillance_shared/`, refresh the edge copy with `edge-agent/scripts/sync-shared-from-controller.sh` and deploy or commit under `edge-agent/shared/`.
- **`ENVCAM/`** — optional local Python venv (if present on your machine).

## Installation

**System prerequisites**

- Python 3 with `pip` (use a venv on the Pi or workstation as you prefer).
- **Node.js** and **npm** for the frontend.
- **ffmpeg** on `PATH` — required for recording pipelines (muxing, continuous copy mode) in the backend.

**Backend**

```bash
cd controller/backend
export PYTHONPATH="$(pwd)/../shared"
pip install -r requirements.txt
```

**SSD models (motion detection / diagnostics on the controller)**

Run once if `backend/models/` does not yet contain the Caffe MobileNet-SSD files (defaults via `SURVEILLANCE_MODEL_DIR` in [`backend/app/detector.py`](backend/app/detector.py)):

```bash
cd controller/backend
./scripts/fetch_ssd_models.sh
```

**Frontend**

```bash
cd controller/frontend
npm install
```

## Execution

**API**

From `controller/backend`, with `PYTHONPATH` including `../shared`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Interactive OpenAPI docs are served at **`http://<host>:8000/docs`** (FastAPI default).

**MQTT (recording indicators and bridge)**

Set these when using Mosquitto on the controller network (see [`backend/app/mqtt_bridge.py`](backend/app/mqtt_bridge.py)):

| Variable | Purpose |
|----------|---------|
| `CONTROLLER_MQTT_HOST` | Broker hostname or IP (empty disables subscription bridge features that depend on it). |
| `CONTROLLER_MQTT_PORT` | Broker port; default **1883**. |
| `CONTROLLER_MQTT_USER` | Optional MQTT username. |
| `CONTROLLER_MQTT_PASSWORD` | Optional MQTT password. |
| `CONTROLLER_MQTT_TOPIC_PREFIX` | Topic prefix; default **`surveillance/cameras`**. |

**Frontend (development)**

From `controller/frontend`:

```bash
npm run dev
```

Vite environment variables (optional; see [`frontend/src/App.jsx`](frontend/src/App.jsx)):

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Backend base URL (no trailing slash); default `http://192.168.2.104:8000`. |
| `VITE_MEDIAMTX_BASE` | MediaMTX / live stream base URL; default `http://192.168.2.160:8889`. |
| `VITE_WS_RECORDING_URL` | Optional explicit WebSocket URL for recording events; if unset, derived from `VITE_API_URL` as `ws(s)://…/ws/recording`. |

Create a `.env` or `.env.local` in `controller/frontend` with `VITE_*` entries, or prefix them when invoking Vite (e.g. `VITE_API_URL=http://localhost:8000 npm run dev`).
