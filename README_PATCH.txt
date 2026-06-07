
SMARTCAM PATCH INSTRUCTIONS

1. Copy:
controller/backend/app/camera_store.py

into your existing project.

2. Why VLC works but the UI says "No cameras yet"
   VLC opens the RTSP URL you type. The dashboard lists cameras from the backend
   registry (this module + whatever your FastAPI routes expose). If the list API
   returns [], the UI shows empty even when the camera URL is valid.

   Fix (pick one):
   - Add the camera on the Devices page (if your app has that flow).
   - Ensure `app.main` imports `camera_store` before routes run so `_init_store()` runs.
   - Set env (no password in source): see section 4 below.
   - Put a non-empty `data/cameras.json` (see section 5).

3. IMPORTANT (legacy): Edit camera_store.py bootstrap_default_cameras() and replace:

password="CHANGE_ME"

with your actual TP-Link VIGI password — OR use env in section 4 instead.

4. Env-based camera (recommended for .42)

   export SMARTCAM_VIGI_IP=192.168.2.42
   export SMARTCAM_VIGI_USER=admin
   export SMARTCAM_VIGI_PASS='your_password'
   export SMARTCAM_VIGI_NAME='Front Gate'

   Then start uvicorn. This registers one camera with stream1/stream2 URLs
   (password is URL-encoded for special characters).

5. Optional JSON file

   Set SMARTCAM_CAMERAS_JSON=/path/to/cameras.json or place data/cameras.json
   under the backend cwd (or controller/backend/data/cameras.json relative to
   this file's parent). Format: a JSON array of camera objects, each with at
   least "id" and your app's expected fields (e.g. "main_stream", "name").

6. Update start.sh backend command:

uvicorn app.main:app --host 0.0.0.0 --port 5000 &

7. Install dependencies:

pip install fastapi uvicorn

8. Run:

./start.sh controller

9. Open:
http://PI_IP:5000/docs
http://PI_IP:5173

10. If the list is still empty, check:
    - In browser devtools Network: GET cameras (or your route) — 200 and body?
    - Frontend .env: API base URL must point at THIS backend (not another host).
    - Backend logs: confirm `camera_store` imported and no later clear_cameras().
