#!/usr/bin/env bash
# Best-effort hailo_platform install into controller/backend/.venv (not on PyPI).
# Called from ./start.sh controller when the module is missing.
#
# 1) pip install SMARTCAM_HAILO_WHEEL or wheels/hailo_platform-*.whl (match Python version)
# 2) optional apt install hailo-all when a Hailo PCIe device is present (--system-site-packages venv)
#
# Set SMARTCAM_INSTALL_HAILO=0 to skip. Set SMARTCAM_AUTO_APT=0 to skip apt attempts.

set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="${BACKEND_ROOT}/.venv/bin/python3"
WHEELS_DIR="${BACKEND_ROOT}/wheels"

log() { printf '[hailo-install] %s\n' "$*"; }
warn() { log "WARN: $*"; }

auto_apt_enabled() {
  local v="${SMARTCAM_AUTO_APT:-1}"
  case "$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')" in
    0 | false | no | off) return 1 ;;
    *) return 0 ;;
  esac
}

hailo_install_enabled() {
  local v="${SMARTCAM_INSTALL_HAILO:-1}"
  case "$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')" in
    0 | false | no | off) return 1 ;;
    *) return 0 ;;
  esac
}

try_apt_install() {
  local pkgs=("$@")
  if ! auto_apt_enabled; then
    return 1
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    return 1
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    apt-get update -qq
    apt-get install -y "${pkgs[@]}"
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      sudo apt-get update -qq
      sudo apt-get install -y "${pkgs[@]}"
      return 0
    fi
    warn "need passwordless sudo (or run as root) to: sudo apt-get install -y ${pkgs[*]}"
  fi
  return 1
}

hailo_pcie_present() {
  if command -v lspci >/dev/null 2>&1; then
    lspci 2>/dev/null | grep -qi hailo && return 0
  fi
  return 1
}

import_ok() {
  [[ -x "$VENV_PY" ]] || return 1
  "$VENV_PY" -c "import hailo_platform" 2>/dev/null
}

python_tag() {
  "$VENV_PY" -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")'
}

pick_wheel() {
  local explicit="${SMARTCAM_HAILO_WHEEL:-}"
  if [[ -n "$explicit" && -f "$explicit" ]]; then
    printf '%s' "$explicit"
    return 0
  fi
  local py_tag
  py_tag="$(python_tag)"
  local candidates=()
  local d f
  for d in "$WHEELS_DIR" "$BACKEND_ROOT/models" "$BACKEND_ROOT"; do
    [[ -d "$d" ]] || continue
    shopt -s nullglob
    for f in "$d"/hailo_platform-*.whl; do
      candidates+=("$f")
    done
    shopt -u nullglob
  done
  if [[ ${#candidates[@]} -eq 0 ]]; then
    return 1
  fi
  local best="" score=-1
  for f in "${candidates[@]}"; do
    local s=0
    [[ "$f" == *"cp${py_tag}"* ]] && s=10
    [[ "$f" == *"aarch64"* ]] && s=$((s + 5))
    [[ "$f" == *"linux"* ]] && s=$((s + 1))
    if [[ $s -gt $score ]]; then
      score=$s
      best="$f"
    fi
  done
  [[ -n "$best" ]] && printf '%s' "$best"
}

if [[ ! -x "$VENV_PY" ]]; then
  warn "missing $VENV_PY — run ./start.sh controller --install first"
  exit 0
fi

if import_ok; then
  log "hailo_platform already importable in .venv"
  exit 0
fi

if ! hailo_install_enabled; then
  log "SMARTCAM_INSTALL_HAILO=0 — skipping hailo_platform install"
  exit 0
fi

log "hailo_platform not found in .venv — attempting install (see docs/SETUP_PI5.md §4b)"

wheel="$(pick_wheel || true)"
if [[ -n "$wheel" ]]; then
  log "pip install $(basename "$wheel")"
  "$VENV_PY" -m pip install -q "$wheel" || warn "pip install failed for $wheel"
  if import_ok; then
    log "hailo_platform OK after wheel install"
    exit 0
  fi
fi

if hailo_pcie_present; then
  log "Hailo PCIe device detected — trying apt hailo-all (Pi archive)"
  if try_apt_install hailo-all; then
    if import_ok; then
      log "hailo_platform OK after hailo-all (system-site-packages venv)"
      exit 0
    fi
    warn "hailo-all installed but hailo_platform still missing in .venv — copy hailo_platform-*.whl to backend/wheels/ and re-run, or reboot then retry"
  else
    warn "could not apt install hailo-all — enable archive.raspberrypi.com or run: sudo apt install hailo-all"
  fi
else
  warn "no Hailo PCIe device — place hailo_platform wheel in backend/wheels/ or set SMARTCAM_HAILO_WHEEL"
fi

if ! import_ok; then
  warn "hailo_platform still unavailable — OpenCV person detection still works without Hailo"
  warn "Download wheel: https://hailo.ai/developer-zone/software-downloads/ (match Python $(python_tag))"
  exit 0
fi
