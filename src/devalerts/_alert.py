"""Alert message formatting and secret redaction."""

from __future__ import annotations

import re
import socket
import traceback

_MAX_MESSAGE_LENGTH = 4096


def _format_context(tags: dict[str, str] | None) -> str:
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = "unknown-host"
    line = f"🖥️ {hostname}"
    if tags:
        line += " (" + ", ".join(f"{key}={value}" for key, value in tags.items()) + ")"
    return line


def _format_alert(
    exc_type, exc_value, tb, skipped: int = 0, tags: dict[str, str] | None = None
) -> str:
    header = f"\U0001f534 {exc_type.__name__}: {exc_value}\n{_format_context(tags)}"
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
