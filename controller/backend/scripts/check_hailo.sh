#!/usr/bin/env bash
# Verify HailoRT + yolov8n.hef for SmartCam live person detection on Pi 5.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BACKEND_ROOT"

if [[ -f "$BACKEND_ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$BACKEND_ROOT/.venv/bin/activate"
fi

PY="${PYTHON:-python3}"
echo "Python: $($PY -c 'import sys; print(sys.executable)')"
echo "Version: $($PY -c 'import sys; print(sys.version.split()[0])')"

echo ""
echo "=== hailo_platform (must import in THIS venv) ==="
if $PY -c "import hailo_platform; print('OK:', hailo_platform.__file__)" 2>/dev/null; then
  HAILO_PY=ok
else
  HAILO_PY=missing
  echo "FAIL: No module named hailo_platform"
  echo ""
  echo "Install Hailo on the Pi 5, then install the Python wheel into this venv:"
  echo "  1. Pi OS: sudo apt update && sudo apt install hailo-all"
  echo "     (package is on http://archive.raspberrypi.com/debian bookworm — not plain Debian)"
  echo "     If missing: apt-cache policy hailo-all  # should list archive.raspberrypi.com"
  echo "  2. sudo reboot && hailortcli fw-control identify"
  echo "  3. Hailo Python wheel: https://hailo.ai/developer-zone/software-downloads/"
  echo "     - hailort .deb (arm64)"
  echo "     - hailo_platform-*.whl matching Python $($PY -c 'import sys; print("%d.%d" % (sys.version_info.major, sys.version_info.minor))')"
  echo "  3. pip install /path/to/hailo_platform-*.whl"
  echo ""
  echo "System check (optional):"
  if command -v hailortcli >/dev/null 2>&1; then
    hailortcli fw-control identify || true
  else
    echo "  hailortcli not on PATH — install HailoRT system packages first"
  fi
fi

echo ""
echo "=== PCIe device ==="
if command -v lspci >/dev/null 2>&1; then
  lspci | grep -i hailo || echo "(no Hailo in lspci — check AI HAT / M.2 HAT wiring)"
else
  echo "lspci not available"
fi

HEF="${SMARTCAM_HAILO_HEF_PATH:-$BACKEND_ROOT/models/yolov8n.hef}"
echo ""
echo "=== HEF model ==="
if [[ -f "$HEF" ]]; then
  echo "OK: $HEF ($(du -h "$HEF" | cut -f1))"
else
  echo "MISSING: $HEF"
  echo "Copy your compiled yolov8n.hef to controller/backend/models/"
fi

echo ""
echo "=== SmartCam API (if backend running) ==="
if curl -sf http://127.0.0.1:8000/system/live_detection >/dev/null 2>&1; then
  curl -s http://127.0.0.1:8000/system/live_detection | $PY -m json.tool 2>/dev/null || true
else
  echo "Backend not reachable on :8000 (start uvicorn to test live_detection status)"
fi

if curl -sf http://127.0.0.1:8000/system/live_detection >/dev/null 2>&1; then
  echo ""
  echo "SKIP: Backend is running on :8000 — stop uvicorn before VDevice test (avoids error 73 DEVICE_IN_USE)"
elif [[ "$HAILO_PY" == ok ]]; then
  echo ""
  echo "=== Device.scan + VDevice open (Python) ==="
  if ! $PY - <<'PY' 2>&1; then
import os
from hailo_platform import ConfigureParams, Device, HEF, HailoStreamInterface, VDevice

ids = Device.scan()
print("Device.scan():", ids)
if not ids:
    override = os.environ.get("SMARTCAM_HAILO_DEVICE_ID", "").strip()
    if override:
        ids = [override]
        print("Using SMARTCAM_HAILO_DEVICE_ID:", ids)
    else:
        raise SystemExit("No devices from Device.scan(); set SMARTCAM_HAILO_DEVICE_ID")
hef_path = os.environ.get("SMARTCAM_HAILO_HEF_PATH", "models/yolov8n.hef")
hef = HEF(hef_path)
vd = VDevice(device_ids=ids)
cfg = ConfigureParams.create_from_hef(hef, HailoStreamInterface.PCIe)
ng = vd.configure(hef, cfg)
print("VDevice.configure OK, network groups:", len(ng))
vd.release()
print("Hailo Python path OK")
PY
    echo "FAIL: Python could not open VDevice (see error above)."
    echo "Try: export SMARTCAM_HAILO_DEVICE_ID=0001:01:00.0  # use id from hailortcli identify"
    exit 1
  fi
fi

if [[ "$HAILO_PY" == ok ]] && [[ -f "$HEF" ]]; then
  echo ""
  echo "All checks passed. Restart uvicorn and refresh the UI."
  exit 0
fi
exit 1
