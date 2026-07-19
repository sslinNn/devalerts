from devalerts import _badge


def test_streak_band_thresholds():
    assert _badge._streak_band(None) == "grey"
    assert _badge._streak_band(0) == "red"
    assert _badge._streak_band(1) == "yellow"
    assert _badge._streak_band(6) == "yellow"
    assert _badge._streak_band(7) == "green"


def test_streak_text_for_each_band():
    assert _badge._streak_text(None) == "no incidents yet"
    assert _badge._streak_text(0) == "today"
    assert _badge._streak_text(1) == "1 day"
    assert _badge._streak_text(5) == "5 days"


def test_render_badge_contains_day_count_and_svg_tag():
    svg = _badge._render_badge("crash streak", 5)
    assert svg.startswith("<svg")
    assert "5 days" in svg
    assert "crash streak" in svg


def test_render_badge_no_incidents_yet():
    svg = _badge._render_badge("crash streak", None)
    assert "no incidents yet" in svg


def test_render_badge_escapes_label_and_value():
    svg = _badge._render_badge("<script>", 5)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
