"""#HELP1 — multi-page Help hub with agent-aware capability page."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "src/dashboard/static/js/core.js").read_text()
INDEX = (ROOT / "src/dashboard/static/index.html").read_text()
CSS = (ROOT / "src/dashboard/static/css/dashboard.css").read_text()
PANELS = (ROOT / "src/dashboard/static/js/panels.js").read_text()


def test_help_body_is_js_filled_shell():
    assert 'id="help-modal-body"' in INDEX
    # no static glossary dump left in the shell
    assert "help-glossary" not in INDEX
    assert "Filled by openHelp()" in INDEX or "helpShowPage" in INDEX


def test_help_js_has_toc_and_pages():
    for name in (
        "function openHelp",
        "function helpShowPage",
        "function _helpHubHtml",
        "function _helpTogetherHtml",
        "function _helpDeeperHtml",
        "function _helpAgentName",
        "Me &",
        "what we can do together",
        "Details coming soon",
        "Go Deeper",
    ):
        assert name in CORE, name


def test_help_css_toc():
    assert ".help-toc" in CSS
    assert ".help-back" in CSS


def test_go_deeper_links_to_help():
    assert "openHelp" in PANELS
    assert "How to use this system" in PANELS
