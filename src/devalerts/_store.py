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
# ponytail: fixed cap, not configurable -- upgrade to an init() param if
# users report the ceiling being wrong for their crash-loop cadence.
_MAX_BACKOFF_MULTIPLIER = 8


def _fingerprint(exc_type, tb) -> tuple[str, str]:
    frames = traceback.extract_tb(tb)
    location = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"
    raw = f"{exc_type.__name__}:{location}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16], location


def _fingerprint_log(
    logger_name: str, level: int, msg: str, pathname: str, lineno: int
) -> tuple[str, str]:
    location = f"{pathname}:{lineno}"
    raw = f"{logger_name}:{level}:{msg}:{location}"
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
            total_count INTEGER NOT NULL DEFAULT 0,
            rate_limit_seconds INTEGER,
            muted INTEGER NOT NULL DEFAULT 0,
            backoff_multiplier INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    # ponytail: lazy migration for DBs created before these columns existed --
    # ADD COLUMN has no "IF NOT EXISTS", so swallow the duplicate-column error
    # on every subsequent call instead of tracking schema version.
    for ddl in (
        "ALTER TABLE error_groups ADD COLUMN rate_limit_seconds INTEGER",
        "ALTER TABLE error_groups ADD COLUMN muted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE error_groups ADD COLUMN backoff_multiplier INTEGER NOT NULL DEFAULT 1",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    return conn


def _should_send(
    fingerprint: str, exc_type_name: str, location: str, rate_limit_seconds: int
) -> tuple[bool, int, bool]:
    now = time.time()
    try:
        conn = _get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT last_sent, count_since_last_sent, muted, backoff_multiplier "
                    "FROM error_groups WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                is_new = row is None
                muted = bool(row[2]) if row else False
                multiplier = row[3] if row else 1
                effective_window = rate_limit_seconds * multiplier
                if not muted and (
                    row is None or row[0] is None or now - row[0] >= effective_window
                ):
                    send, skipped = True, (row[1] if row else 0)
                    # Chronic (occurrences piled up while suppressed) doubles the
                    # backoff, capped at _MAX_BACKOFF_MULTIPLIER; a single fresh
                    # occurrence after a genuine quiet spell resets it to 1.
                    new_multiplier = (
                        min(multiplier * 2, _MAX_BACKOFF_MULTIPLIER) if skipped else 1
                    )
                    conn.execute(
                        """
                        INSERT INTO error_groups
                            (fingerprint, exc_type, location, first_seen, last_seen,
                             last_sent, count_since_last_sent, total_count,
                             rate_limit_seconds, backoff_multiplier)
                        VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                        ON CONFLICT(fingerprint) DO UPDATE SET
                            last_seen = excluded.last_seen,
                            last_sent = excluded.last_sent,
                            count_since_last_sent = 0,
                            total_count = total_count + 1,
                            rate_limit_seconds = excluded.rate_limit_seconds,
                            backoff_multiplier = excluded.backoff_multiplier
                        """,
                        (
                            fingerprint,
                            exc_type_name,
                            location,
                            now,
                            now,
                            now,
                            rate_limit_seconds,
                            new_multiplier,
                        ),
                    )
                else:
                    # Covers both "still rate-limited" and "muted" -- neither sends,
                    # both keep counting so an eventual unmute/window-expiry reports
                    # the accumulated skip count via count_since_last_sent.
                    send, skipped = False, 0
                    conn.execute(
                        """
                        UPDATE error_groups
                        SET last_seen = ?, count_since_last_sent = count_since_last_sent + 1,
                            total_count = total_count + 1, rate_limit_seconds = ?
                        WHERE fingerprint = ?
                        """,
                        (now, rate_limit_seconds, fingerprint),
                    )
                conn.execute(
                    "DELETE FROM error_groups WHERE last_seen < ?",
                    (now - _RETENTION_SECONDS,),
                )
            return send, skipped, is_new
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as error:
        # ponytail: dedup/rate-limit state must never block an alert -- fail
        # open (send, as if this were the first occurrence) on any DB error.
        print(
            f"devalerts: dedup/rate-limit state error, sending anyway: {error}",
            file=sys.stderr,
        )
        return True, 0, True


def _last_incident() -> float | None:
    """Unix timestamp of the most recent occurrence across all groups (sent or
    suppressed -- last_seen is updated either way), or None if the state DB
    has no groups recorded (fresh install, or everything cleared)."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT MAX(last_seen) FROM error_groups").fetchone()
    finally:
        conn.close()
    return row[0]


def _match_fingerprints(prefix: str) -> list[str]:
    """Fingerprints starting with prefix, for CLI mute/unmute/clear commands."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT fingerprint FROM error_groups WHERE fingerprint LIKE ? ESCAPE '\\'",
            (
                prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%",
            ),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def _set_muted(fingerprint: str, muted: bool) -> None:
    conn = _get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE error_groups SET muted = ? WHERE fingerprint = ?",
                (int(muted), fingerprint),
            )
    finally:
        conn.close()


def _clear(fingerprint: str) -> None:
    conn = _get_connection()
    try:
        with conn:
            conn.execute(
                "DELETE FROM error_groups WHERE fingerprint = ?", (fingerprint,)
            )
    finally:
        conn.close()


def _clear_all() -> None:
    conn = _get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM error_groups")
    finally:
        conn.close()
