import json
import urllib.error

from devalerts import _telegram


def test_send_telegram_message_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    _telegram._send_telegram_message("TOKEN", 12345, "hello")

    assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert captured["body"] == {"chat_id": 12345, "text": "hello"}
    assert captured["timeout"] == _telegram._TIMEOUT_SECONDS


def test_send_telegram_message_swallows_network_errors(monkeypatch, capsys):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(_telegram.urllib.request, "urlopen", fake_urlopen)

    _telegram._send_telegram_message("TOKEN", 12345, "hello")  # must not raise

    assert "failed to send Telegram alert" in capsys.readouterr().err
