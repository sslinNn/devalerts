"""Send unhandled Python exceptions straight to a Telegram chat."""

from __future__ import annotations

import contextlib
import sys
import threading

from ._alert import _format_alert, _redact
from ._store import _DEFAULT_RATE_LIMIT_SECONDS, _fingerprint, _should_send
from ._telegram import _send_telegram_message

__all__: list[str] = ["init", "report", "capture", "ASGIMiddleware"]

_state = {
    "bot_token": None,
    "chat_id": None,
    "redact": True,
    "rate_limit_seconds": _DEFAULT_RATE_LIMIT_SECONDS,
    "prev_excepthook": None,
    "prev_threading_excepthook": None,
}


def _send_exception(exc_type, exc_value, tb) -> None:
    fingerprint, location = _fingerprint(exc_type, tb)
    send, skipped = _should_send(fingerprint, exc_type.__name__, location, _state["rate_limit_seconds"])
    if not send:
        return
    message = _format_alert(exc_type, exc_value, tb, skipped=skipped)
    if _state["redact"]:
        message = _redact(message)
    _send_telegram_message(_state["bot_token"], _state["chat_id"], message)


def _excepthook(exc_type, exc_value, tb) -> None:
    if exc_type is not KeyboardInterrupt:
        try:
            _send_exception(exc_type, exc_value, tb)
        except Exception as error:  # noqa: BLE001 - crash handler must never raise
            print(f"devalerts: internal error while sending alert: {error}", file=sys.stderr)
    _state["prev_excepthook"](exc_type, exc_value, tb)


def _threading_excepthook(args) -> None:
    try:
        _send_exception(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception as error:  # noqa: BLE001
        print(f"devalerts: internal error while sending alert: {error}", file=sys.stderr)
    _state["prev_threading_excepthook"](args)


def init(
    bot_token: str,
    chat_id: int | str,
    *,
    redact: bool = True,
    rate_limit_seconds: int = _DEFAULT_RATE_LIMIT_SECONDS,
) -> None:
    """Install a global exception hook that sends unhandled exceptions to Telegram."""
    _state["bot_token"] = bot_token
    _state["chat_id"] = chat_id
    _state["redact"] = redact
    _state["rate_limit_seconds"] = rate_limit_seconds
    # ponytail: guard against calling init() twice capturing our own hook as
    # "previous" -- that would make _excepthook chain to itself and recurse
    # forever on the next crash, violating "must never raise".
    if sys.excepthook is not _excepthook:
        _state["prev_excepthook"] = sys.excepthook
    if threading.excepthook is not _threading_excepthook:
        _state["prev_threading_excepthook"] = threading.excepthook
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook


def report(exc: BaseException | None = None) -> None:
    """Manually send a caught exception to Telegram."""
    if exc is None:
        exc_type, exc_value, tb = sys.exc_info()
        if exc_type is None:
            raise RuntimeError("report() requires an active exception or an exc argument")
    else:
        exc_type, exc_value, tb = type(exc), exc, exc.__traceback__
    _send_exception(exc_type, exc_value, tb)


class capture(contextlib.ContextDecorator):
    """Context manager / decorator: report any exception raised inside the block or
    function, then re-raise it. Use ``@capture()`` on a function instead of wrapping
    its body in a manual ``try/except`` or ``with`` block."""

    def __enter__(self) -> "capture":
        return self

    def __exit__(self, exc_type, exc_value, tb) -> bool:
        if exc_type is not None:
            _send_exception(exc_type, exc_value, tb)
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
