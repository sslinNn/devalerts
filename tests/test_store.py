import sqlite3

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
    send, skipped = _store._should_send("fp1", "ValueError", "app.py:1", 300)
    assert (send, skipped) == (True, 0)


def test_should_send_suppresses_within_rate_limit_window():
    _store._should_send("fp2", "ValueError", "app.py:1", 300)
    send, skipped = _store._should_send("fp2", "ValueError", "app.py:1", 300)
    assert (send, skipped) == (False, 0)


def test_should_send_reports_skipped_count_on_resend():
    fingerprint = "fp3"
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 300) == (True, 0)
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 300) == (False, 0)
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 300) == (False, 0)
    # rate_limit_seconds=0 means "always past the window" -> resends and reports
    # how many occurrences were swallowed since the last alert actually went out.
    assert _store._should_send(fingerprint, "ValueError", "app.py:1", 0) == (True, 2)


def test_should_send_tracks_fingerprints_independently():
    assert _store._should_send("fp4", "ValueError", "a.py:1", 300) == (True, 0)
    assert _store._should_send("fp5", "ValueError", "b.py:2", 300) == (True, 0)


def test_should_send_fails_open_on_db_error(monkeypatch, capsys):
    def _broken_connection():
        raise sqlite3.Error("disk full")

    monkeypatch.setattr(_store, "_get_connection", _broken_connection)
    send, skipped = _store._should_send("fp6", "ValueError", "app.py:1", 300)
    assert (send, skipped) == (True, 0)
    assert "dedup/rate-limit state error" in capsys.readouterr().err
