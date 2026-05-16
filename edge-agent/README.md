# Pi 4 edge agent

## Description

Runs on **each Raspberry Pi 4** connected to a camera: MobileNet-SSD motion detection, local MP4 recordings, MQTT status events to the broker on the Pi 5 controller, and an HTTP API the controller uses to list and proxy recordings. It also advertises the device on the LAN via mDNS ([`app/main.py`](app/main.py), [`app/zeroconf_publish.py`](app/zeroconf_publish.py)).

### Documentation

Full stack setup, MQTT topics, and topology: **[`docs/`](../docs/)** — Pi 4: [`docs/SETUP_PI4.md`](../docs/SETUP_PI4.md), Pi 5: [`docs/SETUP_PI5.md`](../docs/SETUP_PI5.md), overview: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

### MediaMTX and live tiles

- **USB / network RTSP camera:** set **`SURVEILLANCE_RTSP_URL`**. The recorder consumes that URL; mDNS advertises it when present.
- **Raspberry Pi Camera Module:** set **`SURVEILLANCE_PI_CAMERA=1`**, install **`mediamtx`** (see [`scripts/install-rpi-mediamtx.sh`](scripts/install-rpi-mediamtx.sh)). The edge supervises a local MediaMTX child (`source: rpiCamera`) on **`SURVEILLANCE_PUBLISHER_PORT`** (default **8554**). The controller’s MediaMTX (on the Pi 5) pulls **`rtsp://<Pi4>:8554/<path>`** for WebRTC tiles — same layout as before, with RTSP now originating on the Pi 4 when the publisher is enabled.

Point the UI’s **`VITE_MEDIAMTX_BASE`** at `http://<Pi5>:8889` (see [controller README](../controller/README.md)).

### Pi 5 vs Pi 4

- **Controller** code lives on the **Raspberry Pi 5** (`controller/` in the monorepo).
- **Edge agent** runs on each **Pi 4**. Do **not** symlink the Pi 5 `shared` tree onto the Pi 4; that path does not exist on the camera host.

The edge tree includes a **concrete copy** of the Python package at **`shared/surveillance_shared/`** (not a link). After editing **`controller/shared/surveillance_shared/`** on a machine with the full repo, run:

```bash
edge-agent/scripts/sync-shared-from-controller.sh
```

Then deploy or commit the updated files under `edge-agent/shared/`.

### Python import path

[`app/_shared_bootstrap.py`](app/_shared_bootstrap.py) adds a directory that **contains** `surveillance_shared/` to `sys.path`, in this order:

1. **`SURVEILLANCE_SHARED_PATH`** — optional explicit directory
2. **`edge-agent/shared`** — normal layout (this repo copy)
3. **`../controller/shared`** — only when a full monorepo checkout exists on the **same** host (e.g. developer laptop)
4. **`../shared`** — legacy layout

## Installation

```bash
cd /path/to/edge-agent
```

**Models** — once, if `models/` is missing the SSD files:

```bash
./scripts/fetch_ssd_models.sh
```

Writes **`MobileNetSSD_deploy.prototxt`** and **`mobilenet_iter_73000.caffemodel`** into **`models/`**.

**Python**

```bash
pip install -r requirements.txt
```

**Configuration**

Edit **[`.env`](.env)** in this directory (copy from [`.env_example`](.env_example) on first setup). Variables are loaded automatically when you start the API (see [`app/env_loader.py`](app/env_loader.py)); shell exports override `.env` values.

## Execution

```bash
cd /path/to/edge-agent
pip install -r requirements.txt
cp -n .env_example .env   # first time: edit RTSP URL, MQTT host, camera id, etc.
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Use a port consistent with **`SURVEILLANCE_EDGE_HTTP_PORT`** (default **8080**) so mDNS advertisement matches the bound port.

### Environment variables

| Variable | Meaning |
|----------|---------|
| `SURVEILLANCE_RTSP_URL` | Input RTSP URL for OpenCV + ffmpeg. If unset and **`SURVEILLANCE_PI_CAMERA=1`** with a running local MediaMTX child, the agent uses loopback RTSP automatically. |
| `SURVEILLANCE_PI_CAMERA` | Set to **`1`** to supervise local MediaMTX with **`rpiCamera`** (requires `mediamtx` binary). |
| `SURVEILLANCE_MEDIAMTX_BIN` | Optional path to the `mediamtx` executable. |
| `SURVEILLANCE_PUBLISHER_PORT` | Local RTSP bind port for the supervised MediaMTX instance (default **8554**). |
| `SURVEILLANCE_MQTT_HOST` | MQTT broker (typically on **Pi 5**); default **`192.168.2.104`** if unset. |
| `SURVEILLANCE_MQTT_PORT` | Default **1883**. |
| `SURVEILLANCE_MQTT_USER` / `SURVEILLANCE_MQTT_PASSWORD` | Optional broker credentials. |
| `SURVEILLANCE_MQTT_TOPIC_PREFIX` | Default **`surveillance/cameras`**. |
| `SURVEILLANCE_EDGE_CAMERA_ID` | Segment in `surveillance/cameras/{id}/recording`; must be unique per edge. Default **`camera1`**. |
| `SURVEILLANCE_RECORDINGS_DIR` | MP4 output directory; default `<edge-agent>/data/recordings`. |
| `SURVEILLANCE_MODEL_DIR` | SSD models directory; default **`edge-agent/models/`**. |
| `SURVEILLANCE_SHARED_PATH` | Optional folder containing `surveillance_shared/`. |
| `SURVEILLANCE_EDGE_HTTP_PORT` | Port used for discovery metadata; should match uvicorn `--port`. Default **8080**. |
| `SURVEILLANCE_EDGE_DISPLAY_NAME` | mDNS / discovery label; default **`Vigilance Edge`**. |
| `SURVEILLANCE_EDGE_LOCATION` | Optional location string for discovery. |
| `SURVEILLANCE_MEDIAMTX_PATH` | MediaMTX path segment for discovery/controller; defaults to `SURVEILLANCE_EDGE_CAMERA_ID`. |
| `SURVEILLANCE_CONTROLLER_IP` | Used to pick outbound interface toward the controller; default **`192.168.2.104`**. |
| `SURVEILLANCE_EDGE_IP` | Optional forced LAN IPv4 for Zeroconf when auto-detection is wrong. |

Optional OpenCV RTSP tuning and detector overrides (`OPENCV_FFMPEG_CAPTURE_OPTIONS`, `SURVEILLANCE_OPENCV_FFMPEG_CAPTURE_OPTIONS`, `SURVEILLANCE_SSD_PROTO`, `SURVEILLANCE_SSD_WEIGHTS`, `SURVEILLANCE_SSD_CONFIDENCE`, etc.) are documented in [`.env_example`](.env_example).

### MQTT payload (JSON)

`status`: `Start` | `InProgress` | `Stop`; `recording_id`; `timestamp` (ISO UTC); `objects_detected` (strings); `local_path`; optional `filename`.
