#!/usr/bin/env bash
# Run on a dev machine with the full monorepo (controller + edge-agent as siblings).
# Refreshes the Pi-4–deployable copy under edge-agent/shared/surveillance_shared.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$(cd "$ROOT/../controller/shared/surveillance_shared" && pwd)"
DST="$ROOT/shared/surveillance_shared"
if [[ ! -d "$SRC" ]]; then
  echo "Source not found: $SRC" >&2
  exit 1
fi
mkdir -p "$ROOT/shared"
rm -rf "$DST"
cp -a "$SRC" "$DST"
echo "Updated: $DST"
