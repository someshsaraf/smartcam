# SmartCam camera discovery

Implemented on the **controller** (Pi 5):

| Mechanism | What it finds |
|-----------|----------------|
| **ONVIF WS-Discovery** (UDP multicast) | ONVIF cameras on the LAN (including TP-Link **VIGI** when ONVIF is enabled on the camera). |
| **ONVIF GetStreamUri** | Main profile RTSP URL (requires correct **username/password** in the request body for most devices). If discovery only advertises **port 2020**, the controller also tries common TP-Link/VIGI ONVIF ports **80, 8080, 8888, and 443 (TLS)** on the same host. |
| **mDNS** (`_vigilance-edge._tcp.local.`) | SmartCam **Pi edge** agents advertising HTTP API + optional RTSP in TXT. |

## API

### `POST /cameras/discover`

JSON body (all fields optional):

| Field | Default | Description |
|-------|---------|-------------|
| `username` | `admin` | ONVIF user. |
| `password` | `""` | ONVIF password. If empty, ONVIF devices are listed **without** resolved RTSP URLs (`incomplete: true`). |
| `timeout_seconds` | `6` | Clamped ~2–45; scales WS-Discovery, per-device ONVIF, and mDNS browse. |
| `scan_onvif` | `true` | Set `false` to skip ONVIF. |
| `scan_edges` | `true` | Set `false` to skip mDNS edge browse. |

Response:

- `edges`: Pi edge candidates (`kind: "edge"`, `edge_base_url`, optional `main_stream` from TXT).
- `onvif`: ONVIF candidates (`kind: "onvif"`, `host`, `main_stream` / `url` when password worked).
- `devices`: `edges` then `onvif` (for the dashboard “Add” list).
- `errors`: non-fatal strings if a subsystem threw.
- If `SMARTCAM_DISCOVERY` is `0` / `false` / `no`: `disabled: true` and empty lists.

**Dashboard “Find”:** After a successful discover, the UI compares each **registered** camera’s RTSP hostname to each ONVIF row’s `host`. When they match, it **PATCH**es the camera’s stored `url`: it uses the resolved `main_stream` from discovery when ONVIF succeeded, or otherwise injects the **same password** you typed in the discover dialog into the existing RTSP URL (typical when ONVIF still fails but RTSP uses the same account).

**Dependency:** `onvif-zeep` (see `controller/backend/requirements.txt`). Without it, ONVIF steps return `detail` mentioning install.

### `GET /detect/edges`

Returns **only** the mDNS edge list (same shape as each `edges[]` entry). Kept for older clients; the UI uses `POST /cameras/discover`.

## Security notes

- Discovery is intended for **trusted LANs**. Do not expose the controller API untrusted on the Internet without TLS and auth.
- Passwords are used only in memory to talk to cameras; they are **not** echoed in JSON responses.
- Set **`SMARTCAM_DISCOVERY=no`** to disable discovery on locked-down deployments.

## Future upgrades

- Full WS-Discovery **Probe** type filters and dedupe by `EndpointReference`.
- Per-profile stream selection in the UI.
- Optional subnet-only filter via env.
