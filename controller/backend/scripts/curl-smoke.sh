#!/usr/bin/env bash
# Pretty-print /cameras (or show why json.tool would fail). No fragile pipes.
# Usage: ./scripts/curl-smoke.sh [http://127.0.0.1:8000]
set -euo pipefail
BASE="${1:-http://127.0.0.1:8000}"
ROOT="${BASE%/}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "== GET $ROOT/ =="
code_root="$(curl -sS -o "$TMP" -w '%{http_code}' "$ROOT/" || echo "000")"
echo "HTTP $code_root"
if [[ ! -s "$TMP" ]]; then
  echo "(empty body — nothing listening at $ROOT, or connection failed)"
else
  head -c 400 "$TMP" | cat -v
  echo
  if [[ "$code_root" == "200" ]] && command -v python3 >/dev/null; then
    python3 -m json.tool <"$TMP" 2>/dev/null || echo "(not valid JSON — see raw above)"
  fi
fi
echo

echo "== GET $ROOT/cameras =="
code="$(curl -sS -o "$TMP" -w '%{http_code}' "$ROOT/cameras" || echo "000")"
echo "HTTP $code"
if [[ ! -s "$TMP" ]]; then
  echo "Empty body. Typical causes:"
  echo "  - uvicorn not running on this host/port"
  echo "  - wrong BASE URL (try: $0 http://127.0.0.1:5000)"
  exit 1
fi
echo "First bytes (visible):"
head -c 400 "$TMP" | cat -v
echo
if [[ "$code" == "200" ]]; then
  python3 -m json.tool <"$TMP"
else
  echo "Non-200: not piping to json.tool."
  exit 1
fi
