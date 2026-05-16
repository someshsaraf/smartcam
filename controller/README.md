# Controller (Raspberry Pi 5 + Hailo)

## Description

The Smartcam **controller** runs on the **Raspberry Pi 5** (Hailo-capable host). It provides the FastAPI backend (MQTT bridge with Mosquitto, WebSocket updates, LAN discovery, proxy to edge agents, aggregated recordings), a React dashboard, and the authoritative Python package [`shared/surveillance_shared`](shared/surveillance_shared) (MobileNet-SSD detector helpers and RTSP/OpenCV environment).

**Documentation:** [`docs/`](../docs/) — architecture, MQTT schema, and step-by-step setup ([`docs/SETUP_PI5.md`](../docs/SETUP_PI5.md), [`docs/SETUP_PI4.md`](../docs/SETUP_PI4.md)). Mosquitto bootstrap: [`scripts/install-mosquitto.sh`](scripts/install-mosquitto.sh).

Layout:

- **`backend/`** — FastAPI application (`uvicorn app.main:app`).
- **`frontend/`** — React + Vite UI.
- **`shared/`** — canonical **`surveillance_shared`** for the Pi 5 backend. Each Pi 4 edge device keeps a **real file copy** under `edge-agent/shared/surveillance_shared/` (not a symlink). After editing `controller/shared/surveillance_shared/`, refresh the edge copy with `edge-agent/scripts/sync-shared-from-controller.sh` and deploy or commit under `edge-agent/shared/`.
- **`ENVCAM/`** — legacy checkout artifacts only; **do not activate** if copied from another machine (scripts embed absolute paths and `pip` will fail with “cannot execute: required file not found”). Create a fresh venv under `backend/` instead (see below).

## Installation

**System prerequisites**

