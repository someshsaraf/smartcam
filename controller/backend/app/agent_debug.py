"""Debug-mode NDJSON logging (session 2bcf4e)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_SESSION = "2bcf4e"
_RUN_ID = os.environ.get("SMARTCAM_DEBUG_RUN_ID", "pre-fix")


def _log_path() -> Path:
    env = os.environ.get("SMARTCAM_DEBUG_LOG", "").strip()
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for root in [here, *here.parents]:
        if (root / ".cursor").is_dir():
            return root / ".cursor" / f"debug-{_SESSION}.log"
    return Path(f"/tmp/debug-{_SESSION}.log")


def agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
) -> None:
    # #region agent log
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessionId": _SESSION,
            "runId": _RUN_ID,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except Exception:
        pass
    # #endregion
