"""X caption ceilings — Premium long-post + word-safe fit (no mid-word ...)."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
XP = ROOT / "src" / "dashboard" / "routes" / "x_posting.py"


def _import_x():
    import importlib.util
    import sys

    # Prefer package import when path is set up (pytest from repo root).
    sys.path.insert(0, str(ROOT))
    from src.dashboard.routes import x_posting as xp
    return xp


def test_source_has_no_midword_slice():
    src = XP.read_text()
    assert 'caption[:277] + "..."' not in src
    assert "fit_x_text" in src
    assert "X_PREMIUM_MAX_CHARS" in src
    assert "long_posts" in src


def test_x_length_urls_count_23():
    xp = _import_x()
    assert xp.x_length("hi") == 2
    assert xp.x_length("see https://example.com/foo now") == len("see  now") + 23


def test_fit_never_mid_word():
    xp = _import_x()
    # 40 'wordN ' tokens — well over 280 when repeated.
    words = " ".join(f"word{i:02d}" for i in range(80))
    out = xp.fit_x_text(words, 280)
    assert xp.x_length(out) <= 280
    assert out.endswith("...")
    body = out[: -3].rstrip()
    # Last kept token must be a whole wordNN, not a prefix like "wor"
    last = body.split()[-1]
    assert last.startswith("word")
    assert len(last) >= 6  # word + 2 digits


def test_fit_prefers_sentence_break():
    xp = _import_x()
    first = "This is a complete sentence that ends properly. "
    # Pad second sentence past the free ceiling.
    second = "More words that keep going and going until we blow past two hundred eighty characters with plenty of filler text here yes really enough filler now."
    text = (first + second) * 3
    out = xp.fit_x_text(text, 280)
    assert xp.x_length(out) <= 280
    assert out.endswith("...")
    # Should not end mid-token before ellipsis
    assert not out[:-3].endswith("fille")  # partial of filler


def test_build_caption_free_tier_drops_hashtags_then_fits():
    xp = _import_x()
    long_body = "alpha " * 100  # far over 280
    cap = xp.build_caption("title", hashtags="#one #two", description=long_body, max_chars=280)
    assert xp.x_length(cap) <= 280
    assert "#one" not in cap  # hashtags dropped when over
    assert "..." in cap
    # No mid-word chop of "alpha"
    assert "alp..." not in cap
    assert "alph..." not in cap


def test_build_caption_premium_keeps_long_body():
    xp = _import_x()
    body = ("This is a longer Premium caption meant for a single post. " * 20).strip()
    assert xp.x_length(body) > 280
    assert xp.x_length(body) < 25_000
    cap = xp.build_caption("t", description=body, max_chars=25_000)
    assert cap == xp.normalize_unicode(body)
    assert "..." not in cap


def test_resolve_max_chars_prefs_and_env(monkeypatch):
    xp = _import_x()
    monkeypatch.delenv("X_LONG_POSTS", raising=False)
    monkeypatch.delenv("X_MAX_CHARS", raising=False)
    assert xp.resolve_x_max_chars(prefs=None) == 280
    assert xp.resolve_x_max_chars(prefs={"posting": {"x": {"long_posts": True}}}) == 25_000
    assert xp.resolve_x_max_chars(prefs={"long_posts": True}) == 25_000
    assert xp.resolve_x_max_chars(prefs={"max_chars": 5000}) == 5000
    monkeypatch.setenv("X_LONG_POSTS", "true")
    assert xp.resolve_x_max_chars(prefs=None) == 25_000
    monkeypatch.setenv("X_MAX_CHARS", "1000")
    assert xp.resolve_x_max_chars(prefs=None) == 1000


def test_posting_ui_has_long_posts_checkbox():
    html = (
        ROOT / "src/dashboard/static/action-board/full-video-pipeline.html"
    ).read_text()
    assert 'id="pa-x-long-posts"' in html
    assert "long_posts" in html
