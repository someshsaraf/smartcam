# Controller (Pi 5)

Expected layout:

| Path | Purpose |
|------|---------|
| `backend/` | FastAPI app (`uvicorn app.main:app`), `requirements.txt`, `.env` |

**Backend entry:** `app/main.py` wires `GET /cameras` to `camera_store`, **manual recording** (local `ffmpeg` or **proxy** to Pi edge), **aggregated `GET /recordings`**, clip file/thumbnail routes, stubs **`/cameras/{id}/events`**, runs **OpenCV MobileNet-SSD** person detection and **`/ws/detections`**. **`POST /cameras/discover`** runs **ONVIF WS-Discovery** + **GetStreamUri** (VIGI / ONVIF LAN cameras) and **mDNS** for Pi edge agents (`app/camera_discovery.py`). **Hailo** is optional later (see [`../docs/HAILO_YOLOV8N_SMARTCAM.md`](../docs/HAILO_YOLOV8N_SMARTCAM.md)). Repo-root **`./start.sh controller`** starts **MediaMTX** before uvicorn when `mediamtx` is available.

| `frontend/` | React + Vite dashboard (`package.json`, `npm run dev`) |
| `shared/` | Optional Python package path added to `PYTHONPATH` when present |

Some clones or minimal checkouts only contain **`backend/`**. In that case the dashboard **was not removed** — it was never in that tree. Use either:

1. **Full repository** — `git pull` / re-clone so `controller/frontend/` exists, then `./start.sh controller --install` again, or  
2. **API only** — `./start.sh controller --install` (with the updated `start.sh`) skips npm when `frontend/` is missing; use **`http://<pi5>:8000/docs`** for OpenAPI.

From repo root, `./start.sh controller` does the same: **MediaMTX** (if installed and cameras have RTSP URLs), then backend, then Vite if `controller/frontend/package.json` exists. **`./start.sh controller --install`** also downloads **MobileNet-SSD** weights into `backend/models/` when missing (for person overlays).

### Person detection (live tile boxes)

1. Run **`controller/backend/scripts/fetch_ssd_models.sh`** once (or rely on **`./start.sh controller --install`** to fetch them). Prototxt + Caffe weights land in **`backend/models/`**.
2. **`GET /detector/person/status`** — model paths, `model_load_ok`, and WebSocket client count.
3. Env: **`SMARTCAM_PERSON_DETECT_ENABLED`** (default `1`), **`SMARTCAM_PERSON_DETECT_INTERVAL_MS`** (default `200`), **`SMARTCAM_PERSON_CONFIDENCE`**, **`SMARTCAM_MODEL_DIR`** / **`SMARTCAM_SSD_PROTO`** / **`SMARTCAM_SSD_WEIGHTS`** to override paths.
4. Each camera with an **`rtsp://`** or **`rtsps://`** URL in `camera_store` gets a background reader; detections are pushed on **`/ws/detections`**. Add or remove cameras and the supervisor picks up changes within a few seconds (no API restart required). Restart the API after installing model files for the first time.

### Manual recording (live tile **Rec**)

