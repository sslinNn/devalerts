"""CLI: `devalerts dashboard` reports grouped/rate-limited errors from the local
state DB; `devalerts test` sends a one-off message to verify bot_token/chat_id."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from importlib.metadata import version as _pkg_version

from ._slack import _send_slack_message
from ._store import (
    _DB_PATH,
    _DEFAULT_RATE_LIMIT_SECONDS,
    _clear,
    _clear_all,
    _match_fingerprints,
    _set_muted,
)
from ._telegram import _send_telegram_message

_LOCATION_WIDTH = 34


def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        "─●…×".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError, TypeError):
        return False


def _truncate(text: str, width: int, ellipsis: str) -> str:
    """Keep the tail (file:line matters more than leading directories)."""
    if len(text) <= width:
        return text
    keep = width - len(ellipsis)
    return ellipsis + text[-keep:] if keep > 0 else ellipsis[:width]


def _relative_time(ts: float, now: float) -> str:
    delta = now - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        # ponytail: enable VT100 processing for legacy conhost.exe -- Windows
        # Terminal / PowerShell 7 already support ANSI without this.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    return True


class _Style:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self._enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)


def _dashboard(as_json: bool = False) -> int:
    if not _DB_PATH.exists():
        print("[]" if as_json else "No errors recorded yet.")
        return 0

    conn = sqlite3.connect(_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT fingerprint, exc_type, location, last_seen, last_sent, total_count, "
            "count_since_last_sent, rate_limit_seconds, muted, backoff_multiplier "
            "FROM error_groups ORDER BY last_seen DESC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("[]" if as_json else "No errors recorded yet.")
        return 0

    now = time.time()

    if as_json:
        groups = []
        for (
            fingerprint,
            exc_type,
            location,
            last_seen,
            last_sent,
            total_count,
            count_since_last_sent,
            rate_limit_seconds,
            muted,
            backoff_multiplier,
        ) in rows:
            limit = (
                rate_limit_seconds
                if rate_limit_seconds is not None
                else _DEFAULT_RATE_LIMIT_SECONDS
            ) * backoff_multiplier
            rate_limited = (
                not muted and last_sent is not None and now - last_sent < limit
            )
            groups.append(
                {
                    "fingerprint": fingerprint,
                    "exc_type": exc_type,
                    "location": location,
                    "last_seen": last_seen,
                    "last_sent": last_sent,
                    "total_count": total_count,
                    "count_since_last_sent": count_since_last_sent,
                    "rate_limited": rate_limited,
                    "muted": bool(muted),
                    "backoff_multiplier": backoff_multiplier,
                }
            )
        print(json.dumps(groups))
        return 0

    unicode_ok = _supports_unicode()
    sep_char = "─" if unicode_ok else "-"
    dot_char = "●" if unicode_ok else "*"
    ellipsis = "…" if unicode_ok else "..."
    times_char = "×" if unicode_ok else "x"

    style = _Style(_color_enabled())
    type_width = max(len("TYPE"), *(len(r[1]) for r in rows))

    header = (
        f"  {'ID':<8}  {'TYPE':<{type_width}}  {'LOCATION':<{_LOCATION_WIDTH}}  "
        f"{'LAST SEEN':<10}{'TOTAL':>6}   STATUS"
    )
    print(style.bold(header))
    print("  " + sep_char * (len(header) - 2))

    limited_count = 0
    muted_count = 0
    for (
        fingerprint,
        exc_type,
        location,
        last_seen,
        last_sent,
        total_count,
        _count_since_last_sent,
        rate_limit_seconds,
        muted,
        backoff_multiplier,
    ) in rows:
        backoff_suffix = (
            f" {times_char}{backoff_multiplier}" if backoff_multiplier > 1 else ""
        )
        if muted:
            muted_count += 1
            status = style.dim(f"{dot_char} muted")
        else:
            limit = (
                rate_limit_seconds
                if rate_limit_seconds is not None
                else _DEFAULT_RATE_LIMIT_SECONDS
            ) * backoff_multiplier
            in_window = last_sent is not None and now - last_sent < limit
            if in_window:
                limited_count += 1
                status = style.red(f"{dot_char} limited{backoff_suffix}")
            else:
                status = style.green(f"{dot_char} sending{backoff_suffix}")

        row = (
            f"  {style.dim(f'{fingerprint[:8]:<8}')}  "
            f"{exc_type:<{type_width}}  "
            f"{_truncate(location, _LOCATION_WIDTH, ellipsis):<{_LOCATION_WIDTH}}  "
            f"{_relative_time(last_seen, now):<10}"
            f"{total_count:>6}   {status}"
        )
        print(row)

    plural = "" if len(rows) == 1 else "s"
    print(
        f"\n{len(rows)} error group{plural}, {limited_count} currently rate-limited, "
        f"{muted_count} muted."
    )
    return 0


def _resolve_fingerprint(prefix: str) -> str | None:
    matches = _match_fingerprints(prefix)
    if not matches:
        print(f"No error group matches '{prefix}'.", file=sys.stderr)
        return None
    if len(matches) > 1:
        print(
            f"'{prefix}' matches {len(matches)} error groups, be more specific.",
            file=sys.stderr,
        )
        return None
    return matches[0]


def _mute_command(prefix: str, muted: bool) -> int:
    fingerprint = _resolve_fingerprint(prefix)
    if fingerprint is None:
        return 1
    _set_muted(fingerprint, muted)
    print(f"{'Muted' if muted else 'Unmuted'} {fingerprint[:8]}.")
    return 0


def _clear_command(prefix: str | None, clear_all: bool) -> int:
    if clear_all:
        _clear_all()
        print("Cleared all error groups.")
        return 0
    # argparse's mutually exclusive group guarantees exactly one is set.
    assert prefix is not None
    fingerprint = _resolve_fingerprint(prefix)
    if fingerprint is None:
        return 1
    _clear(fingerprint)
    print(f"Cleared {fingerprint[:8]}.")
    return 0


def _test(
    bot_token: str | None, chat_id: str | None, slack_webhook_url: str | None
) -> int:
    if bool(bot_token) != bool(chat_id):
        print("--bot-token and --chat-id must be given together.", file=sys.stderr)
        return 1
    if not bot_token and not slack_webhook_url:
        print(
            "Provide --bot-token/--chat-id and/or --slack-webhook-url.",
            file=sys.stderr,
        )
        return 1
    ok = True
    if bot_token and chat_id:
        ok = (
            _send_telegram_message(
                bot_token,
                chat_id,
                "✅ devalerts test message -- bot_token and chat_id are wired up "
                "correctly.",
            )
            and ok
        )
    if slack_webhook_url:
        ok = (
            _send_slack_message(
                slack_webhook_url,
                "✅ devalerts test message -- slack_webhook_url is wired up correctly.",
            )
            and ok
        )
    if not ok:
        print("Failed to send test message (see error above).", file=sys.stderr)
        return 1
    print("Test message sent -- check your chat.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devalerts")
    parser.add_argument(
        "--version", action="version", version=f"devalerts {_pkg_version('devalerts')}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dashboard_parser = subparsers.add_parser(
        "dashboard", help="Show grouped/rate-limited errors"
    )
    dashboard_parser.add_argument(
        "--json", action="store_true", help="Output as JSON instead of a table"
    )
    test_parser = subparsers.add_parser(
        "test",
        help="Send a test message to verify bot_token/chat_id and/or slack_webhook_url",
    )
    test_parser.add_argument("--bot-token")
    test_parser.add_argument("--chat-id")
    test_parser.add_argument("--slack-webhook-url")
    mute_parser = subparsers.add_parser("mute", help="Silence a specific error group")
    mute_parser.add_argument(
        "fingerprint", help="Fingerprint or unique prefix (dashboard ID column)"
    )
    unmute_parser = subparsers.add_parser(
        "unmute", help="Re-enable alerts for a muted error group"
    )
    unmute_parser.add_argument("fingerprint", help="Fingerprint or unique prefix")
    clear_parser = subparsers.add_parser(
        "clear", help="Delete error group(s) from local state"
    )
    clear_target = clear_parser.add_mutually_exclusive_group(required=True)
    clear_target.add_argument(
        "fingerprint", nargs="?", help="Fingerprint or unique prefix"
    )
    clear_target.add_argument(
        "--all", action="store_true", help="Delete all error groups"
    )
    args = parser.parse_args(argv)

    if args.command == "dashboard":
        return _dashboard(as_json=args.json)
    if args.command == "test":
        return _test(args.bot_token, args.chat_id, args.slack_webhook_url)
    if args.command == "mute":
        return _mute_command(args.fingerprint, muted=True)
    if args.command == "unmute":
        return _mute_command(args.fingerprint, muted=False)
    if args.command == "clear":
        return _clear_command(args.fingerprint, args.all)
    return 1


if __name__ == "__main__":
    sys.exit(main())
