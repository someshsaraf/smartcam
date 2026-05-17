#!/usr/bin/env bash
# Start SmartCam controller backend (uvicorn) + frontend (Vite dev server).
# Run on the Pi 5 from any directory:
#   bash controller/scripts/start.sh
#   bash controller/scripts/start.sh --install   # first-time / after pull
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROLLER_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_ROOT="$CONTROLLER_ROOT/backend"
FRONTEND_ROOT="$CONTROLLER_ROOT/frontend"
SHARED_ROOT="$CONTROLLER_ROOT/shared"
VENV="$BACKEND_ROOT/.venv"
API_PORT="${SMARTCAM_API_PORT:-8000}"
UI_PORT="${SMARTCAM_UI_PORT:-5173}"

INSTALL=0
BACKEND_ONLY=0
FRONTEND_ONLY=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  Starts the SmartCam controller API and React dev UI. Press Ctrl+C to stop both.

Options:
  --install, -i     Create venv, pip install, npm install (and fetch mediamtx if missing)
  --backend-only    Start uvicorn only
  --frontend-only   Start Vite only
  --help, -h        Show this help

Environment:
  SMARTCAM_API_PORT   API port (default 8000)
  SMARTCAM_UI_PORT    Vite port (default 5173)

Examples:
  bash $SCRIPT_DIR/start.sh --install
  bash $SCRIPT_DIR/start.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install | -i) INSTALL=1; shift ;;
    --backend-only) BACKEND_ONLY=1; shift ;;
    --frontend-only) FRONTEND_ONLY=1; shift ;;
    --help | -h) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$BACKEND_ONLY" -eq 1 && "$FRONTEND_ONLY" -eq 1 ]]; then
  echo "Use only one of --backend-only or --frontend-only." >&2
  exit 1
fi

log() { printf '[smartcam] %s\n' "$*"; }
die() { log "ERROR: $*"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

lan_hint() {
  local ip=""
  if [[ -f "$BACKEND_ROOT/.env" ]]; then
    ip="$(grep -E '^CONTROLLER_MQTT_HOST=' "$BACKEND_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "\r' || true)"
  fi
  if [[ -z "$ip" && -f "$FRONTEND_ROOT/.env" ]]; then
    ip="$(grep -E '^VITE_API_URL=' "$FRONTEND_ROOT/.env" 2>/dev/null | head -1 | sed -E 's#.*://([^:/]+).*#\1#' || true)"
  fi
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  printf '%s' "${ip:-127.0.0.1}"
}

setup_backend() {
  need_cmd python3
  if [[ ! -d "$VENV" ]]; then
    log "Creating backend venv (--system-site-packages for apt python3-hailort)…"
    python3 -m venv "$VENV" --system-site-packages
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  export PYTHONPATH="$SHARED_ROOT"
  python -m pip install --upgrade pip -q
  pip install -r "$BACKEND_ROOT/requirements.txt" -q
  if [[ ! -x "$BACKEND_ROOT/bin/mediamtx" ]] && [[ -z "${CONTROLLER_MEDIAMTX_BIN:-}" ]]; then
    if [[ -x "$BACKEND_ROOT/scripts/install_mediamtx.sh" ]]; then
      log "MediaMTX not found — running install_mediamtx.sh…"
      bash "$BACKEND_ROOT/scripts/install_mediamtx.sh"
    fi
  fi
}

setup_frontend() {
  need_cmd npm
  cd "$FRONTEND_ROOT"
  npm install
}

if [[ "$INSTALL" -eq 1 ]]; then
  log "Install mode"
  setup_backend
  setup_frontend
  log "Install finished."
fi

PIDS=()
cleanup() {
  log "Stopping…"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

HOST="$(lan_hint)"

start_backend() {
  if [[ ! -d "$VENV" ]]; then
    die "Backend venv missing. Run: bash $SCRIPT_DIR/start.sh --install"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  export PYTHONPATH="$SHARED_ROOT"
  cd "$BACKEND_ROOT"
  log "Starting API on http://0.0.0.0:${API_PORT} (docs: http://${HOST}:${API_PORT}/docs)"
  uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" 2>&1 | sed -u 's/^/[backend] /' &
  PIDS+=("$!")
}

start_frontend() {
  need_cmd npm
  if [[ ! -d "$FRONTEND_ROOT/node_modules" ]]; then
    die "Frontend node_modules missing. Run: bash $SCRIPT_DIR/start.sh --install"
  fi
  cd "$FRONTEND_ROOT"
  log "Starting UI on http://0.0.0.0:${UI_PORT} (open http://${HOST}:${UI_PORT}/)"
  npm run dev -- --host 0.0.0.0 --port "$UI_PORT" 2>&1 | sed -u 's/^/[frontend] /' &
  PIDS+=("$!")
}

if [[ "$FRONTEND_ONLY" -eq 0 ]]; then
  start_backend
fi
if [[ "$BACKEND_ONLY" -eq 0 ]]; then
  start_frontend
fi

log "Running. Ctrl+C stops all services."
if [[ -x "$BACKEND_ROOT/scripts/check_hailo.sh" && "$FRONTEND_ONLY" -eq 0 ]]; then
  sleep 2
  bash "$BACKEND_ROOT/scripts/check_hailo.sh" || true
fi

wait -n 2>/dev/null || wait
