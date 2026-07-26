"""#GABS-V1 Phase 1 — Gabs by Gabe Quick path unit tests."""

from src.dashboard.routes import gabs as gabs_mod


def test_valid_url_accepts_https():
    ok, val = gabs_mod._valid_url("https://example.com/path")
    assert ok is True
    assert val.startswith("https://")


def test_valid_url_rejects_empty_and_scheme():
    ok, err = gabs_mod._valid_url("")
    assert ok is False
    assert "required" in err.lower()
    ok, err = gabs_mod._valid_url("ftp://x.com")
    assert ok is False


def test_slug():
    assert gabs_mod._slug("Hello, World!!") == "hello-world"
    assert gabs_mod._slug("") == "gab"


def test_build_html_contains_sections():
    data = {
        "title": "Test Gab",
        "what_it_is": "A sample source",
        "bottom_line": "Matters a little",
        "fit": "adjacent",
        "key_points": ["one", "two"],
        "gaps": ["unknown"],
        "sources": [{"title": "Ex", "url": "https://example.com"}],
        "suggested_next": "ignore — noise adjacent",
    }
    html_out = gabs_mod._build_html(data, "https://example.com/x", "focus", 42)
    assert "Gabs by Gabe" in html_out
    assert "Gab 42" in html_out
    assert "Test Gab" in html_out
    assert "Bottom line" in html_out
    assert "adjacent" in html_out
    assert "https://example.com/x" in html_out


def test_parse_sources():
    assert gabs_mod._parse_sources('[{"url":"https://a.com"}]') == [{"url": "https://a.com"}]
    assert gabs_mod._parse_sources(None) == []
    assert gabs_mod._parse_sources("not-json") == []


def test_gabs_router_has_expected_paths():
    paths = {getattr(r, "path", None) for r in gabs_mod.router.routes}
    assert "/gabs" in paths
    assert "/api/gabs" in paths
    assert "/api/gabs/capture" in paths
