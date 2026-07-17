"""CLI: `devalerts dashboard` reports grouped/rate-limited errors from the local state DB."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime

from . import _DB_PATH, _DEFAULT_RATE_LIMIT_SECONDS


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

    now = time.time()
    print(f"{'FINGERPRINT':<18}{'TYPE':<16}{'LOCATION':<30}{'LAST SEEN':<21}{'TOTAL':>7}  IN WINDOW")
    for fingerprint, exc_type, location, last_seen, last_sent, total_count in rows:
        last_seen_str = datetime.fromtimestamp(last_seen).strftime("%Y-%m-%d %H:%M:%S")
        # ponytail: the dashboard runs in a separate process from init(), so it
        # doesn't know the app's actual rate_limit_seconds -- uses the library
        # default. Upgrade to persisting the configured value if apps commonly
        # override it.
        in_window = last_sent is not None and now - last_sent < _DEFAULT_RATE_LIMIT_SECONDS
        print(
            f"{fingerprint:<18}{exc_type:<16}{location:<30}{last_seen_str:<21}"
            f"{total_count:>7}  {'yes' if in_window else 'no'}"
        )
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
