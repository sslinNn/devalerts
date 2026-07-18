"""Send unhandled Python exceptions straight to a Telegram chat."""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from types import TracebackType
from typing import Callable, Literal, Optional, TypedDict

from ._alert import (
    _format_alert,
    _format_alert_slack,
    _format_log_alert,
    _format_log_alert_slack,
    _redact,
)
from ._blame import _git_blame_for_traceback
from ._celery import init_celery
from ._slack import _send_slack_message
from ._store import (
    _DEFAULT_RATE_LIMIT_SECONDS,
    _fingerprint,
    _fingerprint_log,
    _should_send,
)
from ._telegram import _send_telegram_message

__all__: list[str] = [
    "init",
    "report",
    "capture",
    "ASGIMiddleware",
    "LogHandler",
    "init_celery",
]

_ExceptHook = Callable[
    [type[BaseException], BaseException, Optional[TracebackType]], object
]
_ThreadingExceptHook = Callable[[threading.ExceptHookArgs], object]


class _State(TypedDict):
    bot_token: str | None
    chat_id: int | str | None
    slack_webhook_url: str | None
    redact: bool
    rate_limit_seconds: int
    tags: dict[str, str]
    blame: bool
    # Seeded with the real default hooks below (never None) -- _excepthook/
    # _threading_excepthook can only ever fire after init() has replaced
    # sys.excepthook/threading.excepthook, by which point these are always
    # set, so keeping them non-Optional avoids an unreachable None check.
    prev_excepthook: _ExceptHook
    prev_threading_excepthook: _ThreadingExceptHook


_state: _State = {
    "bot_token": None,
    "chat_id": None,
    "slack_webhook_url": None,
    "redact": True,
    "rate_limit_seconds": _DEFAULT_RATE_LIMIT_SECONDS,
    "tags": {},
    "blame": False,
    "prev_excepthook": sys.excepthook,
    "prev_threading_excepthook": threading.excepthook,
}


def _configured() -> bool:
    return bool(
        (_state["bot_token"] and _state["chat_id"]) or _state["slack_webhook_url"]
    )


def _deliver(format_telegram, format_slack) -> None:
    """Formats and sends to every channel init() configured -- each channel gets its
    own formatting call since Telegram (HTML) and Slack (mrkdwn) need different
    markup, but both share the same fingerprint/dedup decision made by the caller."""
    if _state["bot_token"] and _state["chat_id"]:
        message = format_telegram()
        if _state["redact"]:
            message = _redact(message)
        _send_telegram_message(_state["bot_token"], _state["chat_id"], message)
    if _state["slack_webhook_url"]:
        message = format_slack()
        if _state["redact"]:
            message = _redact(message)
        _send_slack_message(_state["slack_webhook_url"], message)


def _send_exception(
    exc_type, exc_value, tb, extra: dict[str, str] | None = None
) -> None:
    if not _configured():
        print("devalerts: init() was not called, dropping alert", file=sys.stderr)
        return
    fingerprint, location = _fingerprint(exc_type, tb)
    send, skipped, is_new = _should_send(
        fingerprint, exc_type.__name__, location, _state["rate_limit_seconds"]
    )
    if not send:
        return
    tags = {**_state["tags"], **(extra or {})}
    blame = _git_blame_for_traceback(tb) if _state["blame"] else None
    _deliver(
        lambda: _format_alert(
            exc_type,
            exc_value,
            tb,
            skipped=skipped,
            tags=tags,
            blame=blame,
            is_new=is_new,
        ),
        lambda: _format_alert_slack(
            exc_type,
            exc_value,
            tb,
            skipped=skipped,
            tags=tags,
            blame=blame,
            is_new=is_new,
        ),
    )


def _send_log(record: logging.LogRecord, extra: dict[str, str] | None = None) -> None:
    if not _configured():
        print("devalerts: init() was not called, dropping alert", file=sys.stderr)
        return
    fingerprint, location = _fingerprint_log(
        record.name, record.levelno, str(record.msg), record.pathname, record.lineno
    )
    send, skipped, is_new = _should_send(
        fingerprint, record.name, location, _state["rate_limit_seconds"]
    )
    if not send:
        return
    tags = {**_state["tags"], **(extra or {})}
    _deliver(
        lambda: _format_log_alert(
            record.name,
            record.levelname,
            record.getMessage(),
            skipped=skipped,
            tags=tags,
            is_new=is_new,
        ),
        lambda: _format_log_alert_slack(
            record.name,
            record.levelname,
            record.getMessage(),
            skipped=skipped,
            tags=tags,
            is_new=is_new,
        ),
    )


def _excepthook(exc_type, exc_value, tb) -> None:
    if exc_type is not KeyboardInterrupt:
        try:
            _send_exception(exc_type, exc_value, tb)
        except Exception as error:  # noqa: BLE001 - crash handler must never raise
            print(
                f"devalerts: internal error while sending alert: {error}",
                file=sys.stderr,
            )
    _state["prev_excepthook"](exc_type, exc_value, tb)


