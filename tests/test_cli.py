import sqlite3
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


def test_dashboard_reports_no_errors_when_db_exists_but_empty(capsys):
    _store._get_connection().close()  # creates the table, inserts nothing

    assert cli._dashboard() == 0
    assert "No errors recorded yet." in capsys.readouterr().out


def test_dashboard_shows_sending_when_outside_rate_limit_window(capsys):
    _store._should_send("fp", "TimeoutError", "app.py:1", rate_limit_seconds=300)
    conn = sqlite3.connect(cli._DB_PATH)
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 400, "fp"),
    )
    conn.commit()
    conn.close()

    assert cli._dashboard() == 0
    output = capsys.readouterr().out
    assert "sending" in output
    assert "0 currently rate-limited" in output


def test_main_dashboard_subcommand(capsys):
    assert cli.main(["dashboard"]) == 0


def test_test_command_success(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_send_telegram_message", lambda token, chat_id, text: True
    )

    exit_code = cli.main(["test", "--bot-token", "TOKEN", "--chat-id", "123"])

    assert exit_code == 0
    assert "Test message sent" in capsys.readouterr().out


def test_test_command_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_send_telegram_message", lambda token, chat_id, text: False
    )

    exit_code = cli.main(["test", "--bot-token", "TOKEN", "--chat-id", "123"])

    assert exit_code == 1
    assert "Failed to send test message" in capsys.readouterr().err
