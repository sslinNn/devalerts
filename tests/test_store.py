import sqlite3
import time

import pytest

from devalerts import _store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(_store, "_DB_PATH", tmp_path / "state.db")


def _tb():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        return exc.__traceback__


def test_fingerprint_deterministic_for_same_type_and_location():
    tb = _tb()
    fp1, loc1 = _store._fingerprint(ValueError, tb)
    fp2, loc2 = _store._fingerprint(ValueError, tb)
    assert fp1 == fp2
    assert loc1 == loc2
    assert "test_store.py:" in loc1


def test_fingerprint_differs_for_different_exception_type():
    tb = _tb()
    fp_value_error, _ = _store._fingerprint(ValueError, tb)
    fp_type_error, _ = _store._fingerprint(TypeError, tb)
    assert fp_value_error != fp_type_error


def test_should_send_first_occurrence_sends_immediately():
    send, skipped, is_new = _store._should_send("fp1", "ValueError", "app.py:1", 300)
    assert (send, skipped, is_new) == (True, 0, True)


def test_should_send_suppresses_within_rate_limit_window():
    _store._should_send("fp2", "ValueError", "app.py:1", 300)
    send, skipped, is_new = _store._should_send("fp2", "ValueError", "app.py:1", 300)
    assert (send, skipped, is_new) == (False, 0, False)


def test_should_send_reports_skipped_count_on_resend():
    fingerprint = "fp3"
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 300) == (
        True,
        0,
        True,
    )
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 300) == (
        False,
        0,
        False,
    )
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 300) == (
        False,
        0,
        False,
    )
    # rate_limit_seconds=0 means "always past the window" -> resends and reports
    # how many occurrences were swallowed since the last alert actually went out.
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 0) == (
        True,
        2,
        False,
    )


def test_should_send_tracks_fingerprints_independently():
    assert _store._should_send("fp4", "ValueError", "a.py:1", 300) == (True, 0, True)
    assert _store._should_send("fp5", "ValueError", "b.py:2", 300) == (True, 0, True)


def test_should_send_reports_is_new_false_on_second_occurrence():
    fingerprint = "fp3b"
    _store._should_send(fingerprint, "ValueError", "app.py:1", 0)
    _, _, is_new = _store._should_send(fingerprint, "ValueError", "app.py:1", 0)
    assert is_new is False


def test_should_send_fails_open_on_db_error(monkeypatch, capsys):
    def _broken_connection():
        raise sqlite3.Error("disk full")

    monkeypatch.setattr(_store, "_get_connection", _broken_connection)
    send, skipped, is_new = _store._should_send("fp6", "ValueError", "app.py:1", 300)
    assert (send, skipped, is_new) == (True, 0, True)
    assert "dedup/rate-limit state error" in capsys.readouterr().err


def test_should_send_persists_rate_limit_seconds():
    _store._should_send("fp7", "ValueError", "app.py:1", 120)
    conn = _store._get_connection()
    row = conn.execute(
        "SELECT rate_limit_seconds FROM error_groups WHERE fingerprint = ?", ("fp7",)
    ).fetchone()
    conn.close()
    assert row[0] == 120


def test_muted_group_never_sends_but_keeps_counting():
    _store._should_send("fp8", "ValueError", "app.py:1", 0)
    _store._set_muted("fp8", True)
    assert _store._should_send("fp8", "ValueError", "app.py:1", 0) == (False, 0, False)
    assert _store._should_send("fp8", "ValueError", "app.py:1", 0) == (False, 0, False)


def test_unmute_resends_and_reports_accumulated_skip_count():
    _store._should_send("fp9", "ValueError", "app.py:1", 0)
    _store._set_muted("fp9", True)
    _store._should_send("fp9", "ValueError", "app.py:1", 0)
    _store._should_send("fp9", "ValueError", "app.py:1", 0)
    _store._set_muted("fp9", False)
    assert _store._should_send("fp9", "ValueError", "app.py:1", 0) == (True, 2, False)


def test_match_fingerprints_by_prefix():
    _store._should_send("abc123", "ValueError", "app.py:1", 300)
    _store._should_send("abc999", "ValueError", "app.py:1", 300)
    _store._should_send("xyz000", "ValueError", "app.py:1", 300)
    assert sorted(_store._match_fingerprints("abc")) == ["abc123", "abc999"]
    assert _store._match_fingerprints("abc123") == ["abc123"]
    assert _store._match_fingerprints("nope") == []


def test_clear_removes_single_group():
    _store._should_send("fp10", "ValueError", "app.py:1", 300)
    _store._should_send("fp11", "ValueError", "app.py:1", 300)
    _store._clear("fp10")
    assert _store._match_fingerprints("fp10") == []
    assert _store._match_fingerprints("fp11") == ["fp11"]


def test_clear_all_removes_every_group():
    _store._should_send("fp12", "ValueError", "app.py:1", 300)
    _store._should_send("fp13", "ValueError", "app.py:1", 300)
    _store._clear_all()
    assert _store._match_fingerprints("fp") == []


