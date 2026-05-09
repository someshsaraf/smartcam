#!/usr/bin/env bash
# install-rpi-mediamtx.sh — install MediaMTX on a Raspberry Pi 4 for the
# SmartCam edge-agent's LocalPublisher.
#
# Idempotent. Detects arm64v8 / armv7, downloads the matching upstream
# release tarball, verifies its SHA256 against the upstream checksum file,
# and installs the binary to /usr/local/bin/mediamtx.
#
# Usage:
#   ./scripts/install-rpi-mediamtx.sh [--version vX.Y.Z] [--with-libcamera] [--force]
#
# Flags:
#   --version vX.Y.Z   Pin a specific MediaMTX release (default DEFAULT_VERSION below).
#   --with-libcamera   Also apt-install libcamera + rpicam-apps (with name fallbacks).
#   --force            Overwrite an existing /usr/local/bin/mediamtx.
#   -h, --help         Show this help and exit.
#
# Security:
#   - SHA256 verification is mandatory; the script aborts if the upstream
#     checksum file is missing or the downloaded tarball doesn't match.
#   - Downloads are restricted to https://github.com/bluenviron/mediamtx/...
#   - The installer does not run mediamtx; the LocalPublisher inside the
#     edge-agent owns the process lifecycle.
#
# Exit codes:
#   0   success
#   1   bad CLI arguments
#   2   unsupported architecture
#   3   missing required tool on PATH
#   4   download / extract failure
#   5   checksum mismatch

set -euo pipefail

DEFAULT_VERSION="v1.9.0"

VERSION=""
WITH_LIBCAMERA=0
FORCE=0

usage() {
  # Skip the shebang on line 1; print the doc-comment block until the first
  # non-comment line.
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      if [[ -z "$VERSION" ]]; then
        echo "Error: --version requires an argument (e.g. v1.9.0)" >&2
        exit 1
      fi
      shift 2
      ;;
    --with-libcamera)
      WITH_LIBCAMERA=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION="$DEFAULT_VERSION"
fi

if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: --version must look like vMAJOR.MINOR.PATCH (got: $VERSION)" >&2
  exit 1
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command not found on PATH: $1" >&2
    exit 3
  fi
}

require_cmd curl
require_cmd tar
require_cmd sha256sum
require_cmd uname

# Detect arch.
arch="$(uname -m)"
case "$arch" in
  aarch64 | arm64)
    ARCH_TAG="linux_arm64v8"
    ;;
  armv7l | armv7 | armhf)
    ARCH_TAG="linux_armv7"
    ;;
  *)
    echo "Error: unsupported architecture '$arch' (need aarch64 or armv7l)." >&2
    exit 2
    ;;
esac

DEST="/usr/local/bin/mediamtx"
if [[ -e "$DEST" && "$FORCE" -eq 0 ]]; then
  echo "$DEST already exists. Run again with --force to overwrite." >&2
  if "$DEST" --version >/dev/null 2>&1; then
    "$DEST" --version >&2
  fi
  exit 0
fi

# Stage in a private tmpdir; trap clean-up so we never leave the tarball
# behind on a failed install. Avoids partial-state on disk after errors.
TMP="$(mktemp -d -t smartcam-mediamtx.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

TARBALL="mediamtx_${VERSION}_${ARCH_TAG}.tar.gz"
SUMFILE="mediamtx_${VERSION}_checksums.txt"
BASE="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}"

echo "Downloading ${BASE}/${TARBALL} ..."
if ! curl -fsSL -o "${TMP}/${TARBALL}" "${BASE}/${TARBALL}"; then
  echo "Error: failed to download tarball." >&2
  exit 4
fi

# The upstream "checksums.txt" filename has historically appeared as
# "mediamtx_${VERSION}_checksums.txt". Some releases also publish a
# "checksums.txt" without the prefix; try the prefixed first, then the bare.
echo "Downloading checksum file ..."
if ! curl -fsSL -o "${TMP}/${SUMFILE}" "${BASE}/${SUMFILE}"; then
  if ! curl -fsSL -o "${TMP}/${SUMFILE}" "${BASE}/checksums.txt"; then
    echo "Error: cannot fetch checksum file from ${BASE}" >&2
    exit 4
  fi
fi

# Build a single-line digest file referencing our tarball name and verify.
expected="$(awk -v t="$TARBALL" '$2 == t || $2 == "*"t { print $1 }' "${TMP}/${SUMFILE}" | head -n1)"
if [[ -z "$expected" ]]; then
  echo "Error: checksum entry for ${TARBALL} not found in ${SUMFILE}." >&2
  exit 5
fi

actual="$(sha256sum "${TMP}/${TARBALL}" | awk '{print $1}')"
if [[ "$expected" != "$actual" ]]; then
  echo "Error: SHA256 mismatch for ${TARBALL}." >&2
  echo "  expected: $expected" >&2
  echo "  actual:   $actual" >&2
  exit 5
fi
echo "SHA256 OK."

# Extract.
mkdir -p "${TMP}/extract"
if ! tar -xzf "${TMP}/${TARBALL}" -C "${TMP}/extract" mediamtx; then
  echo "Error: tar extract failed." >&2
  exit 4
fi

# Install. We use sudo only when not already root.
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "Error: not root and no sudo available; cannot install to $DEST" >&2
    exit 3
  fi
fi

$SUDO install -m 0755 "${TMP}/extract/mediamtx" "$DEST"

if [[ "$WITH_LIBCAMERA" -eq 1 ]]; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "--with-libcamera requested but apt-get is not on PATH; skipping." >&2
  else
    echo "Installing libcamera and rpicam-apps via apt-get ..."
    export DEBIAN_FRONTEND=noninteractive
    $SUDO apt-get update -qq
    # Try modern names first, then older names. Don't fail the whole install
    # on a single missing package — Raspberry Pi OS releases differ.
    for pkg in libcamera0.5 libcamera0 libcamera-tools rpicam-apps libcamera-apps; do
      $SUDO apt-get install -y "$pkg" 2>/dev/null || true
    done
  fi
fi

echo
echo "Installed mediamtx to $DEST"
"$DEST" --version || true
echo
echo "Next step: enable the edge-agent publisher with SURVEILLANCE_PI_CAMERA=1"
echo "and start the agent (see docs/SETUP_PI4.md)."
