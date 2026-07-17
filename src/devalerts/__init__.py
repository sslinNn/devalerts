"""Send unhandled Python exceptions straight to a Telegram chat."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

__all__: list[str] = ["init", "report", "capture", "ASGIMiddleware"]

_MAX_MESSAGE_LENGTH = 4096
_DB_PATH = Path.home() / ".devalerts" / "state.db"
_RETENTION_SECONDS = 7 * 24 * 3600
_DEFAULT_RATE_LIMIT_SECONDS = 300


def _format_alert(exc_type, exc_value, tb, skipped: int = 0) -> str:
    header = f"\U0001F534 {exc_type.__name__}: {exc_value}"
    if skipped:
        header += f"\n⚠️ Повторилась ещё {skipped} раз(а) с последнего алерта"
    body = "".join(traceback.format_exception(exc_type, exc_value, tb))
    message = f"{header}\n\n{body}"
    if len(message) <= _MAX_MESSAGE_LENGTH:
        return message
    marker = "\n\n...(truncated)...\n"
    # ponytail: header itself can exceed the limit if exc_value's str() is huge
    # (e.g. a validation error echoing a large payload) — keep can go negative,
    # so clamp it and hard-truncate the final result as a backstop guarantee.
    keep = max(_MAX_MESSAGE_LENGTH - len(header) - len(marker), 0)
    truncated = f"{header}{marker}{body[-keep:]}" if keep else header
    return truncated[:_MAX_MESSAGE_LENGTH]


# ponytail: fixed pattern list, not exhaustive — catches common
# token/key shapes only. Upgrade to entropy-based detection if
# real users report leaked secrets slipping through.
_REDACT_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_.]+", re.IGNORECASE), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"),
        r"\1=[REDACTED]",
    ),
]


def _redact(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


_TIMEOUT_SECONDS = 5


def _send_telegram_message(bot_token: str, chat_id, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"devalerts: failed to send Telegram alert: {error}", file=sys.stderr)


_state = {
    "bot_token": None,
    "chat_id": None,
    "redact": True,
    "rate_limit_seconds": _DEFAULT_RATE_LIMIT_SECONDS,
    "prev_excepthook": None,
    "prev_threading_excepthook": None,
}


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


def _should_send(fingerprint: str, exc_type_name: str, location: str, rate_limit_seconds: int) -> tuple[bool, int]:
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
                conn.execute("DELETE FROM error_groups WHERE last_seen < ?", (now - _RETENTION_SECONDS,))
            return send, skipped
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as error:
        # ponytail: dedup/rate-limit state must never block an alert -- fail
        # open (send, as if this were the first occurrence) on any DB error.
        print(f"devalerts: dedup/rate-limit state error, sending anyway: {error}", file=sys.stderr)
        return True, 0


def _send_exception(exc_type, exc_value, tb) -> None:
    fingerprint, location = _fingerprint(exc_type, tb)
    send, skipped = _should_send(fingerprint, exc_type.__name__, location, _state["rate_limit_seconds"])
    if not send:
        return
    message = _format_alert(exc_type, exc_value, tb, skipped=skipped)
    if _state["redact"]:
        message = _redact(message)
    _send_telegram_message(_state["bot_token"], _state["chat_id"], message)


def _excepthook(exc_type, exc_value, tb) -> None:
    if exc_type is not KeyboardInterrupt:
        try:
            _send_exception(exc_type, exc_value, tb)
        except Exception as error:  # noqa: BLE001 - crash handler must never raise
            print(f"devalerts: internal error while sending alert: {error}", file=sys.stderr)
    _state["prev_excepthook"](exc_type, exc_value, tb)


def _threading_excepthook(args) -> None:
    try:
        _send_exception(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception as error:  # noqa: BLE001
        print(f"devalerts: internal error while sending alert: {error}", file=sys.stderr)
    _state["prev_threading_excepthook"](args)


def init(
    bot_token: str,
    chat_id,
    *,
    redact: bool = True,
    rate_limit_seconds: int = _DEFAULT_RATE_LIMIT_SECONDS,
) -> None:
    """Install a global exception hook that sends unhandled exceptions to Telegram."""
    _state["bot_token"] = bot_token
    _state["chat_id"] = chat_id
    _state["redact"] = redact
    _state["rate_limit_seconds"] = rate_limit_seconds
    # ponytail: guard against calling init() twice capturing our own hook as
    # "previous" -- that would make _excepthook chain to itself and recurse
    # forever on the next crash, violating "must never raise".
    if sys.excepthook is not _excepthook:
        _state["prev_excepthook"] = sys.excepthook
    if threading.excepthook is not _threading_excepthook:
        _state["prev_threading_excepthook"] = threading.excepthook
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook


def report(exc: BaseException | None = None) -> None:
    """Manually send a caught exception to Telegram."""
    if exc is None:
        exc_type, exc_value, tb = sys.exc_info()
        if exc_type is None:
            raise RuntimeError("report() requires an active exception or an exc argument")
    else:
        exc_type, exc_value, tb = type(exc), exc, exc.__traceback__
    _send_exception(exc_type, exc_value, tb)


class capture(contextlib.ContextDecorator):
    """Context manager / decorator: report any exception raised inside the block or
    function, then re-raise it. Use ``@capture()`` on a function instead of wrapping
    its body in a manual ``try/except`` or ``with`` block."""

    def __enter__(self) -> "capture":
        return self

    def __exit__(self, exc_type, exc_value, tb) -> bool:
        if exc_type is not None:
            _send_exception(exc_type, exc_value, tb)
        return False


class ASGIMiddleware:
    """ASGI middleware for FastAPI/Starlette (or any ASGI app): report any exception
    that escapes a request, then re-raise it so the framework's own error handling
    still runs unchanged.

    Usage::

        app.add_middleware(devalerts.ASGIMiddleware)

    Only exceptions that actually reach here (unhandled server errors) get reported —
    routing 404s and raised ``HTTPException``s are already turned into responses by
    the framework before this middleware sees them, same as Sentry's ASGI integration.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        try:
            await self.app(scope, receive, send)
        except Exception:
            exc_type, exc_value, tb = sys.exc_info()
            # ponytail: fire-and-forget in a thread -- _send_exception is a
            # blocking network call; awaiting it here would stall the event
            # loop (and the error response) for up to _TIMEOUT_SECONDS.
            threading.Thread(
                target=_send_exception, args=(exc_type, exc_value, tb), daemon=True
            ).start()
            raise
