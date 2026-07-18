import socket

from devalerts import _alert
from devalerts._alert import (
    _MAX_MESSAGE_LENGTH,
    _format_alert,
    _format_alert_slack,
    _redact,
)


def _make_tb(msg="boom"):
    try:
        raise ValueError(msg)
    except ValueError as exc:
        return type(exc), exc, exc.__traceback__


def _visible_length(html_message: str) -> int:
    """What Telegram's 4096-char limit actually counts: the parsed text,
    not our <blockquote expandable> wrapper or HTML-escaped entities."""
    text = html_message.replace("<blockquote expandable>", "").replace(
        "</blockquote>", ""
    )
    return len(text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))


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


def test_format_alert_includes_blame_when_given():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(
        exc_type, exc_value, tb, blame="sslinNn · a1b2c3d · 2026-07-15 (3d ago)"
    )
    assert "blame: sslinNn · a1b2c3d · 2026-07-15 (3d ago)" in message


def test_format_alert_no_blame_line_when_none():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb)
    assert "blame" not in message


def test_format_alert_marks_new_error():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb, is_new=True)
    assert message.startswith("🆕 New error")


def test_format_alert_no_new_marker_by_default():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb)
    assert "New error" not in message


def test_format_alert_slack_includes_type_and_message():
    exc_type, exc_value, tb = _make_tb("something broke")
    message = _format_alert_slack(exc_type, exc_value, tb)
    assert "ValueError: something broke" in message
    assert "Traceback" in message


def test_format_alert_slack_wraps_traceback_in_code_fence():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert_slack(exc_type, exc_value, tb)
    assert "```" in message
    assert message.endswith("```")


def test_format_alert_slack_bolds_the_header_line():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert_slack(exc_type, exc_value, tb)
    assert message.startswith("*🔴 ValueError:")


def test_format_alert_truncates_long_body(monkeypatch):
    monkeypatch.setattr(_alert, "_MAX_MESSAGE_LENGTH", 100)
    exc_type, exc_value, tb = _make_tb("boom")
    message = _alert._format_alert(exc_type, exc_value, tb)
    assert _visible_length(message) <= 100
    assert "...(truncated)..." in message


def test_format_alert_header_alone_exceeds_limit():
    # exc_value str() so huge the header itself overflows _MAX_MESSAGE_LENGTH --
    # keep must clamp to 0 instead of going negative and slicing garbage.
    exc_type, exc_value, tb = _make_tb("z" * 20_000)
    message = _format_alert(exc_type, exc_value, tb)
    assert _visible_length(message) == _MAX_MESSAGE_LENGTH
    assert "<blockquote" not in message  # nothing left of the body to wrap


def test_redact_aws_key():
    assert _redact("key=AKIAABCDEFGHIJKLMNOP") == "key=[REDACTED]"


def test_redact_bearer_token():
    assert (
        _redact("Authorization: Bearer abc123.def-456_ghi")
        == "Authorization: Bearer [REDACTED]"
    )


def test_redact_generic_secret_patterns():
    assert _redact("api_key=sk_live_abc123") == "api_key=[REDACTED]"
    assert _redact("password: hunter2") == "password=[REDACTED]"
    assert _redact("SECRET=topsecret") == "SECRET=[REDACTED]"


def test_redact_leaves_normal_text_untouched():
    text = "user visited /checkout with total=42"
    assert _redact(text) == text


def test_format_alert_includes_hostname_by_default():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb)
    assert socket.gethostname() in message


def test_format_alert_includes_tags():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb, tags={"env": "production"})
    assert "env=production" in message


def test_format_alert_no_tags_parens_when_no_tags():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb)
    assert "(" not in message.splitlines()[1]


def test_format_alert_wraps_traceback_in_expandable_blockquote():
    exc_type, exc_value, tb = _make_tb()
    message = _format_alert(exc_type, exc_value, tb)
    assert "<blockquote expandable>" in message
    assert message.endswith("</blockquote>")
    header, _, wrapped_body = message.partition("<blockquote expandable>")
    assert "Traceback" not in header
    assert "Traceback" in wrapped_body


def test_format_alert_escapes_html_special_chars_in_header_and_body():
    exc_type, exc_value, tb = _make_tb("<script>&boom</script>")
    message = _format_alert(exc_type, exc_value, tb, tags={"path": "<a>"})
    assert "<script>" not in message
    assert "&lt;script&gt;&amp;boom&lt;/script&gt;" in message
    assert "path=&lt;a&gt;" in message


def test_format_alert_html_escaping_survives_redaction(monkeypatch):
    from devalerts._alert import _redact

    exc_type, exc_value, tb = _make_tb("api_key=<secret>")
    message = _redact(_format_alert(exc_type, exc_value, tb))
    assert "api_key=[REDACTED]" in message
    assert "<secret>" not in message
