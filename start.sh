#!/usr/bin/env bash
# SmartCam — start controller (Pi 5) or edge-agent (Pi 4).
#
#   ./start.sh controller [--install] [--backend-only | --frontend-only]
#   ./start.sh edge [--install]
#
# Press Ctrl+C to stop all processes started by this script.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROLLER_ROOT="$REPO_ROOT/controller"
BACKEND_ROOT="$CONTROLLER_ROOT/backend"
FRONTEND_ROOT="$CONTROLLER_ROOT/frontend"
CONTROLLER_SHARED="$CONTROLLER_ROOT/shared"
EDGE_ROOT="$REPO_ROOT/edge-agent"
EDGE_SHARED="$EDGE_ROOT/shared"

TARGET=""
INSTALL=0
BACKEND_ONLY=0
FRONTEND_ONLY=0

CONTROLLER_API_PORT="${SMARTCAM_API_PORT:-8000}"
CONTROLLER_UI_PORT="${SMARTCAM_UI_PORT:-5173}"
EDGE_HTTP_PORT="${SMARTCAM_EDGE_PORT:-8080}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <target> [options]

Targets:
  controller    Pi 5 — FastAPI backend + React dev UI (Vite)
  edge          Pi 4 — edge-agent FastAPI (motion record, MQTT, mDNS)

Options:
  --install, -i       Install dependencies before start
  --backend-only      Controller: uvicorn only
  --frontend-only     Controller: Vite only (requires controller/frontend/)
  --help, -h          Show this help

If controller/frontend/ is missing (partial clone), install/start skips the UI
and runs the API only unless you pass --frontend-only (which errors).

Environment:
  SMARTCAM_API_PORT     Controller API (default 8000)
  SMARTCAM_UI_PORT      Controller UI  (default 5173)
  SMARTCAM_EDGE_PORT    Edge agent HTTP (default 8080)
  CONTROLLER_MEDIAMTX_BIN  Optional path to mediamtx binary (else PATH or backend/bin/mediamtx)

Examples:
  ./start.sh controller --install
  ./start.sh controller
  ./start.sh edge --install
  ./start.sh edge
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    controller | ctrl | pi5) TARGET="controller"; shift ;;
    edge | edge-agent | pi4) TARGET="edge"; shift ;;
    --install | -i) INSTALL=1; shift ;;
    --backend-only) BACKEND_ONLY=1; shift ;;
    --frontend-only) FRONTEND_ONLY=1; shift ;;
    --help | -h) usage; exit 0 ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  usage >&2
  exit 1
fi

if [[ "$TARGET" == "controller" && "$BACKEND_ONLY" -eq 1 && "$FRONTEND_ONLY" -eq 1 ]]; then
  echo "Use only one of --backend-only or --frontend-only." >&2
  exit 1
fi

if [[ "$TARGET" == "edge" && ( "$BACKEND_ONLY" -eq 1 || "$FRONTEND_ONLY" -eq 1 ) ]]; then
  echo "--backend-only and --frontend-only apply only to controller." >&2
  exit 1
fi

log() { printf '[smartcam] %s\n' "$*"; }
die() { log "ERROR: $*"; exit 1; }

controller_has_frontend() {
  [[ -f "$FRONTEND_ROOT/package.json" ]]
}

