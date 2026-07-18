"""Best-effort ``git blame`` lookup for the line that raised. Never raises --
returns None on any failure (no git installed, not a repo, uncommitted line,
timeout, ...)."""

from __future__ import annotations

import subprocess
import time
import traceback
from pathlib import Path
from types import TracebackType

_TIMEOUT_SECONDS = 2


def _relative_time(seconds_ago: float) -> str:
    seconds_ago = max(seconds_ago, 0)
    minutes = seconds_ago / 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    return f"{int(months / 12)}y ago"


def _git_blame(filename: str, lineno: int) -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(filename).parent),
                "blame",
                "-L",
                f"{lineno},{lineno}",
                "--porcelain",
                "--",
                filename,
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        lines = result.stdout.splitlines()
        sha = lines[0].split()[0]
        author: str | None = None
        author_time: int | None = None
        for line in lines[1:]:
            if line.startswith("author "):
                author = line[len("author ") :]
            elif line.startswith("author-time "):
                author_time = int(line[len("author-time ") :])
            elif line.startswith("\t"):
                break
        if not author or author_time is None or author == "Not Committed Yet":
            return None
        date = time.strftime("%Y-%m-%d", time.localtime(author_time))
        age = _relative_time(time.time() - author_time)
        return f"{author} · {sha[:7]} · {date} ({age})"
    except Exception:  # noqa: BLE001 - best-effort, must never raise
        return None


def _git_blame_for_traceback(tb: TracebackType | None) -> str | None:
    frames = traceback.extract_tb(tb)
    if not frames:
        return None
    lineno = frames[-1].lineno
    if lineno is None:
        return None
    return _git_blame(frames[-1].filename, lineno)
