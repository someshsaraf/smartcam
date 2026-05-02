# `surveillance_shared` on the Pi 4

The controller (Pi 5) and edge agent (Pi 4) are **different machines**. Do **not** use a symlink to the controller’s `shared` folder; it will not exist on the camera Pi.

This directory holds a **real copy** of the `surveillance_shared` Python package (checked into git under `edge-agent/shared/surveillance_shared/`).

When you change detector or RTSP helpers in **`controller/shared/surveillance_shared/`**, refresh the edge copy from a machine that has the full repo:

```bash
edge-agent/scripts/sync-shared-from-controller.sh
```

Then commit the updated `edge-agent/shared/surveillance_shared/` files (or rsync that folder to each Pi 4).
