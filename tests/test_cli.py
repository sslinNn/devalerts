import time

import pytest

from devalerts import _store, cli


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(_store, "_DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(cli, "_DB_PATH", tmp_path / "state.db")


def test_truncate_keeps_text_under_width_unchanged():
    assert cli._truncate("short", 10, "...") == "short"


def test_truncate_keeps_tail_of_long_text():
    result = cli._truncate("/very/long/path/to/app.py:42", 15, "...")
    assert len(result) == 15
    assert result.endswith("app.py:42")
    assert result.startswith("...")


def test_relative_time_buckets():
    now = 1_000_000.0
    assert cli._relative_time(now - 30, now) == "just now"
    assert cli._relative_time(now - 120, now) == "2m ago"
    assert cli._relative_time(now - 7200, now) == "2h ago"
    assert cli._relative_time(now - 172800, now) == "2d ago"


def test_dashboard_reports_no_errors_when_db_missing(capsys):
    assert cli._dashboard() == 0
    assert "No errors recorded yet." in capsys.readouterr().out


def test_dashboard_lists_recorded_error_group(capsys):
    _store._should_send("fp", "ValueError", "app.py:1", rate_limit_seconds=300)

    assert cli._dashboard() == 0
    output = capsys.readouterr().out
    assert "ValueError" in output
    assert "app.py:1" in output
    assert "1 error group, " in output


def test_main_dashboard_subcommand(capsys):
    assert cli.main(["dashboard"]) == 0
