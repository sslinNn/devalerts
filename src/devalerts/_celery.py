"""Optional Celery integration: report task failures that Celery's own trace
machinery swallows before they ever reach sys.excepthook -- the same problem
ASGIMiddleware solves for FastAPI/Starlette request errors."""

from __future__ import annotations

_connected = False


def init_celery() -> None:
    """Report Celery task failures to Telegram. Call in addition to init(),
    which never sees exceptions raised inside a task -- Celery catches those
    itself to record the task's FAILURE state.

    Usage::

        devalerts.init(bot_token="...", chat_id=123456789)
        devalerts.init_celery()

    Requires Celery to already be installed in the worker process (not a
    devalerts dependency -- imported lazily here).
    """
    global _connected
    if _connected:
        return
    from celery.signals import task_failure  # type: ignore[import-untyped]

    task_failure.connect(_on_task_failure, weak=False)
    _connected = True


def _on_task_failure(
    sender=None, task_id=None, exception=None, traceback=None, **_kwargs
) -> None:
    from . import _send_exception

    if exception is None:
        return
    tags: dict[str, str] = {}
    if task_id is not None:
        tags["task_id"] = str(task_id)
    if sender is not None:
        tags["task"] = getattr(sender, "name", type(sender).__name__)
    exc = exception.with_traceback(traceback) if traceback is not None else exception
    _send_exception(type(exc), exc, exc.__traceback__, extra=tags)
