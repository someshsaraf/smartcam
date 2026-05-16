# Pi 4 setup (edge-agent + Camera Module 3)

This guide brings up a Raspberry Pi 4 with a Camera Module 3 from a fresh
Raspberry Pi OS install. After completing it the Pi 4 is publishing RTSP at
`:8554/{cam_id}`, recording locally to its SD card, and discoverable from the
Pi 5 controller via mDNS.

Architecture context: see [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. System prerequisites

- Raspberry Pi OS Bookworm (64-bit recommended).
- Camera Module 3 connected and enabled (`raspi-config nonint do_camera 0` on
  legacy stacks; not required on Bookworm).
- Network reachable to the Pi 5 controller. Note both LAN IPs.
- A Linux user account that is a member of the `video` group.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg curl ca-certificates
```

Sanity-check the camera before continuing:

```bash
rpicam-hello -t 2000           # window or headless smoke test
rpicam-vid --codec h264 -t 1000 -o /tmp/test.h264 && ls -l /tmp/test.h264
```

## 2. Install MediaMTX

Run the helper bundled with the edge-agent (created in T7). It picks the
matching arm64v8 / armv7 binary, verifies its SHA256 against the upstream
checksum file, and installs to `/usr/local/bin/mediamtx`.

```bash
cd /path/to/SmartCam/smartcam/edge-agent
./scripts/install-rpi-mediamtx.sh --with-libcamera
mediamtx --version
```

Flags:

- `--with-libcamera` — additionally apt-installs `libcamera0.5`,
  `libcamera-tools`, and `rpicam-apps` (with name fallbacks for older
  releases).
- `--version vX.Y.Z` — pin a specific MediaMTX release; defaults to the
  version recorded in the script.
- `--force` — overwrite an existing `mediamtx` binary.

The script does not install a systemd unit. The `LocalPublisher` inside the
edge-agent owns the MediaMTX child process.

## 3. Install the edge-agent Python environment

```bash
cd /path/to/SmartCam/smartcam/edge-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Fetch the SSD detector models (motion mode):

```bash
./scripts/fetch_ssd_models.sh
```

## 4. Configure environment

Edit **`edge-agent/.env`** directly (loaded automatically on API start). For a
Camera Module 3 edge, set at least:

```bash
SURVEILLANCE_PI_CAMERA=1
SURVEILLANCE_EDGE_CAMERA_ID=cam-frontdoor       # unique per Pi 4
SURVEILLANCE_MEDIAMTX_PATH=cam-frontdoor
SURVEILLANCE_MQTT_HOST=192.168.2.139            # Pi 5 IP
SURVEILLANCE_CONTROLLER_IP=192.168.2.139
SURVEILLANCE_EDGE_IP=<this-pi4-lan-ip>
```

Notes:

- Do not run `source .env` (quoted values are fine; unquoted spaces break bash). Use a systemd unit that
  declares `EnvironmentFile=`.
- If you skip `SURVEILLANCE_PI_CAMERA=1` (e.g. you want to use a USB camera
  with an external RTSP server), set `SURVEILLANCE_RTSP_URL` to that
  external URL and the publisher stays dormant.

## 5. Run the edge-agent

```bash
cd /path/to/SmartCam/smartcam/edge-agent
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Expected startup log:

```
[edge] mDNS _vigilance-edge._tcp.local. instance='Front-door-cam-frontdoor' ip=192.168.2.164:8080 id='cam-frontdoor'
[edge] LocalPublisher started mediamtx pid=... bind=:8554 path=cam-frontdoor
INFO:     Uvicorn running on http://0.0.0.0:8080 ...
```

## 6. Manual smoke checks

Run these from the Pi 4 unless noted otherwise.

1. MediaMTX binary is the expected version:

   ```bash
   mediamtx --version
   ```

2. The publisher is listening on RTSP:

   ```bash
   ss -tlnp | grep ':8554'
   ```

3. Edge HTTP API is up and reports the publisher state:

   ```bash
   curl -s http://127.0.0.1:8080/health | python3 -m json.tool
   # expect: rtsp_configured: true, publisher_running: true,
   # publisher_url: rtsp://127.0.0.1:8554/cam-frontdoor, mediamtx_binary set
   ```

4. From the **Pi 5**, verify the LAN-side stream:

   ```bash
   ffprobe -rtsp_transport tcp rtsp://192.168.2.164:8554/cam-frontdoor
   ```

   Expect H.264, the resolution implied by the active `quality` preset (medium
   = 1280×720), and a sane frame rate.

5. From the **Pi 5**, verify mDNS discovery sees the edge with a populated
   `rtsp` TXT field:

   ```bash
   avahi-browse -rt _vigilance-edge._tcp
   # txt should contain rtsp=rtsp://192.168.2.164:8554/cam-frontdoor
   ```

6. From the controller UI on the Pi 5, click **Detect cameras**, then **Add**
   the discovered entry. The live tile should play within a few seconds.
7. In the UI Settings dialog, switch the camera to **Continuous**. After
   one segment closes (default `SEGMENT_SECONDS=600`, lower for testing) the
   recordings list shows the new MP4. Switching back to **Motion** and
   waving in front of the camera should produce an `evt_*.mp4`.
8. Click **Delete** on a recording in the UI; verify the file disappears
   from `edge-agent/data/recordings/` on the Pi 4 SD card.

## 7. Optional: run as a systemd service

Create `/etc/systemd/system/smartcam-edge.service` (replace paths and the
user as needed). The unit relies on `LocalPublisher` to manage `mediamtx`,
so only the edge-agent process is wrapped:

```ini
[Unit]
Description=SmartCam edge-agent (Pi 4 + Camera Module 3)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=somesh
SupplementaryGroups=video
WorkingDirectory=/home/somesh/SmartCam/smartcam/edge-agent
EnvironmentFile=/home/somesh/SmartCam/smartcam/edge-agent/.env
ExecStart=/home/somesh/SmartCam/smartcam/edge-agent/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/home/somesh/SmartCam/smartcam/edge-agent/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now smartcam-edge
journalctl -u smartcam-edge -f
```

## 8. Troubleshooting

| Symptom | Probable cause | Fix |
|---------|----------------|-----|
| `publisher_running: false`, log: "no mediamtx binary on PATH" | T7 installer never ran or PATH not exported in the unit | Run `./scripts/install-rpi-mediamtx.sh`; verify `which mediamtx`; add `Environment=PATH=/usr/local/bin:/usr/bin:/bin` to the unit |
| `[rpiCamera source] failed to open camera` | User not in `video` group, or another process holds the camera | `sudo usermod -aG video $USER` and re-login; `pgrep -fa rpicam` |
| Pi 5 logs `connection refused` to `:8554` | Pi 4 mediamtx not running, or firewall on Pi 4 | `ss -tlnp \| grep :8554`; check ufw/nftables |
| Pi 4 mDNS not discovered | `avahi-daemon` disabled; LAN-IP guessed via wrong interface | `sudo systemctl enable --now avahi-daemon`; set `SURVEILLANCE_EDGE_IP` explicitly |
| Recording clips empty | `ffmpeg` missing, or RTSP URL wrong | `which ffmpeg`; `curl /health` shows the actual `publisher_url` |
| `Stop` MQTT lacks filename in continuous mode | Pre-T5 build; segment list tail not enabled | Pull latest; restart edge-agent |
