#!/usr/bin/env bash
# install_mediamtx.sh — download MediaMTX into controller/backend/bin/mediamtx
# (no sudo). Invoked by repo-root ./start.sh controller --install when the
# binary is missing.
#
# Usage:
#   ./scripts/install_mediamtx.sh [--version vX.Y.Z] [--force]
#   bash scripts/install_mediamtx.sh [--version vX.Y.Z] [--force]
#
# Flags:
#   --version vX.Y.Z   Pin release (default below).
#   --force            Overwrite an existing backend/bin/mediamtx.
#   -h, --help         Show help and exit.
#
# Security: SHA256 verification against upstream checksum file (same pattern as
# edge-agent/scripts/install-rpi-mediamtx.sh). Downloads only from GitHub
# bluenviron/mediamtx releases.

set -euo pipefail

DEFAULT_VERSION="v1.18.1"
VERSION=""
FORCE=0

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${BACKEND_ROOT}/bin/mediamtx"

usage() {
  cat <<'EOF'
Usage: install_mediamtx.sh [--version vX.Y.Z] [--force]

Installs mediamtx to controller/backend/bin/mediamtx (creates bin/ if needed).
Run from repo: ./start.sh controller --install
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      [[ -n "$VERSION" ]] || { echo "Error: --version needs a value (e.g. v1.18.1)" >&2; exit 1; }
      shift 2
      ;;
    --force) FORCE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -z "$VERSION" ]] && VERSION="$DEFAULT_VERSION"

if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: --version must look like vMAJOR.MINOR.PATCH (got: $VERSION)" >&2
  exit 1
fi

hint_bad_release() {
  echo "Hint: ${VERSION} may not exist on GitHub, or the asset name changed." >&2
  echo "      Try: $0 --version ${DEFAULT_VERSION}" >&2
  echo "      https://github.com/bluenviron/mediamtx/releases" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Error: required command not on PATH: $1" >&2
    exit 3
  }
}

require_cmd curl
require_cmd tar
require_cmd awk
require_cmd head

sha256_of_file() {
  local f="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$f" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$f" | awk '{print $1}'
  else
    echo "Error: need sha256sum or shasum -a 256" >&2
    exit 3
  fi
}

os="$(uname -s)"
arch="$(uname -m)"
case "${os}/${arch}" in
  Linux/aarch64 | Linux/arm64) ARCH_TAG="linux_arm64" ;;
  Linux/armv7l | Linux/armv7 | Linux/armhf) ARCH_TAG="linux_armv7" ;;
  Linux/x86_64 | Linux/amd64) ARCH_TAG="linux_amd64" ;;
  Darwin/arm64) ARCH_TAG="darwin_arm64" ;;
  Darwin/x86_64) ARCH_TAG="darwin_amd64" ;;
  *)
    echo "Error: unsupported OS/arch: ${os} ${arch}" >&2
    exit 2
    ;;
esac

if [[ -e "$DEST" && "$FORCE" -eq 0 ]]; then
  echo "$DEST already exists. Use --force to replace." >&2
  if "$DEST" --version >/dev/null 2>&1; then
    "$DEST" --version >&2
  fi
  exit 0
fi

TMP="$(mktemp -d -t smartcam-ctl-mediamtx.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

TARBALL="mediamtx_${VERSION}_${ARCH_TAG}.tar.gz"
BASE="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}"

echo "Installing MediaMTX ${VERSION} (${ARCH_TAG}) to ${DEST} ..."

echo "Downloading ${BASE}/${TARBALL} ..."
if ! curl -fsSL -o "${TMP}/${TARBALL}" "${BASE}/${TARBALL}"; then
  if [[ "$ARCH_TAG" == "linux_arm64" ]]; then
    LEGACY="mediamtx_${VERSION}_linux_arm64v8.tar.gz"
    echo "Retrying legacy asset ${LEGACY} ..."
    if ! curl -fsSL -o "${TMP}/${LEGACY}" "${BASE}/${LEGACY}"; then
      echo "Error: download failed." >&2
      hint_bad_release
      exit 4
    fi
    TARBALL="$LEGACY"
  else
    echo "Error: download failed." >&2
    hint_bad_release
    exit 4
  fi
fi

CHECKSUM_LOCAL="${TMP}/checksums.verify"
SUMFILE_LABEL=""
for name in "checksums.sha256" "mediamtx_${VERSION}_checksums.txt" "checksums.txt"; do
  echo "Downloading checksum file (${name}) ..."
  if curl -fsSL -o "$CHECKSUM_LOCAL" "${BASE}/${name}"; then
    SUMFILE_LABEL="$name"
    break
  fi
done
if [[ -z "$SUMFILE_LABEL" ]]; then
  echo "Error: cannot fetch checksum file from ${BASE}" >&2
  hint_bad_release
  exit 4
fi

expected="$(awk -v t="$TARBALL" '$2 == t || $2 == "*"t { print $1 }' "$CHECKSUM_LOCAL" | head -n1)"
if [[ -z "$expected" ]]; then
  echo "Error: no checksum line for ${TARBALL} in ${SUMFILE_LABEL}." >&2
  exit 5
fi

actual="$(sha256_of_file "${TMP}/${TARBALL}")"
if [[ "$expected" != "$actual" ]]; then
  echo "Error: SHA256 mismatch for ${TARBALL}." >&2
  echo "  expected: $expected" >&2
  echo "  actual:   $actual" >&2
  exit 5
fi
echo "SHA256 OK."

mkdir -p "${TMP}/extract"
if ! tar -xzf "${TMP}/${TARBALL}" -C "${TMP}/extract" mediamtx; then
  echo "Error: tar extract failed." >&2
  exit 4
fi

mkdir -p "${BACKEND_ROOT}/bin"
install -m 0755 "${TMP}/extract/mediamtx" "$DEST"

echo "Installed: $DEST"
"$DEST" --version || true
echo "Start the controller with: ./start.sh controller"