def _threading_excepthook(args) -> None:
    try:
        _send_exception(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception as error:  # noqa: BLE001
        print(
            f"devalerts: internal error while sending alert: {error}", file=sys.stderr
        )
    _state["prev_threading_excepthook"](args)


def init(
    bot_token: str | None = None,
    chat_id: int | str | None = None,
    *,
    slack_webhook_url: str | None = None,
    redact: bool = True,
    rate_limit_seconds: int = _DEFAULT_RATE_LIMIT_SECONDS,
    tags: dict[str, str] | None = None,
    blame: bool = False,
) -> None:
    """Install a global exception hook that sends unhandled exceptions to Telegram
    and/or Slack. Requires ``bot_token``+``chat_id``, ``slack_webhook_url``, or both
    -- configured channels all receive every alert.

    ``blame=True`` runs ``git blame`` on the line that raised and adds the
    author/commit/date to the alert -- best-effort, silently skipped if
    there's no git repo (e.g. a container image without ``.git``)."""
    if bool(bot_token) != bool(chat_id):
        raise ValueError("bot_token and chat_id must be given together")
    if not bot_token and not slack_webhook_url:
        raise ValueError("init() requires bot_token+chat_id and/or slack_webhook_url")
    _state["bot_token"] = bot_token
    _state["chat_id"] = chat_id
    _state["slack_webhook_url"] = slack_webhook_url
    _state["redact"] = redact
    _state["rate_limit_seconds"] = rate_limit_seconds
    _state["tags"] = tags or {}
    _state["blame"] = blame
    # ponytail: guard against calling init() twice capturing our own hook as
    # "previous" -- that would make _excepthook chain to itself and recurse
    # forever on the next crash, violating "must never raise".
    if sys.excepthook is not _excepthook:
        _state["prev_excepthook"] = sys.excepthook
    if threading.excepthook is not _threading_excepthook:
        _state["prev_threading_excepthook"] = threading.excepthook
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook


def report(
    exc: BaseException | None = None, *, extra: dict[str, str] | None = None
) -> None:
    """Manually send a caught exception to Telegram."""
    if exc is None:
        exc_type, exc_value, tb = sys.exc_info()
        if exc_type is None:
            raise RuntimeError(
                "report() requires an active exception or an exc argument"
            )
    else:
        exc_type, exc_value, tb = type(exc), exc, exc.__traceback__
    _send_exception(exc_type, exc_value, tb, extra=extra)


class capture(contextlib.ContextDecorator):
    """Context manager / decorator: report any exception raised inside the block or
    function, then re-raise it. Use ``@capture()`` on a function instead of wrapping
    its body in a manual ``try/except`` or ``with`` block.

    Used as a decorator, the wrapped function's name is tagged automatically as
    ``job`` -- no need to pass ``extra={"job": ...}`` by hand. Used as a bare
    ``with capture():`` block, there's no function to name, so only explicit
    ``extra`` tags apply."""

    def __init__(self, *, extra: dict[str, str] | None = None) -> None:
        self._extra = extra
        self._job_name: str | None = None

    def __call__(self, func):
        self._job_name = func.__qualname__
        return super().__call__(func)

    def __enter__(self) -> "capture":
        return self

    def __exit__(self, exc_type, exc_value, tb) -> Literal[False]:
        if exc_type is not None:
            tags = {"job": self._job_name} if self._job_name else {}
            tags.update(self._extra or {})
            _send_exception(exc_type, exc_value, tb, extra=tags)
        return False


class ASGIMiddleware:
    """ASGI middleware for FastAPI/Starlette (or any ASGI app): report any exception
    that escapes a request, then re-raise it so the framework's own error handling
    still runs unchanged.

    Usage::

        app.add_middleware(devalerts.ASGIMiddleware)

    Only exceptions that actually reach here (unhandled server errors) get reported —
    routing 404s and raised ``HTTPException``s are already turned into responses by
    the framework before this middleware sees them, same as Sentry's ASGI integration.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        try:
            await self.app(scope, receive, send)
        except Exception:
            exc_type, exc_value, tb = sys.exc_info()
            # ponytail: fire-and-forget in a thread -- _send_exception is a
            # blocking network call; awaiting it here would stall the event
            # loop (and the error response) for up to _TIMEOUT_SECONDS.
            threading.Thread(
                target=_send_exception, args=(exc_type, exc_value, tb), daemon=True
            ).start()
            raise


class LogHandler(logging.Handler):
    """logging.Handler: report ERROR+ log records to Telegram -- catches exceptions
    that are logged and swallowed (``logger.exception(...)``) rather than left to
    propagate to ``sys.excepthook``, which never sees them.

    Usage::

        logging.getLogger().addHandler(devalerts.LogHandler())

    A record with ``exc_info`` (``logger.exception()``, or
    ``logger.error(..., exc_info=True)``) is reported the same way an unhandled
    instance of that exception would be -- same fingerprint, so logging it and then
    re-raising sends one alert, not two. A plain ``logger.error("message")`` with no
    exception is reported as a short text alert, grouped by logger name + level +
    message.
    """

    def __init__(
        self, level: int = logging.ERROR, *, extra: dict[str, str] | None = None
    ) -> None:
        super().__init__(level=level)
        self._extra = extra

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tags = {"logger": record.name, **(self._extra or {})}
            if record.exc_info and record.exc_info[0] is not None:
                exc_type, exc_value, tb = record.exc_info
                _send_exception(exc_type, exc_value, tb, extra=tags)
            else:
                _send_log(record, extra=tags)
        except Exception:  # noqa: BLE001 - a logging handler must never raise
            self.handleError(record)
