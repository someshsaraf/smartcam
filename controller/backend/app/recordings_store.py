"""SQLite catalog of recording clips (files remain on edge or local disk)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "recordings.db"
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
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            recording_id TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(camera_id, filename)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recordings_cam_mtime
        ON recordings (camera_id, mtime DESC)
        """
    )
    conn.commit()
    _CONN = conn
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "camera_id": int(row["camera_id"]),
        "name": str(row["filename"]),
        "filename": str(row["filename"]),
        "size": int(row["size"]),
        "mtime": float(row["mtime"]),
        "recording_id": row["recording_id"],
    }


def upsert_recording(
    camera_id: int,
    filename: str,
    *,
    size: int,
    mtime: float,
    recording_id: Optional[str] = None,
) -> None:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    fn = str(filename or "").strip()
    if not fn:
        raise ValueError("filename required")
    ts = _utc_iso()
    with _LOCK:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO recordings (camera_id, filename, size, mtime, recording_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id, filename) DO UPDATE SET
                size = excluded.size,
                mtime = excluded.mtime,
                recording_id = COALESCE(excluded.recording_id, recordings.recording_id),
                updated_at = excluded.updated_at
            """,
            (int(camera_id), fn, int(size), float(mtime), recording_id, ts),
        )
        conn.commit()


def delete_recording(camera_id: int, filename: str) -> bool:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    fn = str(filename or "").strip()
    if not fn:
        return False
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM recordings WHERE camera_id = ? AND filename = ?",
            (int(camera_id), fn),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0


def delete_all_for_camera(camera_id: int) -> int:
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    with _LOCK:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM recordings WHERE camera_id = ?",
            (int(camera_id),),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def list_recordings(
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
            SELECT camera_id, filename, size, mtime, recording_id
            FROM recordings
            WHERE camera_id = ?
            ORDER BY mtime DESC, filename DESC
            LIMIT ? OFFSET ?
            """,
            (int(camera_id), limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_all_recordings(
    *,
    camera_id: Optional[int] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(1000, int(limit)))
    offset = max(0, int(offset))
    with _LOCK:
        conn = _connect()
        if camera_id is not None:
            rows = conn.execute(
                """
                SELECT camera_id, filename, size, mtime, recording_id
                FROM recordings
                WHERE camera_id = ?
                ORDER BY mtime DESC, filename DESC
                LIMIT ? OFFSET ?
                """,
                (int(camera_id), limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT camera_id, filename, size, mtime, recording_id
                FROM recordings
                ORDER BY mtime DESC, filename DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_recordings(camera_id: Optional[int] = None) -> int:
    with _LOCK:
        conn = _connect()
        if camera_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM recordings WHERE camera_id = ?",
                (int(camera_id),),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM recordings").fetchone()
    return int(row["n"]) if row else 0


def reconcile_camera_from_edge_list(
    camera_id: int,
    edge_items: list[dict[str, Any]],
) -> dict[str, int]:
    """Upsert rows from edge list; remove DB rows missing on edge."""
    if not isinstance(camera_id, int) or camera_id < 0:
        raise ValueError("camera_id must be a non-negative int")
    names: set[str] = set()
    upserted = 0
    for item in edge_items:
        if not isinstance(item, dict):
            continue
        fn = str(item.get("name") or item.get("filename") or "").strip()
        if not fn:
            continue
        names.add(fn)
        try:
            upsert_recording(
                camera_id,
                fn,
                size=int(item.get("size") or 0),
                mtime=float(item.get("mtime") or 0),
                recording_id=(
                    str(item["recording_id"]).strip()
                    if item.get("recording_id")
                    else None
                ),
            )
            upserted += 1
        except (TypeError, ValueError) as e:
            logger.debug("skip recording row cam_id=%s name=%s: %s", camera_id, fn, e)

    removed = 0
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            "SELECT filename FROM recordings WHERE camera_id = ?",
            (int(camera_id),),
        ).fetchall()
        for row in rows:
            fn = str(row["filename"])
            if fn not in names:
                conn.execute(
                    "DELETE FROM recordings WHERE camera_id = ? AND filename = ?",
                    (int(camera_id), fn),
                )
                removed += 1
        conn.commit()
    return {"upserted": upserted, "removed": removed, "listed": len(names)}
