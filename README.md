# SmartCam

LAN surveillance stack: **Pi 4 edge agents** (record, detect motion, optional local RTSP from the Pi camera) and a **Pi 5 controller** (dashboard, MQTT bridge, central MediaMTX for live tiles).

| Path | Role |
|------|------|
| [`edge-agent/`](edge-agent/) | FastAPI on each camera Pi |
| [`controller/`](controller/) | FastAPI backend + React UI on the hub Pi |
| [`docs/`](docs/) | Architecture and full setup ([`docs/SETUP_PI4.md`](docs/SETUP_PI4.md), [`docs/SETUP_PI5.md`](docs/SETUP_PI5.md)) |

First-time install (venv, models, Mosquitto, MediaMTX) is in those guides and in [`edge-agent/README.md`](edge-agent/README.md) and [`controller/README.md`](controller/README.md). Below is the minimal **how to start** each service after dependencies are installed.

---

## Start the controller (Raspberry Pi 5)

**1. Backend API** — from `controller/backend`, with Python 3 venv activated:

```bash
cd controller/backend
source .venv/bin/activate
export PYTHONPATH="$(pwd)/../shared"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Set **`CONTROLLER_MQTT_*`** if you use Mosquitto (see [`controller/README.md`](controller/README.md)). The API starts **MediaMTX** for WebRTC tiles when the `mediamtx` binary is on `PATH` (or **`CONTROLLER_MEDIAMTX_BIN`**).

**2. Frontend** — in another terminal:

```bash
cd controller/frontend
npm run dev
```

Open the URL Vite prints (e.g. `http://<pi5-ip>:5173`). Edit [`controller/frontend/.env`](controller/frontend/.env) if your Pi 5 LAN IP is not `192.168.2.139`.

---

## Start the edge agent (Raspberry Pi 4)

From `edge-agent`, with venv activated (**.env** loads automatically via `env_loader`):

```bash
cd edge-agent
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Use port **8080** unless you changed **`SURVEILLANCE_EDGE_HTTP_PORT`** (it must match what mDNS advertises).

**Pi Camera + local RTSP:** set **`SURVEILLANCE_PI_CAMERA=1`**, install **`mediamtx`** on the Pi 4, and ensure **`SURVEILLANCE_MQTT_HOST`** reaches the broker on the Pi 5. Details: [`edge-agent/README.md`](edge-agent/README.md).

---

## Suggested order

1. Mosquitto and controller backend (so MQTT is up before edges connect).
2. Controller frontend (optional for API-only use).
3. Each edge agent.

Quick checks: **`http://<pi5>:8000/docs`**, **`http://<pi4>:8080/health`**.
