"""Slack incoming-webhook delivery -- retries transient failures, logs to the
same local fallback file Telegram delivery uses if every attempt fails. Never
raises."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from ._telegram import _log_failed_delivery

_TIMEOUT_SECONDS = 5
_MAX_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 1.0


def _send_slack_message(webhook_url: str, text: str) -> bool:
    payload = json.dumps({"text": text}).encode("utf-8")
    last_error: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        request = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
            return True
        except (urllib.error.URLError, OSError, ValueError) as error:
            last_error = error

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_SECONDS)

    print(f"devalerts: failed to send Slack alert: {last_error}", file=sys.stderr)
    _log_failed_delivery(text)
    return False
