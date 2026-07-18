import logging

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
    yield sent


@pytest.fixture
def logger():
    logger = logging.getLogger("devalerts-test-logger")
    logger.setLevel(logging.DEBUG)
    yield logger
    logger.handlers.clear()


def test_logger_exception_reports_with_traceback(isolated, logger):
    logger.addHandler(devalerts.LogHandler())

    try:
        raise ValueError("db connection dropped")
    except ValueError:
        logger.exception("failed to save")

    assert len(isolated) == 1
    assert "ValueError: db connection dropped" in isolated[0]
    assert "logger=devalerts-test-logger" in isolated[0]


def test_plain_error_without_exc_info_reports_text_alert(isolated, logger):
    logger.addHandler(devalerts.LogHandler())

    logger.error("payment gateway timed out")

    assert len(isolated) == 1
    assert "devalerts-test-logger" in isolated[0]
    assert "payment gateway timed out" in isolated[0]


def test_below_configured_level_is_not_reported(isolated, logger):
    logger.addHandler(devalerts.LogHandler(level=logging.ERROR))

    logger.warning("just a warning")

    assert isolated == []


def test_log_then_reraise_dedupes_with_excepthook_fingerprint(isolated, logger):
    logger.addHandler(devalerts.LogHandler())

    def _boom():
        raise ValueError("both logged and raised")

    try:
        try:
            _boom()
        except ValueError:
            logger.exception("boom happened")
            raise
    except ValueError:
        exc_type, exc_value, tb = __import__("sys").exc_info()
        devalerts._send_exception(exc_type, exc_value, tb)

    assert len(isolated) == 1


def test_extra_tags_are_merged(isolated, logger):
    logger.addHandler(devalerts.LogHandler(extra={"service": "worker"}))

    logger.error("something broke")

    assert "service=worker" in isolated[0]


def test_emit_never_raises_on_delivery_failure(isolated, logger, monkeypatch):
    def _boom(token, chat_id, text):
        raise RuntimeError("network is down")

    monkeypatch.setattr(devalerts, "_send_telegram_message", _boom)
    logger.addHandler(devalerts.LogHandler())

    logger.error("this must not blow up the caller")
