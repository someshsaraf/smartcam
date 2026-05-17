"""SQLite event log for person detection and motion recording (per camera)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PERSON_DETECTED_EVENT = "person_detected"

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "events.db"
_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ts_param(value: Optional[str]) -> Optional[str]:
    """Parse client ISO timestamp to UTC ISO for DB compare."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    s = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"invalid timestamp: {raw}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _event_time_filters(
    from_ts: Optional[str],
    to_ts: Optional[str],
) -> tuple[list[str], list[Any]]:
    """SQL fragments and params for ts range (normalized UTC ISO)."""
    clauses: list[str] = []
    params: list[Any] = []
    if from_ts:
        clauses.append("ts >= ?")
        params.append(from_ts)
    if to_ts:
        clauses.append("ts <= ?")
        params.append(to_ts)
    return clauses, params


def _connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS surveillance_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            ts TEXT NOT NULL,
            recording_id TEXT,
            filename TEXT,
            person_count INTEGER,
            detail TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_cam_ts ON surveillance_events (camera_id, ts DESC)"
    )
    conn.commit()
    _CONN = conn
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    detail_raw = row["detail"]
    detail: Any = None
    if detail_raw:
        try:
            detail = json.loads(detail_raw)
        except json.JSONDecodeError:
            detail = detail_raw
    return {
        "id": int(row["id"]),
        "camera_id": int(row["camera_id"]),
        "event_type": str(row["event_type"]),
        "ts": str(row["ts"]),
        "recording_id": row["recording_id"],
        "filename": row["filename"],
        "person_count": row["person_count"],
        "detail": detail,
    }


def append_event(
    camera_id: int,
    event_type: str,
    *,
    recording_id: Optional[str] = None,
    filename: Optional[str] = None,
    person_count: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    et = str(event_type or "").strip()
    if et != PERSON_DETECTED_EVENT:
        raise ValueError(f"only {PERSON_DETECTED_EVENT!r} events are stored")
    ts = _utc_iso()
    detail_json = json.dumps(detail) if detail else None
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO surveillance_events
                (camera_id, event_type, ts, recording_id, filename, person_count, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(camera_id),
                et,
                ts,
                recording_id,
                filename,
                person_count,
                detail_json,
            ),
        )
        conn.commit()
        row_id = int(cur.lastrowid)
    return {
        "id": row_id,
        "camera_id": int(camera_id),
        "event_type": et,
        "ts": ts,
        "recording_id": recording_id,
        "filename": filename,
        "person_count": person_count,
        "detail": detail,
    }


def list_events(
    camera_id: int,
    *,
    limit: int = 200,
    offset: int = 0,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> list[dict[str, Any]]:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    time_clauses, time_params = _event_time_filters(from_ts, to_ts)
    where = ["camera_id = ?", "event_type = ?", *time_clauses]
    params: list[Any] = [int(camera_id), PERSON_DETECTED_EVENT, *time_params, limit, offset]
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            f"""
            SELECT id, camera_id, event_type, ts, recording_id, filename, person_count, detail
            FROM surveillance_events
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_event(camera_id: int, event_id: int) -> bool:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    if not isinstance(event_id, int) or event_id < 1:
        raise ValueError("event_id must be a positive int")
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            """
            DELETE FROM surveillance_events
            WHERE camera_id = ? AND id = ? AND event_type = ?
            """,
            (int(camera_id), int(event_id), PERSON_DETECTED_EVENT),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0


def delete_events(
    camera_id: int,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> int:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    time_clauses, time_params = _event_time_filters(from_ts, to_ts)
    where = ["camera_id = ?", "event_type = ?", *time_clauses]
    params: list[Any] = [int(camera_id), PERSON_DETECTED_EVENT, *time_params]
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            f"DELETE FROM surveillance_events WHERE {' AND '.join(where)}",
            params,
        )
        conn.commit()
        return int(cur.rowcount or 0)


def attach_recording_filename(
    camera_id: int,
    recording_id: str,
    filename: str,
) -> int:
    """Set filename on person_detected rows for a finished motion clip."""
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    rid = str(recording_id or "").strip()
    fn = str(filename or "").strip()
    if not rid or not fn:
        return 0
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            """
            UPDATE surveillance_events
            SET filename = ?
            WHERE camera_id = ? AND recording_id = ? AND event_type = ?
              AND (filename IS NULL OR filename = '')
            """,
            (fn, int(camera_id), rid, PERSON_DETECTED_EVENT),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def purge_legacy_events() -> int:
    """Remove mqtt/recording_* rows written before person_detected-only policy."""
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM surveillance_events WHERE event_type != ?",
            (PERSON_DETECTED_EVENT,),
        )
        conn.commit()
        return int(cur.rowcount or 0)
