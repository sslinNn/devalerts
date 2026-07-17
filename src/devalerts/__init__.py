"""Throwaway prototype: send unhandled Python exceptions straight to a Telegram chat."""

from __future__ import annotations

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
