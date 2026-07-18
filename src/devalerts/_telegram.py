"""Telegram Bot API delivery -- retries transient failures, logs to a local
fallback file if every attempt fails. Never raises."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_TIMEOUT_SECONDS = 5
_MAX_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 1.0
# ponytail: cap how long we'll honor Telegram's requested Retry-After --
# beyond this we're blocking an excepthook that's about to exit the process,
# so give up and fall back to the local log instead of stalling shutdown.
_MAX_RETRY_AFTER_SECONDS = 10.0
_FAILED_LOG_PATH = Path.home() / ".devalerts" / "failed.log"


def _retry_after_seconds(error: urllib.error.HTTPError) -> float | None:
    try:
        body = json.loads(error.read())
        retry_after = body.get("parameters", {}).get("retry_after")
        return float(retry_after) if retry_after is not None else None
    except (ValueError, AttributeError, TypeError):
        return None


def _log_failed_delivery(text: str) -> None:
    try:
        _FAILED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _FAILED_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{text}\n\n")
    except OSError as error:
        print(f"devalerts: failed to write fallback log: {error}", file=sys.stderr)


def _send_telegram_message(bot_token: str, chat_id: int | str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    ).encode("utf-8")
    last_error: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        wait: float = _RETRY_BACKOFF_SECONDS
        try:
            urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
            return True
        except urllib.error.HTTPError as error:
            last_error = error
            retry_after = _retry_after_seconds(error)
            if retry_after is not None:
                if retry_after > _MAX_RETRY_AFTER_SECONDS:
                    break
                wait = retry_after
        except (urllib.error.URLError, OSError, ValueError) as error:
            last_error = error

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(wait)

    print(f"devalerts: failed to send Telegram alert: {last_error}", file=sys.stderr)
    _log_failed_delivery(text)
    return False
