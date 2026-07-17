"""Throwaway prototype: send unhandled Python exceptions straight to a Telegram chat."""

from __future__ import annotations

import re
import traceback

__all__: list[str] = []

_MAX_MESSAGE_LENGTH = 4096


def _format_alert(exc_type, exc_value, tb) -> str:
    header = f"\U0001F534 {exc_type.__name__}: {exc_value}"
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


import json
import sys
import urllib.error
import urllib.request

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
