import json
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


def test_version_flag_prints_installed_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])

    assert exc_info.value.code == 0
    assert "devalerts" in capsys.readouterr().out


def test_dashboard_uses_configured_rate_limit_not_default(capsys):
    _store._should_send("fp", "ValueError", "app.py:1", rate_limit_seconds=30)
    conn = sqlite3.connect(cli._DB_PATH)
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 60, "fp"),
    )
    conn.commit()
    conn.close()

    # 60s have passed: past the configured 30s limit, still inside the 300s default.
    assert cli._dashboard() == 0
    assert "sending" in capsys.readouterr().out


def test_dashboard_json_output(capsys):
    _store._should_send("fp", "ValueError", "app.py:1", rate_limit_seconds=300)

    assert cli._dashboard(as_json=True) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == [
        {
            "fingerprint": "fp",
            "exc_type": "ValueError",
            "location": "app.py:1",
            "last_seen": data[0]["last_seen"],
            "last_sent": data[0]["last_sent"],
            "total_count": 1,
            "count_since_last_sent": 0,
            "rate_limited": True,
            "muted": False,
        }
    ]


def test_dashboard_json_empty(capsys):
    assert cli._dashboard(as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_main_dashboard_json_flag(capsys):
    assert cli.main(["dashboard", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_mute_then_dashboard_shows_muted(capsys):
    _store._should_send("abcdef1234", "ValueError", "app.py:1", rate_limit_seconds=300)

    assert cli.main(["mute", "abcdef"]) == 0
    assert "Muted abcdef12." in capsys.readouterr().out

    assert cli._dashboard() == 0
    output = capsys.readouterr().out
    assert "muted" in output
    assert "1 muted." in output


def test_unmute_restores_normal_status(capsys):
    _store._should_send("abcdef1234", "ValueError", "app.py:1", rate_limit_seconds=300)
    cli.main(["mute", "abcdef"])
    capsys.readouterr()

    assert cli.main(["unmute", "abcdef"]) == 0
    assert "Unmuted abcdef12." in capsys.readouterr().out


def test_mute_unknown_fingerprint_fails(capsys):
    exit_code = cli.main(["mute", "nosuchfp"])
    assert exit_code == 1
    assert "No error group matches" in capsys.readouterr().err


def test_mute_ambiguous_prefix_fails(capsys):
    _store._should_send("abc111", "ValueError", "app.py:1", rate_limit_seconds=300)
    _store._should_send("abc222", "ValueError", "app.py:1", rate_limit_seconds=300)

    exit_code = cli.main(["mute", "abc"])
    assert exit_code == 1
    assert "matches 2 error groups" in capsys.readouterr().err


def test_clear_removes_single_group(capsys):
    _store._should_send("fp", "ValueError", "app.py:1", rate_limit_seconds=300)

    assert cli.main(["clear", "fp"]) == 0
    assert "Cleared fp." in capsys.readouterr().out
    assert _store._match_fingerprints("fp") == []


def test_clear_all_removes_every_group(capsys):
    _store._should_send("fp1", "ValueError", "app.py:1", rate_limit_seconds=300)
    _store._should_send("fp2", "ValueError", "app.py:1", rate_limit_seconds=300)

    assert cli.main(["clear", "--all"]) == 0
    assert "Cleared all error groups." in capsys.readouterr().out
    assert _store._match_fingerprints("fp") == []


def test_clear_unknown_fingerprint_fails(capsys):
    exit_code = cli.main(["clear", "nosuchfp"])
    assert exit_code == 1
    assert "No error group matches" in capsys.readouterr().err
