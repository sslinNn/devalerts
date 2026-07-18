import json

import pytest

from devalerts import _slack


@pytest.fixture(autouse=True)
def isolated_failed_log(tmp_path, monkeypatch):
    monkeypatch.setattr(_slack, "_log_failed_delivery", lambda text: None)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(_slack.time, "sleep", lambda seconds: None)


def test_send_slack_message_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout

    monkeypatch.setattr(_slack.urllib.request, "urlopen", fake_urlopen)

    ok = _slack._send_slack_message("https://hooks.slack.com/services/x", "hello")

    assert ok is True
    assert captured["url"] == "https://hooks.slack.com/services/x"
    assert captured["body"] == {"text": "hello"}
    assert captured["timeout"] == _slack._TIMEOUT_SECONDS


def test_send_slack_message_retries_then_succeeds(monkeypatch):
    import urllib.error

    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(_slack.urllib.request, "urlopen", fake_urlopen)

    ok = _slack._send_slack_message("https://hooks.slack.com/services/x", "hello")

    assert ok is True
    assert len(attempts) == 2


def test_send_slack_message_swallows_network_errors_and_logs_fallback(
    monkeypatch, capsys
):
    import urllib.error

    logged = []
    monkeypatch.setattr(
        _slack, "_log_failed_delivery", lambda text: logged.append(text)
    )

    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(_slack.urllib.request, "urlopen", fake_urlopen)

    ok = _slack._send_slack_message(
        "https://hooks.slack.com/services/x", "hello"
    )  # must not raise

    assert ok is False
    assert "failed to send Slack alert" in capsys.readouterr().err
    assert logged == ["hello"]