controller_pythonpath_export() {
  # Always include BACKEND_ROOT so `import app.main` works even when shared/ exists.
  if [[ -d "$CONTROLLER_SHARED" ]]; then
    export PYTHONPATH="$CONTROLLER_SHARED:$BACKEND_ROOT"
  else
    export PYTHONPATH="$BACKEND_ROOT"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

sed_prefix() {
  if sed --version 2>/dev/null | grep -q GNU; then
    sed -u 's/^/['"$1"'] /'
  else
    sed 's/^/['"$1"'] /'
  fi
}

lan_hint_from_env() {
  local ip="" f
  for f in \
    "$BACKEND_ROOT/.env:CONTROLLER_MQTT_HOST" \
    "$FRONTEND_ROOT/.env:VITE_API_URL" \
    "$EDGE_ROOT/.env:SURVEILLANCE_EDGE_IP"; do
    local path="${f%%:*}" key="${f##*:}"
    [[ -f "$path" ]] || continue
    if [[ "$key" == "VITE_API_URL" ]]; then
      ip="$(grep -E "^${key}=" "$path" 2>/dev/null | head -1 | sed -E 's#.*://([^:/]+).*#\1#' | tr -d ' "\r' || true)"
    else
      ip="$(grep -E "^${key}=" "$path" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "\r' || true)"
    fi
    [[ -n "$ip" ]] && break
  done
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  printf '%s' "${ip:-127.0.0.1}"
}

PIDS=()
cleanup() {
  log "Stopping…"
  local i
  # Stop frontend/API before MediaMTX (reverse of startup order).
  for (( i = ${#PIDS[@]} - 1; i >= 0; i-- )); do
    kill "${PIDS[i]}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- Controller (Pi 5) ---
controller_setup() {
  need_cmd python3
  [[ -f "$BACKEND_ROOT/requirements.txt" ]] || die "Missing $BACKEND_ROOT/requirements.txt — git pull or copy from the SmartCam repo."
  local venv="$BACKEND_ROOT/.venv"
  if [[ ! -d "$venv" ]]; then
    log "Creating controller venv (--system-site-packages for apt python3-hailort)…"
    python3 -m venv "$venv" --system-site-packages
  fi
  # shellcheck disable=SC1091
  source "$venv/bin/activate"
  controller_pythonpath_export
  python -m pip install --upgrade pip -q
  pip install -r "$BACKEND_ROOT/requirements.txt" -q
  if [[ ! -x "$BACKEND_ROOT/bin/mediamtx" ]]; then
    if [[ -x "$BACKEND_ROOT/scripts/install_mediamtx.sh" ]]; then
      log "Installing controller MediaMTX…"
      bash "$BACKEND_ROOT/scripts/install_mediamtx.sh"
    fi
  fi
}

controller_setup_frontend() {
  if ! controller_has_frontend; then
    if [[ "$FRONTEND_ONLY" -eq 1 ]]; then
      die "Missing $FRONTEND_ROOT (need package.json) for --frontend-only."
    fi
    log "Skipping frontend install: no $FRONTEND_ROOT — use a full SmartCam tree or API-only: ./start.sh controller --backend-only"
    return 0
  fi
  need_cmd npm
  (cd "$FRONTEND_ROOT" && npm install)
}

controller_start_mediamtx() {
  [[ "$FRONTEND_ONLY" -eq 1 ]] && return 0
  local venv="$BACKEND_ROOT/.venv"
  if [[ ! -d "$venv" ]]; then
    log "WARN: no venv — skip MediaMTX (run ./start.sh controller --install)."
    return 0
  fi
  # shellcheck disable=SC1091
  source "$venv/bin/activate"
  controller_pythonpath_export
  cd "$BACKEND_ROOT"
  if ! python "$BACKEND_ROOT/scripts/generate_controller_mediamtx_yaml.py" 2>&1 | sed_prefix yaml; then
    log "WARN: MediaMTX YAML generation failed — skip starting mediamtx."
    return 0
  fi
  local cfg="$BACKEND_ROOT/data/mediamtx.generated.yml"
  if [[ ! -f "$cfg" ]]; then
    log "WARN: $cfg missing (no RTSP cameras?) — skip MediaMTX."
    return 0
  fi
  local bin="${CONTROLLER_MEDIAMTX_BIN:-}"
  if [[ -z "$bin" ]]; then
    if command -v mediamtx >/dev/null 2>&1; then
      bin="$(command -v mediamtx)"
    elif [[ -x "$BACKEND_ROOT/bin/mediamtx" ]]; then
      bin="$BACKEND_ROOT/bin/mediamtx"
    elif [[ -x /usr/local/bin/mediamtx ]]; then
      bin="/usr/local/bin/mediamtx"
    elif [[ -x /usr/bin/mediamtx ]]; then
      bin="/usr/bin/mediamtx"
    fi
  fi
  if [[ -z "$bin" ]]; then
    log "WARN: mediamtx not found — run ./start.sh controller --install (downloads to controller/backend/bin/) or install to PATH /usr/local/bin (docs/SETUP_PI5.md). HLS/WebRTC will not work."
    return 0
  fi
  log "Starting MediaMTX ($bin) with $cfg …"
  "$bin" "$cfg" 2>&1 | sed_prefix mediamtx &
  PIDS+=("$!")
}

controller_start_backend() {
  local venv="$BACKEND_ROOT/.venv"
  [[ -d "$venv" ]] || die "Run: ./start.sh controller --install"
  # shellcheck disable=SC1091
  source "$venv/bin/activate"
  controller_pythonpath_export
  cd "$BACKEND_ROOT"
  if ! python -c "import app.main" 2>&1; then
    die "Cannot import app.main from $BACKEND_ROOT — sync backend/app/main.py, run ./start.sh controller --install, then: cd $BACKEND_ROOT && source .venv/bin/activate && python -c 'import app.main'"
  fi
  local host
  host="$(lan_hint_from_env)"
  log "Controller API: http://0.0.0.0:${CONTROLLER_API_PORT} (docs: http://${host}:${CONTROLLER_API_PORT}/docs)"
  uvicorn app.main:app --app-dir "$BACKEND_ROOT" --host 0.0.0.0 --port "$CONTROLLER_API_PORT" 2>&1 | sed_prefix backend &
  PIDS+=("$!")
}

controller_start_frontend() {
  if ! controller_has_frontend; then
    if [[ "$FRONTEND_ONLY" -eq 1 ]]; then
      die "Missing $FRONTEND_ROOT for --frontend-only."
    fi
    local host
    host="$(lan_hint_from_env)"
    log "Skipping Vite: no controller/frontend — open http://${host}:${CONTROLLER_API_PORT}/docs on the LAN."
    return 0
  fi
  [[ -d "$FRONTEND_ROOT/node_modules" ]] || die "Run: ./start.sh controller --install"
  cd "$FRONTEND_ROOT"
  # Drop stale Vite pre-bundles so UI picks up App.jsx changes after git pull.
  rm -rf node_modules/.vite 2>/dev/null || true
  local host
  host="$(lan_hint_from_env)"
  log "Controller UI: http://0.0.0.0:${CONTROLLER_UI_PORT} (open http://${host}:${CONTROLLER_UI_PORT}/)"
  npm run dev -- --host 0.0.0.0 --port "$CONTROLLER_UI_PORT" 2>&1 | sed_prefix frontend &
  PIDS+=("$!")
}

run_controller() {
  [[ -d "$CONTROLLER_ROOT" ]] || die "Missing $CONTROLLER_ROOT"
  if [[ "$INSTALL" -eq 1 ]]; then
    log "Installing controller…"
    controller_setup
    controller_setup_frontend
    log "Controller install done."
  fi
  if [[ "$FRONTEND_ONLY" -eq 0 ]]; then
    controller_start_mediamtx
    controller_start_backend
  fi
  [[ "$BACKEND_ONLY" -eq 0 ]] && controller_start_frontend
  log "Controller running. Ctrl+C to stop."
  if [[ "$FRONTEND_ONLY" -eq 0 && -x "$BACKEND_ROOT/scripts/check_hailo.sh" ]]; then
    sleep 2
    bash "$BACKEND_ROOT/scripts/check_hailo.sh" || true
  fi
}

# --- Edge agent (Pi 4) ---
edge_setup() {
  need_cmd python3
  local venv="$EDGE_ROOT/.venv"
  if [[ ! -d "$venv" ]]; then
    log "Creating edge-agent venv…"
    python3 -m venv "$venv"
  fi
  # shellcheck disable=SC1091
  source "$venv/bin/activate"
  python -m pip install --upgrade pip -q
  pip install -r "$EDGE_ROOT/requirements.txt" -q
  if [[ ! -f "$EDGE_ROOT/models/mobilenet_iter_73000.caffemodel" ]]; then
    if [[ -x "$EDGE_ROOT/scripts/fetch_ssd_models.sh" ]]; then
      log "Fetching SSD models for edge-agent…"
      bash "$EDGE_ROOT/scripts/fetch_ssd_models.sh"
    fi
  fi
  if grep -qE '^SURVEILLANCE_PI_CAMERA=1' "$EDGE_ROOT/.env" 2>/dev/null; then
    if ! command -v mediamtx >/dev/null 2>&1 && [[ -x "$EDGE_ROOT/scripts/install-rpi-mediamtx.sh" ]]; then
      log "Pi camera enabled — installing edge MediaMTX if needed…"
      bash "$EDGE_ROOT/scripts/install-rpi-mediamtx.sh" || log "WARN: edge MediaMTX install failed (see script output)"
    fi
  fi
}

edge_start() {
  local venv="$EDGE_ROOT/.venv"
  [[ -d "$venv" ]] || die "Run: ./start.sh edge --install"
  # shellcheck disable=SC1091
  source "$venv/bin/activate"
  cd "$EDGE_ROOT"
  if [[ -f "$EDGE_ROOT/.env" ]]; then
    local p
    p="$(grep -E '^SURVEILLANCE_EDGE_HTTP_PORT=' "$EDGE_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "\r' || true)"
    [[ -n "$p" ]] && EDGE_HTTP_PORT="$p"
  fi
  local host
  host="$(lan_hint_from_env)"
  log "Edge agent: http://0.0.0.0:${EDGE_HTTP_PORT} (health: http://${host}:${EDGE_HTTP_PORT}/health)"
  uvicorn app.main:app --host 0.0.0.0 --port "$EDGE_HTTP_PORT" 2>&1 | sed_prefix edge &
  PIDS+=("$!")
}

run_edge() {
  [[ -d "$EDGE_ROOT" ]] || die "Missing $EDGE_ROOT"
  if [[ "$INSTALL" -eq 1 ]]; then
    log "Installing edge-agent…"
    edge_setup
    log "Edge install done."
  fi
  edge_start
  log "Edge agent running. Ctrl+C to stop."
}

case "$TARGET" in
  controller) run_controller ;;
  edge) run_edge ;;
esac

wait
