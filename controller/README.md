# Controller (Pi 5)

Expected layout:

| Path | Purpose |
|------|---------|
| `backend/` | FastAPI app (`uvicorn app.main:app`), `requirements.txt`, `.env` |

**Backend entry:** `app/main.py` in this repo is a **minimal** API: it wires `GET /cameras` to `camera_store`, stubs recordings/events/WebSockets, and **does not** run MediaMTX/Hailo inside the API process. Repo-root **`./start.sh controller`** starts **MediaMTX** (generated config) before uvicorn when the `mediamtx` binary is available. Use the minimal `main.py` when your Pi checkout had no `main.py` and the UI always showed “No cameras yet”. If you already run a **full** controller build on the Pi, keep that `main.py` and only ensure it returns `camera_store` cameras (or merge routes).

| `frontend/` | React + Vite dashboard (`package.json`, `npm run dev`) |
| `shared/` | Optional Python package path added to `PYTHONPATH` when present |

Some clones or minimal checkouts only contain **`backend/`**. In that case the dashboard **was not removed** — it was never in that tree. Use either:

1. **Full repository** — `git pull` / re-clone so `controller/frontend/` exists, then `./start.sh controller --install` again, or  
2. **API only** — `./start.sh controller --install` (with the updated `start.sh`) skips npm when `frontend/` is missing; use **`http://<pi5>:8000/docs`** for OpenAPI.

From repo root, `./start.sh controller` does the same: **MediaMTX** (if installed and cameras have RTSP URLs), then backend, then Vite if `controller/frontend/package.json` exists.

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

   ```bash
   # If this prints nothing or "Failed to connect", nothing is listening on :8000.
   curl -sS -o /tmp/smartcam_cameras.body -w "http_code=%{http_code} bytes=%{size_download}\n" \
     http://127.0.0.1:8000/cameras
   head -c 300 /tmp/smartcam_cameras.body; echo
   ```

   `python3 -m json.tool` fails with **Expecting value: line 1 column 1** when the **body is empty** (wrong port, API not running, or TLS/proxy stripping the response). Fix the process first, then:

   **Do not paste the error message into the shell** — lines like `(char 0)` contain `(` and can trigger **`-bash: syntax error near unexpected token '('`**.

   From `controller/backend` you can run:

   `bash scripts/curl-smoke.sh http://127.0.0.1:8000`

   `curl -s http://127.0.0.1:8000/ | python3 -m json.tool`  
   should show `service` / `ok` / `cameras` from this repo’s minimal `app.main`.

   `curl -s http://127.0.0.1:8000/cameras | python3 -m json.tool`  
   should show a **JSON array** (possibly `[]`) of camera objects with at least `id` and **`url`** (RTSP). If your file only has `main_stream`, the backend should copy that to `url` when loading `camera_store` (recent versions); the UI also maps `main_stream` → `url` when loading.

4. **If the list is populated but the tile is black**  
   Live tiles use **`GET /cameras/{id}/hls/index.m3u8`** (or WebRTC) via **MediaMTX** on the controller. Ensure **MediaMTX** is installed, `ffmpeg` works, and the controller logs show paths for your camera. Check **`GET /cameras/{id}/stream_health`** in `/docs` for RTSP pull errors.

5. **`mediamtx_path` in JSON**  
   If you run multiple cameras with the same last URL segment (e.g. both use `…/stream1`), set a unique **`mediamtx_path`** per camera to match the path names in the generated MediaMTX config.

6. **`edge_base_url` with a VIGI-only setup**  
   If live video is **direct RTSP** to the VIGI (e.g. `192.168.2.42`) and you do **not** have a Pi 4 agent online, omit **`edge_base_url`** or set it to `null`. Otherwise the UI may poll `http://<edge>/health` and show warnings for an offline Pi. The UI now skips that check when the RTSP host and edge host differ.

7. **Gray live tile (broken document / blank WebRTC)**  
   - **WebRTC** uses `VITE_MEDIAMTX_BASE` + a **path** that must match `mediamtx.generated.yml` on the Pi 5. For VIGI URLs ending in `/stream1`, the UI now defaults the path to **`cam{camera_id}`** (e.g. `cam0`) unless you set **`mediamtx_path`** on the camera. If your server uses another name, set `mediamtx_path` to match.  
   - Set **`VITE_LIVE_WEBRTC=0`** in `.env` to use **HLS** (often more reliable on phones). Restart Vite.  
   - Confirm MediaMTX is up: `ss -tlnp | grep -E '8888|8889'` on the controller.
