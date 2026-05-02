#!/usr/bin/env bash
# Download MediaMTX from GitHub releases into controller/backend/bin/.
# Not installable via pip — run once after pip install -r requirements.txt.
set -euo pipefail

VERSION="${MEDIAMTX_VERSION:-v1.18.1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="${MEDIAMTX_INSTALL_DIR:-$BACKEND_ROOT/bin}"
mkdir -p "$DEST"

uname_s="$(uname -s)"
uname_m="$(uname -m)"
case "${uname_s}_${uname_m}" in
  Linux_aarch64 | Linux_arm64) SUFFIX="linux_arm64" ;;
  Linux_armv7l) SUFFIX="linux_armv7" ;;
  Linux_armv6l) SUFFIX="linux_armv6" ;;
  Linux_x86_64) SUFFIX="linux_amd64" ;;
  Darwin_arm64) SUFFIX="darwin_arm64" ;;
  Darwin_x86_64) SUFFIX="darwin_amd64" ;;
  *)
    echo "Unsupported platform: ${uname_s} ${uname_m}" >&2
    exit 1
    ;;
esac

NAME="mediamtx_${VERSION}_${SUFFIX}.tar.gz"
URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/${NAME}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading ${URL}"
curl -fsSL -o "$TMP/$NAME" "$URL"
tar -xzf "$TMP/$NAME" -C "$TMP"
install -m 0755 "$TMP/mediamtx" "$DEST/mediamtx"
echo "Installed: $DEST/mediamtx"
"$DEST/mediamtx" --version || true
