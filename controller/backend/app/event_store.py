"""Persistent camera events for the dashboard Events panel (person_detected, etc.)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_events: List[Dict[str, Any]] = []
_next_id = 1
_loaded = False


def _resolve_events_json_path() -> str:
    env = os.environ.get("SMARTCAM_EVENTS_JSON", "").strip()
    if env:
        return os.path.normpath(os.path.expanduser(env))
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "data", "events.json"))


def _unix_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _parse_iso_ts(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _ensure_loaded() -> None:
    global _loaded, _events, _next_id
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        path = _resolve_events_json_path()
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                rows = data.get("events") if isinstance(data, dict) else None
                if isinstance(rows, list):
                    _events = [dict(r) for r in rows if isinstance(r, dict)]
                    max_id = 0
                    for row in _events:
                        try:
                            max_id = max(max_id, int(row.get("id", 0)))
                        except (TypeError, ValueError):
                            continue
                    _next_id = max_id + 1
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("[event_store] load failed %s: %s", path, e)
                _events = []
                _next_id = 1
        _loaded = True


def persist_events() -> bool:
    """Write events to JSON (atomic replace)."""
    _ensure_loaded()
    path = _resolve_events_json_path()
    parent = os.path.dirname(path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            logger.warning("[event_store] cannot create directory %r: %s", parent, e)
            return False
    with _lock:
        payload = {"events": [dict(r) for r in _events]}
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError as e:
        logger.warning("[event_store] persist failed %r: %s", path, e)
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False


def add_event(
    camera_id: int,
    event_type: str,
    ts_unix: float,
    *,
    recording_id: Optional[str] = None,
    filename: Optional[str] = None,
    person_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Append an event and persist. Returns the stored row."""
    _ensure_loaded()
    cid = int(camera_id)
    et = str(event_type or "").strip()
    if not et:
        raise ValueError("event_type required")
    ts_val = float(ts_unix)
    row: Dict[str, Any] = {
        "id": 0,
        "camera_id": cid,
        "event_type": et,
        "ts": _unix_to_iso(ts_val),
        "created_at": time.time(),
    }
    rid = str(recording_id or "").strip()
    if rid:
        row["recording_id"] = rid
    fn = str(filename or "").strip()
    if fn:
        row["filename"] = fn
    if person_count is not None:
        try:
            row["person_count"] = max(0, int(person_count))
        except (TypeError, ValueError):
            pass
    with _lock:
        global _next_id
        row["id"] = _next_id
        _next_id += 1
        _events.append(dict(row))
    persist_events()
    return dict(row)


def update_event_by_recording_id(
    camera_id: int,
    recording_id: str,
    *,
    filename: Optional[str] = None,
    clear_recording_id: bool = False,
) -> bool:
    """Update the newest matching event for camera + recording_id."""
    _ensure_loaded()
    cid = int(camera_id)
    rid = str(recording_id or "").strip()
    if not rid:
        return False
    updated = False
    with _lock:
        for row in reversed(_events):
            if int(row.get("camera_id", -1)) != cid:
                continue
            if str(row.get("recording_id") or "") != rid:
                continue
            if filename is not None:
                fn = str(filename or "").strip()
                if fn:
                    row["filename"] = fn
                else:
                    row.pop("filename", None)
            if clear_recording_id:
                row.pop("recording_id", None)
            updated = True
            break
    if updated:
        persist_events()
    return updated


def list_events(
    camera_id: int,
    *,
    limit: int = 200,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> List[Dict[str, Any]]:
    _ensure_loaded()
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 200
    lim = max(1, min(lim, 1000))
    cid = int(camera_id)
    from_unix = _parse_iso_ts(from_ts)
    to_unix = _parse_iso_ts(to_ts)
    with _lock:
        rows = [dict(r) for r in _events if int(r.get("camera_id", -1)) == cid]
    if from_unix is not None or to_unix is not None:
        filtered: List[Dict[str, Any]] = []
        for row in rows:
            ts = _parse_iso_ts(row.get("ts"))
            if ts is None:
                continue
            if from_unix is not None and ts < from_unix:
                continue
            if to_unix is not None and ts > to_unix:
                continue
            filtered.append(row)
        rows = filtered
    rows.sort(key=lambda r: _parse_iso_ts(r.get("ts")) or 0.0, reverse=True)
    return rows[:lim]


def delete_event(camera_id: int, event_id: int) -> bool:
    _ensure_loaded()
    cid = int(camera_id)
    eid = int(event_id)
    removed = False
    with _lock:
        global _events
        before = len(_events)
        _events = [
            r
            for r in _events
            if not (int(r.get("camera_id", -1)) == cid and int(r.get("id", -1)) == eid)
        ]
        removed = len(_events) < before
    if removed:
        persist_events()
    return removed


def delete_events_filtered(
    camera_id: int,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> int:
    _ensure_loaded()
    cid = int(camera_id)
    from_unix = _parse_iso_ts(from_ts)
    to_unix = _parse_iso_ts(to_ts)
    removed = 0
    with _lock:
        global _events
        kept: List[Dict[str, Any]] = []
        for row in _events:
            if int(row.get("camera_id", -1)) != cid:
                kept.append(row)
                continue
            if from_unix is None and to_unix is None:
                removed += 1
                continue
            ts = _parse_iso_ts(row.get("ts"))
            if ts is None:
                kept.append(row)
                continue
            if from_unix is not None and ts < from_unix:
                kept.append(row)
                continue
            if to_unix is not None and ts > to_unix:
                kept.append(row)
                continue
            removed += 1
        _events = kept
    if removed:
        persist_events()
    return removed


def delete_events_for_camera(camera_id: int) -> int:
    return delete_events_filtered(int(camera_id))
