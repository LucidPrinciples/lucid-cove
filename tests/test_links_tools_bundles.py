"""Links tools see and write bundles (not leaf-only)."""
from src.tools.links_tools import (
    _format_card,
    _parse_bundle_items,
    _card_type,
)


def test_parse_bundle_items_rows_sub_spacer():
    items = _parse_bundle_items(
        "1st - | SBA | https://example.com/sba\n"
        "---\n"
        "sub: Account 1\n"
        "Dashboard | dash.cloudflare.com\n"
        "## Gym\n"
        "https://gym.example/\n"
    )
    kinds = [i["kind"] for i in items]
    assert kinds == ["row", "spacer", "subhead", "row", "subhead", "row"]
    assert items[0]["label"] == "1st -" and "example.com/sba" in items[0]["url"]
    assert items[2]["label"] == "Account 1"
    assert items[3]["url"] == "dash.cloudflare.com" or items[3]["url"].endswith("dash.cloudflare.com")
    assert items[4]["label"] == "Gym"


def test_format_card_bundle_shows_rows():
    card = {
        "type": "bundle",
        "title": "Monthly Bills",
        "icon": "💳",
        "wide": True,
        "items": [
            {"kind": "row", "label": "1st -", "text": "SBA", "url": "https://ex/sba"},
            {"kind": "spacer"},
            {"kind": "subhead", "label": "Account 1"},
            {"kind": "row", "label": "Gym", "text": "", "url": "https://gym"},
        ],
    }
    text = _format_card(card)
    assert "[bundle]" in text and "wide" in text
    assert "1st -" in text and "SBA" in text and "https://ex/sba" in text
    assert "## Account 1" in text
    assert "Gym" in text


def test_format_card_leaf():
    text = _format_card({
        "type": "link",
        "title": "Backlog",
        "url": "/backlog",
        "note": "board",
        "icon": "🗂",
        "group": "Tools",
    })
    assert "Backlog" in text and "/backlog" in text and "Tools" in text
    assert _card_type({"type": "bundle"}) == "bundle"
    assert _card_type({}) == "link"


def test_backlog_icon_assets_and_manifest():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    static = root / "src/dashboard/static"
    assert (static / "backlog-icon.png").is_file()
    assert (static / "backlog-icon-192.png").is_file()
    assert (static / "backlog-icon-512.png").is_file()
    html = (static / "backlog.html").read_text()
    assert "backlog-icon.png" in html
    assert "icon-192.png" not in html.split("apple-touch-icon")[1][:80]
    py = (root / "src/dashboard/routes/backlog.py").read_text()
    assert "backlog-icon-192.png" in py and "backlog-icon-512.png" in py
    assert "Docs/tool-pwa-icons.md" in py or "tool-pwa-icons" in py
    doc = root / "Docs/tool-pwa-icons.md"
    assert doc.is_file()
    assert "julian-icon" in doc.read_text()


def test_links_tools_exports_bundle_tool():
    from src.tools import links_tools as lt
    names = {t.name for t in lt.ALL_LINKS_TOOLS}
    assert "get_action_links" in names
    assert "add_action_bundle" in names
    assert "add_action_link" in names
