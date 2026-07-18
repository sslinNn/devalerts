"""Send unhandled Python exceptions straight to a Telegram chat."""

from __future__ import annotations

import contextlib
import sys
import threading
from types import TracebackType
from typing import Callable, Literal, Optional, TypedDict

from ._alert import _format_alert, _redact
from ._celery import init_celery
from ._store import _DEFAULT_RATE_LIMIT_SECONDS, _fingerprint, _should_send
from ._telegram import _send_telegram_message

__all__: list[str] = ["init", "report", "capture", "ASGIMiddleware", "init_celery"]

_ExceptHook = Callable[
    [type[BaseException], BaseException, Optional[TracebackType]], object
]
_ThreadingExceptHook = Callable[[threading.ExceptHookArgs], object]


class _State(TypedDict):
    bot_token: str | None
    chat_id: int | str | None
    redact: bool
    rate_limit_seconds: int
    tags: dict[str, str]
    # Seeded with the real default hooks below (never None) -- _excepthook/
    # _threading_excepthook can only ever fire after init() has replaced
    # sys.excepthook/threading.excepthook, by which point these are always
    # set, so keeping them non-Optional avoids an unreachable None check.
    prev_excepthook: _ExceptHook
    prev_threading_excepthook: _ThreadingExceptHook


_state: _State = {
    "bot_token": None,
    "chat_id": None,
    "redact": True,
    "rate_limit_seconds": _DEFAULT_RATE_LIMIT_SECONDS,
    "tags": {},
    "prev_excepthook": sys.excepthook,
    "prev_threading_excepthook": threading.excepthook,
}


def _send_exception(
    exc_type, exc_value, tb, extra: dict[str, str] | None = None
) -> None:
    if _state["bot_token"] is None or _state["chat_id"] is None:
        print("devalerts: init() was not called, dropping alert", file=sys.stderr)
        return
    fingerprint, location = _fingerprint(exc_type, tb)
    send, skipped = _should_send(
        fingerprint, exc_type.__name__, location, _state["rate_limit_seconds"]
    )
    if not send:
        return
    tags = {**_state["tags"], **(extra or {})}
    message = _format_alert(exc_type, exc_value, tb, skipped=skipped, tags=tags)
    if _state["redact"]:
        message = _redact(message)
    _send_telegram_message(_state["bot_token"], _state["chat_id"], message)


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
    bot_token: str,
    chat_id: int | str,
    *,
    redact: bool = True,
    rate_limit_seconds: int = _DEFAULT_RATE_LIMIT_SECONDS,
    tags: dict[str, str] | None = None,
) -> None:
    """Install a global exception hook that sends unhandled exceptions to Telegram."""
    _state["bot_token"] = bot_token
    _state["chat_id"] = chat_id
    _state["redact"] = redact
    _state["rate_limit_seconds"] = rate_limit_seconds
    _state["tags"] = tags or {}
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
