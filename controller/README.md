# Controller (Pi 5)

Expected layout:

| Path | Purpose |
|------|---------|
| `backend/` | FastAPI app (`uvicorn app.main:app`), `requirements.txt`, `.env` |
| `frontend/` | React + Vite dashboard (`package.json`, `npm run dev`) |
| `shared/` | Optional Python package path added to `PYTHONPATH` when present |

Some clones or minimal checkouts only contain **`backend/`**. In that case the dashboard **was not removed** — it was never in that tree. Use either:

1. **Full repository** — `git pull` / re-clone so `controller/frontend/` exists, then `./start.sh controller --install` again, or  
2. **API only** — `./start.sh controller --install` (with the updated `start.sh`) skips npm when `frontend/` is missing; use **`http://<pi5>:8000/docs`** for OpenAPI.

From repo root, `./start.sh controller` does the same: backend starts, Vite is skipped if there is no `package.json` under `controller/frontend`.

See [`../docs/SETUP_PI5.md`](../docs/SETUP_PI5.md) for Mosquitto, MediaMTX, and Hailo.

## `cameras.json` and live video

1. **`SMARTCAM_CAMERAS_JSON` must be visible to the backend process**  
   Exporting it in an interactive shell is not enough if you start the API with **systemd**, **cron**, or another user. Prefer putting it in **`controller/backend/.env`** (same folder as uvicorn’s working directory is typical):

   ```env
   SMARTCAM_CAMERAS_JSON=/home/somesh/smartcam/controller/backend/data/cameras.json
   ```

   This repo’s `camera_store` loads that `.env` **on import** (before reading the variable), so you do not need a shell `export` if `.env` is present. Otherwise use systemd `Environment=…` or export in the script that starts `uvicorn`.

   After changing `.env` or `cameras.json`, **restart the API**. From a Python shell on the Pi you can force a re-read (if you use this `camera_store`):

   `python3 -c "from app.camera_store import reload_cameras_from_json; print(reload_cameras_from_json())"`

2. **Watch backend stdout on startup** for a line like:

   `[camera_store] (startup) registry has N camera(s). …`

   If `N` is `0`, the JSON path is wrong, the file is empty/invalid, or **your running app does not import this module** (different codebase on the Pi).

3. **Confirm the API, not only the file**

   `curl -s http://127.0.0.1:8000/cameras | python3 -m json.tool`  
   You should see a **non-empty JSON array** of objects with at least `id` and **`url`** (RTSP). If your file only has `main_stream`, the backend should copy that to `url` when loading `camera_store` (recent versions); the UI also maps `main_stream` → `url` when loading.

4. **If the list is populated but the tile is black**  
   Live tiles use **`GET /cameras/{id}/hls/index.m3u8`** (or WebRTC) via **MediaMTX** on the controller. Ensure **MediaMTX** is installed, `ffmpeg` works, and the controller logs show paths for your camera. Check **`GET /cameras/{id}/stream_health`** in `/docs` for RTSP pull errors.

5. **`mediamtx_path` in JSON**  
   If you run multiple cameras with the same last URL segment (e.g. both use `…/stream1`), set a unique **`mediamtx_path`** per camera to match the path names in the generated MediaMTX config.
