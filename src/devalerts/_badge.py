"""Crash-streak SVG badge rendering for `devalerts badge` -- no network call,
no shields.io dependency, just a hand-rolled flat badge."""

from __future__ import annotations

from xml.sax.saxutils import escape

_BAND_COLORS = {
    "grey": "#9f9f9f",
    "red": "#e05d44",
    "yellow": "#dfb317",
    "green": "#4c1",
}


def _streak_band(days: int | None) -> str:
    if days is None:
        return "grey"
    if days < 1:
        return "red"
    if days < 7:
        return "yellow"
    return "green"


def _streak_text(days: int | None) -> str:
    if days is None:
        return "no incidents yet"
    if days < 1:
        return "today"
    return f"{days} day{'' if days == 1 else 's'}"


def _render_badge(label: str, days: int | None) -> str:
    """Flat shields.io-style badge. Character width is a fixed-per-glyph
    approximation, not real font metrics -- fine for a README image, not
    meant to be pixel-perfect."""
    value = _streak_text(days)
    color = _BAND_COLORS[_streak_band(days)]
    label_width = 10 + 7 * len(label)
    value_width = 10 + 7 * len(value)
    width = label_width + value_width
    height = 20
    label_esc = escape(label)
    value_esc = escape(value)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<rect width="{label_width}" height="{height}" fill="#555"/>'
        f'<rect x="{label_width}" width="{value_width}" height="{height}" fill="{color}"/>'
        f'<g fill="#fff" font-family="Verdana,Geneva,sans-serif" font-size="11">'
        f'<text x="{label_width / 2}" y="14" text-anchor="middle">{label_esc}</text>'
        f'<text x="{label_width + value_width / 2}" y="14" text-anchor="middle">'
        f"{value_esc}</text>"
        f"</g></svg>"
    )