def test_backoff_multiplier_doubles_on_chronic_resend():
    conn = _store._get_connection()
    assert _store._should_send("fpB", "ValueError", "app.py:1", 100) == (True, 0, True)
    assert _store._should_send("fpB", "ValueError", "app.py:1", 100) == (
        False,
        0,
        False,
    )
    assert _store._should_send("fpB", "ValueError", "app.py:1", 100) == (
        False,
        0,
        False,
    )
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 150, "fpB"),
    )
    conn.commit()

    # Two occurrences piled up while suppressed -- chronic, multiplier doubles.
    assert _store._should_send("fpB", "ValueError", "app.py:1", 100) == (
        True,
        2,
        False,
    )
    row = conn.execute(
        "SELECT backoff_multiplier FROM error_groups WHERE fingerprint = ?", ("fpB",)
    ).fetchone()
    assert row[0] == 2
    conn.close()


def test_backoff_multiplier_extends_effective_window():
    conn = _store._get_connection()
    assert _store._should_send("fpC", "ValueError", "app.py:1", 100) == (True, 0, True)
    assert _store._should_send("fpC", "ValueError", "app.py:1", 100) == (
        False,
        0,
        False,
    )
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 150, "fpC"),
    )
    conn.commit()
    # Chronic resend: multiplier becomes 2, effective window now 200s.
    assert _store._should_send("fpC", "ValueError", "app.py:1", 100) == (
        True,
        1,
        False,
    )

    # 150s later would have passed the *base* 100s window but not the doubled 200s one.
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 150, "fpC"),
    )
    conn.commit()
    assert _store._should_send("fpC", "ValueError", "app.py:1", 100) == (
        False,
        0,
        False,
    )
    conn.close()


def test_backoff_multiplier_resets_after_quiet_spell():
    conn = _store._get_connection()
    assert _store._should_send("fpD", "ValueError", "app.py:1", 100) == (True, 0, True)
    assert _store._should_send("fpD", "ValueError", "app.py:1", 100) == (
        False,
        0,
        False,
    )
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 150, "fpD"),
    )
    conn.commit()
    assert _store._should_send("fpD", "ValueError", "app.py:1", 100) == (
        True,
        1,
        False,
    )
    row = conn.execute(
        "SELECT backoff_multiplier FROM error_groups WHERE fingerprint = ?", ("fpD",)
    ).fetchone()
    assert row[0] == 2

    # Long quiet spell, then a single fresh occurrence -- no pile-up this time.
    conn.execute(
        "UPDATE error_groups SET last_sent = ? WHERE fingerprint = ?",
        (time.time() - 1000, "fpD"),
    )
    conn.commit()
    assert _store._should_send("fpD", "ValueError", "app.py:1", 100) == (
        True,
        0,
        False,
    )
    row = conn.execute(
        "SELECT backoff_multiplier FROM error_groups WHERE fingerprint = ?", ("fpD",)
    ).fetchone()
    assert row[0] == 1
    conn.close()


def test_backoff_multiplier_caps_at_max():
    conn = _store._get_connection()
    now = time.time()
    conn.execute(
        """
        INSERT INTO error_groups
            (fingerprint, exc_type, location, first_seen, last_seen, last_sent,
             count_since_last_sent, total_count, rate_limit_seconds, backoff_multiplier)
        VALUES ('fpE', 'ValueError', 'app.py:1', ?, ?, ?, 1, 2, 1, 4)
        """,
        (now, now, now - 1000),
    )
    conn.commit()
    conn.close()

    assert _store._should_send("fpE", "ValueError", "app.py:1", 1) == (True, 1, False)
    conn = _store._get_connection()
    row = conn.execute(
        "SELECT backoff_multiplier FROM error_groups WHERE fingerprint = ?", ("fpE",)
    ).fetchone()
    assert row[0] == _store._MAX_BACKOFF_MULTIPLIER  # min(4*2, 8) == 8
    conn.close()


def test_last_incident_returns_none_when_no_groups_recorded():
    assert _store._last_incident() is None


def test_last_incident_returns_max_last_seen_across_groups():
    _store._should_send("fpS1", "ValueError", "app.py:1", 300)
    _store._should_send("fpS2", "TypeError", "app.py:2", 300)
    conn = _store._get_connection()
    conn.execute(
        "UPDATE error_groups SET last_seen = ? WHERE fingerprint = ?",
        (time.time() - 1000, "fpS1"),
    )
    conn.commit()
    newer = conn.execute(
        "SELECT last_seen FROM error_groups WHERE fingerprint = 'fpS2'"
    ).fetchone()[0]
    conn.close()

    assert _store._last_incident() == newer


def test_migration_adds_columns_to_pre_existing_db():
    conn = sqlite3.connect(_store._DB_PATH)
    conn.execute(
        """
        CREATE TABLE error_groups (
            fingerprint TEXT PRIMARY KEY,
            exc_type TEXT NOT NULL,
            location TEXT NOT NULL,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            last_sent REAL,
            count_since_last_sent INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()

    send, skipped, is_new = _store._should_send("fp14", "ValueError", "app.py:1", 300)
    assert (send, skipped, is_new) == (True, 0, True)