1. Set **recording mode to Off** in camera settings and **Save** — settings are written to **`cameras.json`** (see `SMARTCAM_CAMERAS_JSON` / `backend/data/cameras.json`) so **`recording_mode` survives API restarts**.
2. **Pi edge:** the edge agent must **also** have recording mode **Off** for its own recorder, or manual start returns **400** (proxy surfaces that message in the browser).
3. **VIGI / LAN RTSP camera:** the controller runs **`ffmpeg`** against the camera’s **RTSP URL** and writes **`backend/data/recordings/{camera_id}/manual_*.mp4`**. Install **`ffmpeg`** on the Pi (`apt install ffmpeg`). If **`edge_base_url`** is also set (e.g. for MQTT) but the RTSP host **differs** from the edge URL’s host, manual recording **stays on the controller** so a commercial camera is not blocked by an offline edge.
4. **Pi edge as recorder:** set **`edge_base_url`** and use an RTSP URL whose **host matches** the edge’s host (or leave RTSP empty on the row so the edge uses its own stream). Manual start/stop is **proxied** to **`/recordings/manual/*`** on the edge (clips on the Pi 4). If the edge is unreachable, the API may fall back to controller-local **ffmpeg** when the row has an **`rtsp://`** URL.
5. **`GET /recordings`** merges controller-local files and each edge’s catalog. Playback uses **`GET /recordings/{id}/files/{name}`** (local `FileResponse`, or **307** to the edge when the clip lives on the edge).

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
   The minimal API **`GET /cameras/{id}/hls/index.m3u8`** returns **307** to MediaMTX at **`http://<API-host>:8888/{path}/index.m3u8`** (path matches `mediamtx.generated.yml`). Ensure **MediaMTX** is running (`./start.sh controller`). If the browser reaches the API on a different host than MediaMTX, set **`SMARTCAM_HLS_ORIGIN`** (e.g. `http://192.168.2.139:8888`) in the backend environment. **`VITE_HLS_BASE`** on the frontend should point at the same HLS origin for the direct fallback. **`GET /cameras/{id}/stream_health`** returns **`rtsp_url_redacted`**, **`hls_api_playlist_url`**, **`hls_mediamtx_manifest_url`**, and **`mediamtx_path`**. Add **`?include_secrets=true`** to include the full **`rtsp_url`** (password in clear) — the **Debug** panel uses this on a trusted LAN; set **`SMARTCAM_DENY_STREAM_HEALTH_SECRETS=1`** on the server to ignore that query. Alternatively **`SMARTCAM_DEBUG_FULL_RTSP=1`** always adds **`rtsp_url`** for any client (still LAN-trusted only). The live tile shows this block by default (placed in the upper area so dashboard chrome does not cover it); set **`VITE_HIDE_STREAM_DEBUG=1`** to hide it when playback is healthy. The browser **Console** logs **`[SmartCam stream_health]`** on each successful fetch. Set **`SMARTCAM_LOG_STREAM_HEALTH=1`** on the controller to log the same fields in the API process. On HLS failure the overlay lists URLs and reminds you to open DevTools. Use the floating **Debug** button (bottom-right) for a full **debug panel** (client URLs, session, WebSocket status, per-camera `stream_health`).
   **MediaMTX log `401 Unauthorized` on the RTSP source:** (1) Put credentials in the camera **`url`**, e.g. **`rtsp://admin:password@192.168.2.42:554/stream1`**, with the password **percent-encoded** if it contains `@`, `#`, `:`, etc. (2) Regenerate YAML / restart so **`pathDefaults.rtspTransport: tcp`** is applied (default; TP-Link digest issues often improve with TCP — see [MediaMTX #3116](https://github.com/bluenviron/mediamtx/issues/3116)). To use MediaMTX’s default transport instead, set **`SMARTCAM_MEDIAMTX_RTSP_TRANSPORT=automatic`**. (3) If you still see 401 **with** credentials, the digest response is wrong — double-check the VIGI **verification / device** password, not an Omada-only login.

5. **`mediamtx_path` in JSON**  
   If you run multiple cameras with the same last URL segment (e.g. both use `…/stream1`), set a unique **`mediamtx_path`** per camera to match the path names in the generated MediaMTX config.

6. **`edge_base_url` with a VIGI-only setup**  
   If live video is **direct RTSP** to the VIGI (e.g. `192.168.2.42`) and you do **not** have a Pi 4 agent online, omit **`edge_base_url`** or set it to `null`. Otherwise the UI may poll `http://<edge>/health` and show warnings for an offline Pi. The UI now skips that check when the RTSP host and edge host differ.

7. **Gray live tile (broken document / blank WebRTC)**  
   - **WebRTC** uses `VITE_MEDIAMTX_BASE` + a **path** that must match `mediamtx.generated.yml` on the Pi 5. For VIGI URLs ending in `/stream1`, the UI now defaults the path to **`cam{camera_id}`** (e.g. `cam0`) unless you set **`mediamtx_path`** on the camera. If your server uses another name, set `mediamtx_path` to match.  
   - Set **`VITE_LIVE_WEBRTC=0`** in `.env` to use **HLS** (often more reliable on phones). Restart Vite.  
   - Confirm MediaMTX is up: `ss -tlnp | grep -E '8888|8889'` on the controller.
