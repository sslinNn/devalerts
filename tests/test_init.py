import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

import devalerts
from devalerts import _store


class _SyncThread:
    """Runs the target immediately instead of on a real thread, so tests
    don't have to race the ASGI middleware's fire-and-forget reporting."""

    def __init__(self, target=None, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


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


def test_asgi_middleware_passes_through_successful_requests(isolated):
    async def app(scope, receive, send):
        return "ok"

    async def run():
        return await devalerts.ASGIMiddleware(app)({"type": "http"}, None, None)

    asyncio.run(run())
    assert isolated == []


def test_asgi_middleware_reports_and_reraises(isolated, monkeypatch):
    monkeypatch.setattr(devalerts.threading, "Thread", _SyncThread)

    async def app(scope, receive, send):
        raise ValueError("request blew up")

    async def run():
        await devalerts.ASGIMiddleware(app)({"type": "http"}, None, None)

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert len(isolated) == 1
    assert "ValueError: request blew up" in isolated[0]


def test_excepthook_sends_then_chains_to_previous_hook(monkeypatch):
    calls = []
    monkeypatch.setitem(devalerts._state, "prev_excepthook", lambda *a: calls.append(a))
    monkeypatch.setattr(devalerts, "_send_exception", lambda *a: calls.append("sent"))

    exc = ValueError("boom")
    devalerts._excepthook(ValueError, exc, None)

    assert calls == ["sent", (ValueError, exc, None)]


def test_excepthook_skips_send_for_keyboard_interrupt_but_still_chains(monkeypatch):
    calls = []
    monkeypatch.setitem(devalerts._state, "prev_excepthook", lambda *a: calls.append(a))
    monkeypatch.setattr(devalerts, "_send_exception", lambda *a: calls.append("sent"))

    exc = KeyboardInterrupt()
    devalerts._excepthook(KeyboardInterrupt, exc, None)

    assert calls == [(KeyboardInterrupt, exc, None)]


def test_excepthook_never_raises_even_if_send_exception_breaks(monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(
        devalerts._state, "prev_excepthook", lambda *a: calls.append("prev")
    )

    def _broken(*a):
        raise RuntimeError("internal bug")

    monkeypatch.setattr(devalerts, "_send_exception", _broken)

    devalerts._excepthook(ValueError, ValueError("x"), None)  # must not raise

    assert calls == ["prev"]
    assert "internal error while sending alert" in capsys.readouterr().err


def test_threading_excepthook_sends_then_chains_to_previous_hook(monkeypatch):
    calls = []
    monkeypatch.setitem(
        devalerts._state, "prev_threading_excepthook", lambda a: calls.append(a)
    )
    monkeypatch.setattr(devalerts, "_send_exception", lambda *a: calls.append("sent"))

    args = SimpleNamespace(
        exc_type=ValueError, exc_value=ValueError("boom"), exc_traceback=None
    )
    devalerts._threading_excepthook(args)

    assert calls == ["sent", args]


def test_threading_excepthook_never_raises_even_if_send_exception_breaks(
    monkeypatch, capsys
):
    calls = []
    monkeypatch.setitem(
        devalerts._state, "prev_threading_excepthook", lambda a: calls.append("prev")
    )

    def _broken(*a):
        raise RuntimeError("internal bug")

    monkeypatch.setattr(devalerts, "_send_exception", _broken)

    args = SimpleNamespace(
        exc_type=ValueError, exc_value=ValueError("x"), exc_traceback=None
    )
    devalerts._threading_excepthook(args)  # must not raise

    assert calls == ["prev"]
    assert "internal error while sending alert" in capsys.readouterr().err
