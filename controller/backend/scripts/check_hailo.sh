#!/usr/bin/env bash
# Hailo diagnostics for SmartCam controller (.venv import, PCIe, HEF, API).
# Non-fatal — start.sh runs this after backend startup.

set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="${BACKEND_ROOT}/.venv/bin/python3"
API_PORT="${SMARTCAM_API_PORT:-8000}"
HEF="${SMARTCAM_HAILO_HEF_PATH:-${BACKEND_ROOT}/models/yolov8n.hef}"

echo "Python: ${VENV_PY}"
if [[ -x "$VENV_PY" ]]; then
  "$VENV_PY" -V 2>&1 | sed 's/^/Version: /'
else
  echo "Version: (no .venv — run ./start.sh controller --install)"
fi

echo
echo "=== hailo_platform (must import in THIS venv) ==="
if [[ -x "$VENV_PY" ]] && "$VENV_PY" -c "import hailo_platform" 2>/dev/null; then
  echo "OK: hailo_platform imports"
else
  err="$("$VENV_PY" -c "import hailo_platform" 2>&1 || true)"
  echo "FAIL: ${err:-No module named hailo_platform}"
  echo
  echo "Install Hailo on the Pi 5, then install the Python wheel into this venv:"
  echo "  1. Pi OS: sudo apt update && sudo apt install hailo-all"
  echo "     (package is on http://archive.raspberrypi.com/debian bookworm — not plain Debian)"
  echo "     If missing: apt-cache policy hailo-all  # should list archive.raspberrypi.com"
  echo "  2. sudo reboot && hailortcli fw-control identify"
  echo "  3. Hailo Python wheel: https://hailo.ai/developer-zone/software-downloads/"
  echo "     - hailort .deb (arm64)"
  echo "     - hailo_platform-*.whl matching Python $("$VENV_PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo '?')"
  echo "  4. Copy wheel to controller/backend/wheels/ or set SMARTCAM_HAILO_WHEEL=/path/to/wheel"
  echo "  5. ./start.sh controller  (runs scripts/install_hailo_platform.sh automatically)"
fi

echo
echo "=== System check (optional) ==="
if command -v hailortcli >/dev/null 2>&1; then
  hailortcli fw-control identify 2>&1 | head -5 || true
else
  echo "hailortcli not on PATH — install HailoRT system packages first"
fi

echo
echo "=== PCIe device ==="
if command -v lspci >/dev/null 2>&1; then
  lspci 2>/dev/null | grep -i hailo || echo "(no Hailo in lspci)"
else
  echo "lspci not available"
fi

echo
echo "=== HEF model ==="
if [[ -f "$HEF" ]]; then
  echo "OK: $HEF"
else
  echo "MISSING: $HEF"
  echo "Copy your compiled yolov8n.hef to controller/backend/models/"
fi

echo
echo "=== SmartCam API (if backend running) ==="
if curl -sf --max-time 2 "http://127.0.0.1:${API_PORT}/" >/dev/null 2>&1; then
  curl -sf --max-time 2 "http://127.0.0.1:${API_PORT}/detector/person/status" 2>/dev/null \
    | python3 -m json.tool 2>/dev/null || echo "(GET /detector/person/status unavailable)"
else
  echo "Backend not reachable on :${API_PORT} (wait a few seconds after start, or check uvicorn logs)"
fi
