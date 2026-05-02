# Controller (Raspberry Pi 5 + Hailo)

- **`backend/`** — FastAPI API (Mosquitto env, edge proxy, WebSocket).
- **`frontend/`** — React UI.
- **`shared/`** — authoritative **`surveillance_shared`** (detector + RTSP env) for the **Pi 5** backend. Each **Pi 4** edge device carries its **own file copy** under `edge-agent/shared/surveillance_shared/` (not a symlink); run `edge-agent/scripts/sync-shared-from-controller.sh` after changing this tree.
- **`ENVCAM/`** — optional local venv (if present).

## Run API

```bash
cd controller/backend
export PYTHONPATH="$(pwd)/../shared"
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Set `CONTROLLER_MQTT_HOST` for recording indicators on the UI.

## Run UI

From `controller/frontend`, set `VITE_API_URL` (and `VITE_MEDIAMTX_BASE` as needed).
