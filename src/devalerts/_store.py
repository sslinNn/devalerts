"""Local SQLite state: fingerprint-based grouping, dedup, and rate limiting."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
import traceback
from pathlib import Path

_DB_PATH = Path.home() / ".devalerts" / "state.db"
_RETENTION_SECONDS = 7 * 24 * 3600
_DEFAULT_RATE_LIMIT_SECONDS = 300


def _fingerprint(exc_type, tb) -> tuple[str, str]:
    frames = traceback.extract_tb(tb)
    location = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"
    raw = f"{exc_type.__name__}:{location}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16], location


def _get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS error_groups (
            fingerprint TEXT PRIMARY KEY,
            exc_type TEXT NOT NULL,
            location TEXT NOT NULL,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            last_sent REAL,
            count_since_last_sent INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return conn


def _should_send(
    fingerprint: str, exc_type_name: str, location: str, rate_limit_seconds: int
) -> tuple[bool, int]:
    now = time.time()
    try:
        conn = _get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT last_sent, count_since_last_sent FROM error_groups WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if row is None or row[0] is None or now - row[0] >= rate_limit_seconds:
                    send, skipped = True, (row[1] if row else 0)
                    conn.execute(
                        """
                        INSERT INTO error_groups
                            (fingerprint, exc_type, location, first_seen, last_seen,
                             last_sent, count_since_last_sent, total_count)
                        VALUES (?, ?, ?, ?, ?, ?, 0, 1)
                        ON CONFLICT(fingerprint) DO UPDATE SET
                            last_seen = excluded.last_seen,
                            last_sent = excluded.last_sent,
                            count_since_last_sent = 0,
                            total_count = total_count + 1
                        """,
                        (fingerprint, exc_type_name, location, now, now, now),
                    )
                else:
                    send, skipped = False, 0
                    conn.execute(
                        """
                        UPDATE error_groups
                        SET last_seen = ?, count_since_last_sent = count_since_last_sent + 1,
                            total_count = total_count + 1
                        WHERE fingerprint = ?
                        """,
                        (now, fingerprint),
                    )
                conn.execute(
                    "DELETE FROM error_groups WHERE last_seen < ?",
                    (now - _RETENTION_SECONDS,),
                )
            return send, skipped
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as error:
        # ponytail: dedup/rate-limit state must never block an alert -- fail
        # open (send, as if this were the first occurrence) on any DB error.
        print(
            f"devalerts: dedup/rate-limit state error, sending anyway: {error}",
            file=sys.stderr,
        )
        return True, 0
