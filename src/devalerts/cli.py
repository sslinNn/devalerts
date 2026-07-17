"""CLI: `devalerts dashboard` reports grouped/rate-limited errors from the local state DB."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

from ._store import _DB_PATH, _DEFAULT_RATE_LIMIT_SECONDS

_LOCATION_WIDTH = 34


def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        "─●…".encode(encoding)
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


def _dashboard() -> int:
    if not _DB_PATH.exists():
        print("No errors recorded yet.")
        return 0

    conn = sqlite3.connect(_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT fingerprint, exc_type, location, last_seen, last_sent, total_count "
            "FROM error_groups ORDER BY last_seen DESC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No errors recorded yet.")
        return 0

    unicode_ok = _supports_unicode()
    sep_char = "─" if unicode_ok else "-"
    dot_char = "●" if unicode_ok else "*"
    ellipsis = "…" if unicode_ok else "..."

    style = _Style(_color_enabled())
    now = time.time()
    type_width = max(len("TYPE"), *(len(r[1]) for r in rows))

    header = (
        f"  {'ID':<8}  {'TYPE':<{type_width}}  {'LOCATION':<{_LOCATION_WIDTH}}  "
        f"{'LAST SEEN':<10}{'TOTAL':>6}   STATUS"
    )
    print(style.bold(header))
    print("  " + sep_char * (len(header) - 2))

    limited_count = 0
    for fingerprint, exc_type, location, last_seen, last_sent, total_count in rows:
        # ponytail: dashboard runs in a separate process from init(), so it
        # doesn't know the app's actual rate_limit_seconds -- uses the
        # library default. Upgrade to persisting the configured value if
        # apps commonly override it.
        in_window = last_sent is not None and now - last_sent < _DEFAULT_RATE_LIMIT_SECONDS
        if in_window:
            limited_count += 1
            status = style.red(f"{dot_char} limited")
        else:
            status = style.green(f"{dot_char} sending")

        row = (
            f"  {style.dim(f'{fingerprint[:8]:<8}')}  "
            f"{exc_type:<{type_width}}  "
            f"{_truncate(location, _LOCATION_WIDTH, ellipsis):<{_LOCATION_WIDTH}}  "
            f"{_relative_time(last_seen, now):<10}"
            f"{total_count:>6}   {status}"
        )
        print(row)

    plural = "" if len(rows) == 1 else "s"
    print(f"\n{len(rows)} error group{plural}, {limited_count} currently rate-limited.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devalerts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("dashboard", help="Show grouped/rate-limited errors")
    args = parser.parse_args(argv)

    if args.command == "dashboard":
        return _dashboard()
    return 1


if __name__ == "__main__":
    sys.exit(main())
