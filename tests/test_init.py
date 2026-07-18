import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

import devalerts
from devalerts import _store


def _module_level_job():
    raise ValueError("in decorator")


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


def test_capture_as_decorator_auto_tags_job_name(isolated):
    decorated = devalerts.capture()(_module_level_job)
    with pytest.raises(ValueError):
        decorated()
    assert "job=_module_level_job" in isolated[0]


def test_capture_context_manager_has_no_job_tag(isolated):
    with pytest.raises(ValueError):
        with devalerts.capture():
            raise ValueError("in context")
    assert "job=" not in isolated[0]


def test_capture_extra_overrides_auto_job_name(isolated):
    @devalerts.capture(extra={"job": "custom-name"})
    def nightly_sync():
        raise ValueError("in decorator")

    with pytest.raises(ValueError):
        nightly_sync()
    assert "job=custom-name" in isolated[0]
    assert "job=nightly_sync" not in isolated[0]


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


def test_report_passes_extra_tags_into_message(isolated):
    devalerts.report(ValueError("with extra"), extra={"request_id": "abc"})
    assert "request_id=abc" in isolated[0]


def test_capture_passes_extra_tags_into_message(isolated):
    with pytest.raises(ValueError):
        with devalerts.capture(extra={"job": "nightly"}):
            raise ValueError("in context")
    assert "job=nightly" in isolated[0]


def test_init_tags_included_in_every_alert(isolated, monkeypatch):
    monkeypatch.setitem(devalerts._state, "tags", {"env": "production"})
    devalerts.report(ValueError("boom"))
    assert "env=production" in isolated[0]


def test_report_extra_overrides_init_tags_on_key_collision(isolated, monkeypatch):
    monkeypatch.setitem(devalerts._state, "tags", {"env": "production"})
    devalerts.report(ValueError("boom"), extra={"env": "staging"})
    assert "env=staging" in isolated[0]
    assert "env=production" not in isolated[0]


def test_init_stores_tags():
    devalerts.init("token", 1, tags={"env": "production"})
    assert devalerts._state["tags"] == {"env": "production"}


def test_init_defaults_tags_to_empty_dict():
    devalerts.init("token", 1)
    assert devalerts._state["tags"] == {}


def test_init_defaults_blame_to_false():
    devalerts.init("token", 1)
    assert devalerts._state["blame"] is False


def test_init_stores_blame():
    devalerts.init("token", 1, blame=True)
    assert devalerts._state["blame"] is True
    # init() mutates module-level state directly (not via monkeypatch) --
    # reset it so later tests don't inherit blame=True and spawn real `git
    # blame` subprocesses against this repo's own tracked test files.
    devalerts._state["blame"] = False


def test_report_skips_git_blame_lookup_by_default(isolated, monkeypatch):
    monkeypatch.setitem(devalerts._state, "blame", False)
    calls = []
    monkeypatch.setattr(
        devalerts, "_git_blame_for_traceback", lambda tb: calls.append(tb) or "x"
    )
    devalerts.report(ValueError("boom"))
    assert calls == []
    assert "blame" not in isolated[0]


def test_report_marks_first_occurrence_as_new(isolated):
    devalerts.report(ValueError("brand new"))
    assert "New error" in isolated[0]


def test_report_does_not_mark_repeat_occurrence_as_new(isolated, monkeypatch):
    monkeypatch.setitem(devalerts._state, "rate_limit_seconds", 0)

    def _boom():
        raise ValueError("seen before")

    try:
        _boom()
    except ValueError as exc:
        devalerts.report(exc)
    try:
        _boom()
    except ValueError as exc:
        devalerts.report(exc)

    assert "New error" in isolated[0]
    assert "New error" not in isolated[1]


def test_report_includes_blame_when_enabled(isolated, monkeypatch):
    monkeypatch.setitem(devalerts._state, "blame", True)
    monkeypatch.setattr(
        devalerts,
        "_git_blame_for_traceback",
        lambda tb: "sslinNn · a1b2c3d · 2026-07-15 (3d ago)",
    )
    devalerts.report(ValueError("boom"))
    assert "blame: sslinNn · a1b2c3d · 2026-07-15 (3d ago)" in isolated[0]


def test_init_twice_does_not_chain_to_own_excepthook():
    original_hook = sys.excepthook
    devalerts.init("token", 1)
    devalerts.init("token", 1)
    assert devalerts._state["prev_excepthook"] is original_hook


def test_init_stores_slack_webhook_url():
    devalerts.init(slack_webhook_url="https://hooks.slack.com/services/x")
    assert devalerts._state["slack_webhook_url"] == "https://hooks.slack.com/services/x"
    assert devalerts._state["bot_token"] is None
    # init() mutates module-level state directly (not via monkeypatch) --
    # reset it so later tests (in this file and others) don't inherit a
    # live slack_webhook_url and make real HTTP calls on every alert.
    devalerts._state["slack_webhook_url"] = None


def test_init_rejects_bot_token_without_chat_id():
    with pytest.raises(ValueError, match="together"):
        devalerts.init(bot_token="token")


def test_init_rejects_chat_id_without_bot_token():
    with pytest.raises(ValueError, match="together"):
        devalerts.init(chat_id=1)


def test_init_rejects_no_channel_configured():
    with pytest.raises(ValueError, match="requires"):
        devalerts.init()


def test_report_delivers_to_both_telegram_and_slack(isolated, monkeypatch):
    monkeypatch.setitem(
        devalerts._state, "slack_webhook_url", "https://hooks.slack.com/services/x"
    )
    slack_sent = []
    monkeypatch.setattr(
        devalerts,
        "_send_slack_message",
        lambda webhook_url, text: slack_sent.append(text),
    )

    devalerts.report(ValueError("dual channel"))

    assert len(isolated) == 1
    assert "ValueError: dual channel" in isolated[0]
    assert len(slack_sent) == 1
    assert "ValueError: dual channel" in slack_sent[0]
    assert "*🔴 ValueError: dual channel*" in slack_sent[0]


def test_report_delivers_to_slack_only_when_telegram_not_configured(
    isolated, monkeypatch
):
    monkeypatch.setitem(devalerts._state, "bot_token", None)
    monkeypatch.setitem(devalerts._state, "chat_id", None)
    monkeypatch.setitem(
        devalerts._state, "slack_webhook_url", "https://hooks.slack.com/services/x"
    )
    slack_sent = []
    monkeypatch.setattr(
        devalerts,
        "_send_slack_message",
        lambda webhook_url, text: slack_sent.append(text),
    )

    devalerts.report(ValueError("slack only"))

    assert isolated == []
    assert len(slack_sent) == 1


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
