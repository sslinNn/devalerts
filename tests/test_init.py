import sys
import threading

import pytest

import devalerts
from devalerts import _store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_store, "_DB_PATH", tmp_path / "state.db")
    monkeypatch.setitem(devalerts._state, "bot_token", "test-token")
    monkeypatch.setitem(devalerts._state, "chat_id", 12345)
    sent = []
    monkeypatch.setattr(
        devalerts,
        "_send_telegram_message",
        lambda token, chat_id, text: sent.append(text),
    )
    original_excepthook = sys.excepthook
    original_threading_excepthook = threading.excepthook
    yield sent
    sys.excepthook = original_excepthook
    threading.excepthook = original_threading_excepthook
    devalerts._state["prev_excepthook"] = original_excepthook
    devalerts._state["prev_threading_excepthook"] = original_threading_excepthook


def test_report_sends_active_exception(isolated):
    try:
        raise ValueError("boom")
    except ValueError:
        devalerts.report()
    assert len(isolated) == 1
    assert "ValueError: boom" in isolated[0]


def test_report_sends_explicit_exception(isolated):
    devalerts.report(ValueError("explicit"))
    assert "ValueError: explicit" in isolated[0]


def test_report_without_active_exception_raises():
    with pytest.raises(RuntimeError):
        devalerts.report()


def test_capture_context_manager_reports_and_reraises(isolated):
    with pytest.raises(ValueError):
        with devalerts.capture():
            raise ValueError("in context")
    assert len(isolated) == 1


def test_capture_as_decorator(isolated):
    @devalerts.capture()
    def boom():
        raise ValueError("in decorator")

    with pytest.raises(ValueError):
        boom()
    assert len(isolated) == 1


def test_capture_does_nothing_on_success(isolated):
    with devalerts.capture():
        pass
    assert isolated == []


def test_report_drops_alert_when_init_never_called(isolated, monkeypatch, capsys):
    monkeypatch.setitem(devalerts._state, "bot_token", None)
    monkeypatch.setitem(devalerts._state, "chat_id", None)
    devalerts.report(ValueError("no init"))
    assert isolated == []
    assert "init() was not called" in capsys.readouterr().err


def test_init_twice_does_not_chain_to_own_excepthook():
    original_hook = sys.excepthook
    devalerts.init("token", 1)
    devalerts.init("token", 1)
    assert devalerts._state["prev_excepthook"] is original_hook
