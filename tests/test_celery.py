import pytest
from celery.signals import task_failure

import devalerts
from devalerts import _celery, _store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_store, "_DB_PATH", tmp_path / "state.db")
    monkeypatch.setitem(devalerts._state, "bot_token", "test-token")
    monkeypatch.setitem(devalerts._state, "chat_id", 12345)
    monkeypatch.setattr(_celery, "_connected", False)
    sent = []
    monkeypatch.setattr(
        devalerts,
        "_send_telegram_message",
        lambda token, chat_id, text: sent.append(text),
    )
    yield sent
    task_failure.disconnect(_celery._on_task_failure)


def _make_tb():
    try:
        raise ValueError("task blew up")
    except ValueError as exc:
        return exc.__traceback__


class _FakeTask:
    name = "myapp.tasks.do_thing"


def test_init_celery_connects_signal_and_reports_task_failure(isolated):
    devalerts.init_celery()

    task_failure.send(
        sender=_FakeTask(),
        task_id="abc-123",
        exception=ValueError("task blew up"),
        traceback=_make_tb(),
        args=(),
        kwargs={},
        einfo=None,
    )

    assert len(isolated) == 1
    assert "ValueError: task blew up" in isolated[0]
    assert "task=myapp.tasks.do_thing" in isolated[0]
    assert "task_id=abc-123" in isolated[0]


def test_init_celery_twice_does_not_double_report(isolated):
    devalerts.init_celery()
    devalerts.init_celery()

    task_failure.send(
        sender=_FakeTask(),
        task_id="abc-123",
        exception=ValueError("task blew up"),
        traceback=_make_tb(),
        args=(),
        kwargs={},
        einfo=None,
    )

    assert len(isolated) == 1


def test_task_failure_without_exception_is_ignored(isolated):
    devalerts.init_celery()

    task_failure.send(
        sender=_FakeTask(), task_id="abc-123", exception=None, traceback=None
    )

    assert isolated == []
