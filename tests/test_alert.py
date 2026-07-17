from devalerts import _alert
from devalerts._alert import _MAX_MESSAGE_LENGTH, _format_alert, _redact


def _make_tb(msg="boom"):
    try:
        raise ValueError(msg)
    except ValueError as exc:
        return type(exc), exc, exc.__traceback__


def test_format_alert_includes_type_and_message():
    exc_type, exc_value, tb = _make_tb("something broke")
    message = _format_alert(exc_type, exc_value, tb)
    assert "ValueError: something broke" in message
    assert "Traceback" in message


def test_format_alert_marks_skipped_repeats():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb, skipped=3)
    assert "3 раз" in message


def test_format_alert_no_skipped_marker_by_default():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb)
    assert "Повторилась" not in message


def test_format_alert_truncates_long_body(monkeypatch):
    monkeypatch.setattr(_alert, "_MAX_MESSAGE_LENGTH", 100)
    exc_type, exc_value, tb = _make_tb("boom")
    message = _alert._format_alert(exc_type, exc_value, tb)
    assert len(message) <= 100
    assert "...(truncated)..." in message


def test_format_alert_header_alone_exceeds_limit():
    # exc_value str() so huge the header itself overflows _MAX_MESSAGE_LENGTH --
    # keep must clamp to 0 instead of going negative and slicing garbage.
    exc_type, exc_value, tb = _make_tb("z" * 20_000)
    message = _format_alert(exc_type, exc_value, tb)
    assert len(message) == _MAX_MESSAGE_LENGTH


def test_redact_aws_key():
    assert _redact("key=AKIAABCDEFGHIJKLMNOP") == "key=[REDACTED]"


def test_redact_bearer_token():
    assert _redact("Authorization: Bearer abc123.def-456_ghi") == "Authorization: Bearer [REDACTED]"


def test_redact_generic_secret_patterns():
    assert _redact("api_key=sk_live_abc123") == "api_key=[REDACTED]"
    assert _redact("password: hunter2") == "password=[REDACTED]"
    assert _redact("SECRET=topsecret") == "SECRET=[REDACTED]"


def test_redact_leaves_normal_text_untouched():
    text = "user visited /checkout with total=42"
    assert _redact(text) == text
