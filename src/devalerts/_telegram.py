"""Telegram Bot API delivery -- never raises on network failure."""

from __future__ import annotations

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
