# Pi 5 setup (controller + UI)

This guide brings up a Raspberry Pi 5 to act as the SmartCam controller:
Mosquitto MQTT broker, FastAPI backend (with WebSocket and HTTP REST), the
React UI, and a single MediaMTX instance pulling each camera's RTSP stream
for the live tiles.

Architecture context: see [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. System prerequisites

- Raspberry Pi OS Bookworm (64-bit recommended).
- A Linux user account with `sudo`.
- Network reachable to every Pi 4 edge.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg curl ca-certificates nodejs npm
node --version && npm --version
```

(If your distro's `nodejs` is too old for Vite, install
[Node 20+](https://nodejs.org/) via your preferred method.)

## 2. Install Mosquitto

Run the helper bundled with the controller. It is idempotent and binds the
broker to the controller's LAN IP rather than `0.0.0.0`.

```bash
cd /path/to/SmartCam/smartcam/controller
sudo ./scripts/install-mosquitto.sh 192.168.2.104 smartcam 's3cret-on-LAN'
```

Argument order:

```
sudo ./scripts/install-mosquitto.sh [--anon] [--force] <lan-ipv4> [<mqtt_user> <mqtt_password>]
```

Flags:

- `--anon` — allow anonymous (use only on a strictly trusted LAN).
- `--force` — overwrite the existing `smartcam.conf` and password file.

Without `--anon`, both `mqtt_user` and `mqtt_password` are mandatory; the
script writes a `mosquitto_passwd` file at `/etc/mosquitto/smartcam.passwd`
and disables anonymous access.

Verify:

```bash
ss -tlnp | grep ':1883'
mosquitto_sub -h 192.168.2.104 -u smartcam -P 's3cret-on-LAN' -t 'surveillance/cameras/+/recording' -v
```

The same credentials must be set on each Pi 4's `.env`
(`SURVEILLANCE_MQTT_USER`, `SURVEILLANCE_MQTT_PASSWORD`) and on the
controller backend env (`CONTROLLER_MQTT_USER`, `CONTROLLER_MQTT_PASSWORD`).

## 3. Install MediaMTX

MediaMTX is the WebRTC reader that the React UI iframes for live tiles. The
controller starts it automatically as long as the binary is on `PATH`.

```bash
# Use the same upstream tarball you used on the Pi 4.
cd /tmp
VER=v1.18.1
curl -L -O "https://github.com/bluenviron/mediamtx/releases/download/${VER}/mediamtx_${VER}_linux_arm64.tar.gz"
tar -xzf "mediamtx_${VER}_linux_arm64.tar.gz" mediamtx
sudo install -m 0755 mediamtx /usr/local/bin/mediamtx
mediamtx --version
```

Or set `CONTROLLER_MEDIAMTX_BIN=/path/to/mediamtx` if you keep it elsewhere.

## 4. Install backend Python environment

```bash
cd /path/to/SmartCam/smartcam/controller/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

(The `controller/ENVCAM/` tree is a legacy artefact; do not activate it.)

Fetch the SSD detector models for the controller's diagnostics path:

```bash
./scripts/fetch_ssd_models.sh
```

## 5. Configure backend environment

Set in the shell that launches uvicorn (or via a systemd unit's
`EnvironmentFile=`):

```bash
export PYTHONPATH="$(pwd)/../shared"
export CONTROLLER_MQTT_HOST=192.168.2.104
export CONTROLLER_MQTT_PORT=1883
export CONTROLLER_MQTT_USER=smartcam
export CONTROLLER_MQTT_PASSWORD='s3cret-on-LAN'
export CONTROLLER_MQTT_TOPIC_PREFIX=surveillance/cameras
# Optional, defaults work:
# export CONTROLLER_MEDIAMTX_BIN=/usr/local/bin/mediamtx
# export CONTROLLER_MEDIAMTX_WEBRTC_ADDRESS=:8889
```

## 6. Run the backend

```bash
cd /path/to/SmartCam/smartcam/controller/backend
source .venv/bin/activate
export PYTHONPATH="$(pwd)/../shared"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expected log fragments:

```
INFO:     Application startup complete.
mediamtx started pid=... config=mediamtx.generated.yml paths=N
INFO:     Uvicorn running on http://0.0.0.0:8000 ...
```

The OpenAPI docs are at `http://<pi5>:8000/docs`.

## 7. Run the frontend

```bash
cd /path/to/SmartCam/smartcam/controller/frontend
npm install
npm run dev    # vite --host, binds all interfaces
```

Optional `.env.local`:

```env
VITE_API_URL=http://192.168.2.104:8000
VITE_MEDIAMTX_BASE=http://192.168.2.104:8889
# VITE_WS_RECORDING_URL=ws://192.168.2.104:8000/ws/recording
```

Open the **Network** URL Vite prints (e.g. `http://192.168.2.104:5173/`) on
any device on the same LAN.

## 8. Manual smoke checks

1. Mosquitto broker is alive and authenticates:

   ```bash
   mosquitto_pub -h 192.168.2.104 -u smartcam -P 's3cret-on-LAN' \
       -t 'surveillance/cameras/test/recording' -m '{"status":"Stop"}'
   ```

2. MediaMTX is listening:

   ```bash
   ss -tlnp | grep ':8889'
   ```

3. Backend health and MQTT bridge:

   ```bash
   curl -s http://127.0.0.1:8000/system/recording | python3 -m json.tool
   # mqtt_bridge: true, mqtt_host_configured: true, ffmpeg_available: true
   ```

4. Detect a Pi 4 edge from the UI: click **Detect cameras**. The discovered
   row should display its mDNS-advertised RTSP URL (LAN form) — never a
   placeholder.
5. Click **Add** on the discovered entry. The tile starts playing within a
   few seconds.
6. Open Settings, switch to **Continuous**, save. Watch
   `mosquitto_sub -h 192.168.2.104 -t 'surveillance/cameras/+/recording' -v`
   on the Pi 5: every closed segment emits an `InProgress` with `filename`,
   and a final `Stop` carries the last filename.
7. Switch back to **Motion**, trigger motion on the camera, wait for the
   post-roll. The recordings panel shows the new clip.
8. **Delete** the clip from the UI; confirm the file is gone from the Pi 4
   SD card (`ls edge-agent/data/recordings/` on the Pi 4).

## 9. Optional: run as systemd services

Two units, one for backend and one for frontend (in production you would
typically build the frontend with `npm run build` and serve it behind nginx
instead of `vite --host`).

`/etc/systemd/system/smartcam-controller.service`:

```ini
[Unit]
Description=SmartCam controller (FastAPI + MediaMTX child)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=raspberry
WorkingDirectory=/home/raspberry/SmartCam/smartcam/controller/backend
EnvironmentFile=/home/raspberry/SmartCam/smartcam/controller/backend/.env
Environment=PYTHONPATH=/home/raspberry/SmartCam/smartcam/controller/shared
ExecStart=/home/raspberry/SmartCam/smartcam/controller/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/home/raspberry/SmartCam/smartcam/controller/backend/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now smartcam-controller
journalctl -u smartcam-controller -f
```

## 10. Troubleshooting

| Symptom | Probable cause | Fix |
|---------|----------------|-----|
| Browser tile: `192.168.2.104 refused to connect` | MediaMTX not running on the controller | `which mediamtx`; `journalctl -u smartcam-controller`; install via Step 3 |
| `dial tcp 192.168.2.x:8554: connection refused` repeating in backend log | The Pi 4's `mediamtx` is not running, or the saved camera URL is the old placeholder | Bring the Pi 4 publisher up; re-detect and re-add the camera so the URL becomes the LAN form |
| `system_recording.mqtt_bridge: false` | Mosquitto unreachable, wrong creds, or `CONTROLLER_MQTT_HOST` unset | Check `journalctl -u mosquitto`; verify env; restart backend |
| Discovery shows `incomplete: true` | Edge advertised an empty `rtsp` TXT (publisher off and no `SURVEILLANCE_RTSP_URL` set) | Set `SURVEILLANCE_PI_CAMERA=1` on the edge or fill in `SURVEILLANCE_RTSP_URL` |
| `ERR: json: unknown field "..."` from MediaMTX | Older binary rejecting newer YAML keys | Either update MediaMTX, or check that the controller is on the latest commit (we already strip the version-fragile keys) |
| Vite cannot bind on `:5173` | Port already in use | `sudo fuser -k 5173/tcp`; or change `--port` |