- Python 3 with `pip` (on each device run `python3 -m venv .venv` inside `controller/backend` and use that environment).
- **Node.js** and **npm** for the frontend.
- **ffmpeg** on `PATH` — required for recording pipelines (muxing, continuous copy mode) in the backend.
- **MediaMTX** — **not a Python package**; `pip` / `requirements.txt` cannot install it. On the controller host, run **`./scripts/install_mediamtx.sh`** once (downloads the official release tarball into **`backend/bin/mediamtx`**). Alternatively install from [releases](https://github.com/bluenviron/mediamtx/releases) or set **`CONTROLLER_MEDIAMTX_BIN`**. The API starts MediaMTX when it finds a binary. **`CONTROLLER_MEDIAMTX_DISABLED=1`** skips startup.

**Backend**

```bash
cd controller/backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
export PYTHONPATH="$(pwd)/../shared"
python -m pip install --upgrade pip
pip install -r requirements.txt
bash scripts/install_mediamtx.sh   # MediaMTX binary (needs curl); skip if already on PATH
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

From `controller/backend`, with the same venv activated (if you use `.venv`) and `PYTHONPATH` including `../shared`:

```bash
source .venv/bin/activate   # if using .venv from Installation
export PYTHONPATH="$(pwd)/../shared"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Edit **[`backend/.env`](backend/.env)** for your LAN IPs (loaded automatically via [`app/env_loader.py`](backend/app/env_loader.py); do not `source .env`).

Interactive OpenAPI docs are served at **`http://<host>:8000/docs`** (FastAPI default).

**MediaMTX (central live tiles — Option B)**

- On startup, the backend writes **`backend/data/mediamtx.generated.yml`** from **`backend/data/cameras.json`** (each camera’s **`url`** as an RTSP/HLS pull `source`, path name from **`mediamtx_path`**) and runs **`mediamtx <that file>`** as a child process (see [`backend/app/mediamtx_manager.py`](backend/app/mediamtx_manager.py)).
- Listeners default to **`webrtcAddress: :8889`** (browser iframe player). Override with **`CONTROLLER_MEDIAMTX_WEBRTC_ADDRESS`** if needed.
- When you **add, remove, or change saved cameras**, config is **regenerated** and MediaMTX is **restarted** (debounced ~1.2s). Selection-only changes do not alter the config file and do not restart.
- Point the UI at this instance: **`VITE_MEDIAMTX_BASE=http://<controller-lan-ip>:8889`**, or set **`VITE_API_URL`** and let the dev UI default MediaMTX to **the same host** on port **8889**. If the browser says **“refused to connect”** to that IP, check **`GET /system/mediamtx`** (e.g. `http://<pi5>:8000/system/mediamtx`) for **`process_running`**, **`binary_path`**, and **`last_start_error`**.

**Edge agents (mDNS) vs OpenCV on the controller**

- **Discovery does not run at API startup.** The UI **Detect cameras** button calls **`GET /detect/edges`** and **`GET /detect`** (each ~3s mDNS listen in [`backend/app/discovery.py`](backend/app/discovery.py)). Results only include devices not already listed in **`backend/data/cameras.json`**.
- **`GET /detect/edges`** returns payloads with `edge_base_url`, `mqtt_camera_id`, and `url`. Use **`POST /cameras`** (or **Add** in the UI) so the entry is stored with **`edge_base_url` set** for Pi 4 edges.
- Saved cameras persist in **`backend/data/cameras.json`** until removed (**DELETE `/cameras/{id}`** or **✕** in the UI). A fresh checkout uses an empty camera list until you add devices.
- If a camera has a **non-empty `edge_base_url`**, the controller **does not** start the per-camera OpenCV/motion worker in [`backend/app/recording_manager.py`](backend/app/recording_manager.py) (recording and RTSP inference stay on the edge; the controller lists recordings over HTTP to the edge and shows live video via MediaMTX in the UI).
- If `edge_base_url` is **missing** but `url` is an RTSP URL, the controller assumes a **local/direct** camera and will connect to that RTSP URL at startup for motion recording — which produces errors like `Connection refused` when nothing is listening on that host/port (for example edge RTSP on `:8554` while MediaMTX is down).

**MQTT (recording indicators and bridge)**

Set **`CONTROLLER_MQTT_HOST`** to this Pi’s **LAN IPv4** (e.g. `192.168.2.139`, not `127.0.0.1`). When the API starts, it ensures a broker listens on that address (managed `mosquitto` subprocess with config under `backend/data/`, unless something already accepts connections there). Edge agents use the same host in **`SURVEILLANCE_MQTT_HOST`**.

| Variable | Purpose |
|----------|---------|
| `CONTROLLER_MQTT_HOST` | Broker bind/connect address; empty disables broker management and the MQTT bridge. |
| `CONTROLLER_MQTT_PORT` | Default **1883**. |
| `CONTROLLER_MQTT_USER` / `CONTROLLER_MQTT_PASSWORD` | Optional; empty = anonymous on the managed broker. |
| `CONTROLLER_MQTT_TOPIC_PREFIX` | Default **`surveillance/cameras`**. |
| `CONTROLLER_MOSQUITTO_DISABLED` | Set to **`1`** if you run Mosquitto yourself and do not want the API to start it. |
| `CONTROLLER_MOSQUITTO_USE_SYSTEM` | Set to **`1`** to run **`systemctl start mosquitto`** (after [`scripts/install-mosquitto.sh`](scripts/install-mosquitto.sh)) instead of the embedded process. |

Diagnostics: **`GET /system/mosquitto`** and **`GET /system/recording`** (`mosquitto` field). Requires **`mosquitto`** on `PATH` (`apt install mosquitto`).

**Frontend (development)**

From `controller/frontend`:

```bash
npm install
npm run dev
```

Edit **[`frontend/.env`](frontend/.env)** (or **`.env.local`** for machine-specific overrides). **Restart `npm run dev`** after changes.

The dev server uses **`vite --host`**. Open the **Network** URL Vite prints (e.g. `http://<your-pi>:5173/`) from other devices on the LAN.

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Controller API, e.g. `http://<pi5>:8000` (set in `.env` for a stable setup). |
| `VITE_HLS_BASE` | MediaMTX HLS, default `http://<same-host-as-api>:8888`. |
| `VITE_MEDIAMTX_BASE` | MediaMTX WebRTC HTTP, default `http://<same-host-as-api>:8889`. |
| `VITE_WS_RECORDING_URL` / `VITE_WS_DETECTIONS_URL` | Optional; default from `VITE_API_URL`. |

If `VITE_API_URL` is unset, the UI falls back to `http://<browser-hostname>:8000`.
