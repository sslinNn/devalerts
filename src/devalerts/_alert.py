"""Alert message formatting and secret redaction."""

from __future__ import annotations

import re
import socket
import traceback

_MAX_MESSAGE_LENGTH = 4096


def _escape_html(text: str) -> str:
    """Escapes &, <, > -- required by both Telegram's HTML parse mode and Slack's
    mrkdwn, which use the same three characters for their own markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    exc_type,
    exc_value,
    tb,
    skipped: int = 0,
    tags: dict[str, str] | None = None,
    blame: str | None = None,
    is_new: bool = False,
) -> str:
    header = f"\U0001f534 {exc_type.__name__}: {exc_value}\n{_format_context(tags)}"
    if is_new:
        header = f"🆕 New error\n{header}"
    if blame:
        header += f"\n🕵️ blame: {blame}"
    if skipped:
        header += f"\n⚠️ Повторилась ещё {skipped} раз(а) с последнего алерта"
    body = "".join(traceback.format_exception(exc_type, exc_value, tb))

    if len(header) + 2 + len(body) > _MAX_MESSAGE_LENGTH:
        marker = "\n\n...(truncated)...\n"
        # ponytail: header itself can exceed the limit if exc_value's str() is huge
        # (e.g. a validation error echoing a large payload) — keep can go negative,
        # so clamp it; the header itself gets hard-truncated as a backstop. The "2"
        # accounts for the "\n\n" separator joined in below.
        keep = max(_MAX_MESSAGE_LENGTH - len(header) - 2 - len(marker), 0)
        body = f"{marker}{body[-keep:]}" if keep else ""
        header = header[:_MAX_MESSAGE_LENGTH]

    # Budgeted above against the plain text -- Telegram's 4096-char limit
    # applies to the parsed (tag-stripped) text, not the raw HTML we send.
    escaped_header = _escape_html(header)
    if not body:
        return escaped_header
    return (
        f"{escaped_header}\n\n<blockquote expandable>{_escape_html(body)}</blockquote>"
    )


def _format_alert_slack(
    exc_type,
    exc_value,
    tb,
    skipped: int = 0,
    tags: dict[str, str] | None = None,
    blame: str | None = None,
    is_new: bool = False,
) -> str:
    header = f"*\U0001f534 {exc_type.__name__}: {exc_value}*\n{_format_context(tags)}"
    if is_new:
        header = f"🆕 New error\n{header}"
    if blame:
        header += f"\n🕵️ blame: {blame}"
    if skipped:
        header += f"\n⚠️ Повторилась ещё {skipped} раз(а) с последнего алерта"
    body = "".join(traceback.format_exception(exc_type, exc_value, tb))

    fence = "```"
    if len(header) + 2 + len(fence) * 2 + len(body) > _MAX_MESSAGE_LENGTH:
        marker = "\n\n...(truncated)...\n"
        keep = max(
            _MAX_MESSAGE_LENGTH - len(header) - 2 - len(fence) * 2 - len(marker), 0
        )
        body = f"{marker}{body[-keep:]}" if keep else ""
        header = header[:_MAX_MESSAGE_LENGTH]

    escaped_header = _escape_html(header)
    if not body:
        return escaped_header
    return f"{escaped_header}\n\n{fence}{_escape_html(body)}{fence}"


def _format_log_alert_slack(
    logger_name: str,
    level_name: str,
    message: str,
    skipped: int = 0,
    tags: dict[str, str] | None = None,
    is_new: bool = False,
) -> str:
    header = (
        f"*\U0001f534 {logger_name} ({level_name}): {message}*\n{_format_context(tags)}"
    )
    if is_new:
        header = f"🆕 New error\n{header}"
    if skipped:
        header += f"\n⚠️ Повторилась ещё {skipped} раз(а) с последнего алерта"
    return _escape_html(header[:_MAX_MESSAGE_LENGTH])


def _format_log_alert(
    logger_name: str,
    level_name: str,
    message: str,
    skipped: int = 0,
    tags: dict[str, str] | None = None,
    is_new: bool = False,
) -> str:
    header = (
        f"\U0001f534 {logger_name} ({level_name}): {message}\n{_format_context(tags)}"
    )
    if is_new:
        header = f"🆕 New error\n{header}"
    if skipped:
        header += f"\n⚠️ Повторилась ещё {skipped} раз(а) с последнего алерта"
    return _escape_html(header[:_MAX_MESSAGE_LENGTH])


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
