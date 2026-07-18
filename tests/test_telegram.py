import io
import json
import urllib.error

import pytest

from devalerts import _telegram


@pytest.fixture(autouse=True)
def isolated_failed_log(tmp_path, monkeypatch):
    monkeypatch.setattr(_telegram, "_FAILED_LOG_PATH", tmp_path / "failed.log")


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(_telegram.time, "sleep", lambda seconds: None)


def test_send_telegram_message_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    ok = _telegram._send_telegram_message("TOKEN", 12345, "hello")

    assert ok is True
    assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert captured["body"] == {"chat_id": 12345, "text": "hello"}
    assert captured["timeout"] == _telegram._TIMEOUT_SECONDS


def test_send_telegram_message_retries_then_succeeds(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    ok = _telegram._send_telegram_message("TOKEN", 12345, "hello")

    assert ok is True
    assert len(attempts) == 2


def test_send_telegram_message_swallows_network_errors_and_logs_fallback(
    monkeypatch, capsys
):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    ok = _telegram._send_telegram_message("TOKEN", 12345, "hello")  # must not raise

    assert ok is False
    assert "failed to send Telegram alert" in capsys.readouterr().err
    assert "hello" in _telegram._FAILED_LOG_PATH.read_text(encoding="utf-8")


def test_send_telegram_message_honors_retry_after_header(monkeypatch):
    attempts = []
    waited = []
    monkeypatch.setattr(_telegram.time, "sleep", lambda seconds: waited.append(seconds))

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            body = json.dumps({"parameters": {"retry_after": 2}}).encode()
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {}, io.BytesIO(body)
            )

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    ok = _telegram._send_telegram_message("TOKEN", 12345, "hello")

    assert ok is True
    assert waited == [2.0]


def test_send_telegram_message_gives_up_on_long_retry_after(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        body = json.dumps({"parameters": {"retry_after": 999}}).encode()
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", {}, io.BytesIO(body)
        )

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    ok = _telegram._send_telegram_message("TOKEN", 12345, "hello")

    assert ok is False
    assert len(attempts) == 1  # gave up instead of blocking for 999s
