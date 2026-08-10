"""Display prefs — normalize text size / font / contrast for MC chrome."""
from __future__ import annotations

from pathlib import Path

from src.dashboard.routes.settings import (
    _DEFAULT_DISPLAY,
    _normalize_display_prefs,
)


def test_normalize_defaults_on_empty():
    assert _normalize_display_prefs(None) == _DEFAULT_DISPLAY
    assert _normalize_display_prefs({}) == _DEFAULT_DISPLAY
    assert _normalize_display_prefs("nope") == _DEFAULT_DISPLAY


def test_normalize_accepts_valid():
    got = _normalize_display_prefs(
        {"text_size": "xl", "font": "sans", "contrast": "high"}
    )
    assert got == {"text_size": "xl", "font": "sans", "contrast": "high"}


def test_normalize_rejects_unknown_keeps_default():
    got = _normalize_display_prefs(
        {"text_size": "huge", "font": "comic", "contrast": "max"}
    )
    assert got == _DEFAULT_DISPLAY


def test_normalize_partial_merge():
    got = _normalize_display_prefs({"text_size": "lg"})
    assert got["text_size"] == "lg"
    assert got["font"] == "mono"
    assert got["contrast"] == "standard"


def test_normalize_camel_case_aliases():
    got = _normalize_display_prefs(
        {"textSize": "sm", "fontFamily": "serif", "contrast": "high"}
    )
    assert got["text_size"] == "sm"
    assert got["font"] == "serif"
    assert got["contrast"] == "high"


def test_normalize_case_insensitive():
    got = _normalize_display_prefs(
        {"text_size": "LG", "font": "SANS", "contrast": "HIGH"}
    )
    assert got == {"text_size": "lg", "font": "sans", "contrast": "high"}


def test_css_has_display_tokens():
    css = Path("src/dashboard/static/css/dashboard.css").read_text()
    assert "data-text-size" in css
    assert "--text-scale" in css
    assert "data-contrast=\"high\"" in css or "data-contrast=\"high\"" in css.replace(" ", "")
    assert "--font-ui" in css


def test_index_applies_local_prefs_early():
    html = Path("src/dashboard/static/index.html").read_text()
    assert "mc.displayPrefs" in html
    assert "data-text-size" in html


def test_display_prefs_js_exists():
    js = Path("src/dashboard/static/js/display-prefs.js").read_text()
    assert "loadSettingsDisplay" in js
    assert "/api/settings/display" in js
    assert "applyDisplayPrefs" in js


def test_settings_panel_has_display_group():
    panels = Path("src/dashboard/static/js/panels.js").read_text()
    assert "settings-display" in panels
    assert "Display" in panels
