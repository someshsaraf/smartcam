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

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "events.db"
_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    if not et:
        raise ValueError("event_type required")
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
) -> list[dict[str, Any]]:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            """
            SELECT id, camera_id, event_type, ts, recording_id, filename, person_count, detail
            FROM surveillance_events
            WHERE camera_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (int(camera_id), limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
