import os
import subprocess
import time

import pytest

from devalerts._blame import _git_blame, _git_blame_for_traceback, _relative_time

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _run_git(*args, cwd):
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )


@pytest.fixture
def git_repo(tmp_path):
    _run_git("init", "-q", cwd=tmp_path)
    _run_git("config", "user.name", "Test Author", cwd=tmp_path)
    _run_git("config", "user.email", "test@example.com", cwd=tmp_path)
    return tmp_path


def _write_and_commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _run_git("add", filename, cwd=repo)
    _run_git("commit", "-q", "-m", message, cwd=repo)


def test_blame_returns_none_outside_a_git_repo(tmp_path):
    file = tmp_path / "not_a_repo.py"
    file.write_text("raise ValueError('x')\n", encoding="utf-8")

    assert _git_blame(str(file), 1) is None


def test_blame_returns_author_commit_and_date_for_committed_line(git_repo):
    _write_and_commit(
        git_repo, "app.py", "def boom():\n    raise ValueError('x')\n", "add boom"
    )

    result = _git_blame(str(git_repo / "app.py"), 2)

    assert result is not None
    assert "Test Author" in result
    assert time.strftime("%Y-%m-%d") in result
    assert "just now" in result


def test_blame_returns_none_for_uncommitted_line(git_repo):
    _write_and_commit(git_repo, "app.py", "def boom():\n    pass\n", "initial")
    (git_repo / "app.py").write_text(
        "def boom():\n    raise ValueError('x')\n", encoding="utf-8"
    )

    assert _git_blame(str(git_repo / "app.py"), 2) is None


def test_blame_returns_none_for_nonexistent_git_binary(monkeypatch, git_repo):
    _write_and_commit(git_repo, "app.py", "x = 1\n", "initial")
    monkeypatch.setattr(
        "devalerts._blame.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert _git_blame(str(git_repo / "app.py"), 1) is None


def test_relative_time_thresholds():
    assert _relative_time(30) == "just now"
    assert _relative_time(5 * 60) == "5m ago"
    assert _relative_time(3 * 3600) == "3h ago"
    assert _relative_time(2 * 86400) == "2d ago"
    assert _relative_time(60 * 86400) == "2mo ago"
    assert _relative_time(400 * 86400) == "1y ago"


def test_blame_for_traceback_uses_last_frame(git_repo, monkeypatch):
    _write_and_commit(git_repo, "app.py", "raise ValueError('x')\n", "initial")
    calls = []
    monkeypatch.setattr(
        "devalerts._blame._git_blame",
        lambda filename, lineno: calls.append((filename, lineno)) or "mocked",
    )

    try:
        exec(compile("raise ValueError('boom')", str(git_repo / "app.py"), "exec"))
    except ValueError as exc:
        tb = exc.__traceback__

    assert _git_blame_for_traceback(tb) == "mocked"
    assert calls == [(str(git_repo / "app.py"), 1)]


def test_blame_for_traceback_returns_none_without_frames():
    assert _git_blame_for_traceback(None) is None
