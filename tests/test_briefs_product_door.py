"""Briefs as a product door — every Cove, upgrade-safe, no instance seeds."""

from src.dashboard.routes import action_board as ab
from src import config as cfg


def test_default_links_include_briefs_door():
    cards = ab._default_links()
    urls = {c.get("url") for c in cards}
    ids = {c.get("id") for c in cards}
    assert "/briefs" in urls
    assert "briefs" in ids
    # Fundamentals stay present
    assert "/backlog" in urls
    assert "/jules" in urls


def test_ensure_product_doors_empty_board_gets_defaults():
    cards = ab._ensure_product_doors([])
    assert any(c.get("url") == "/briefs" for c in cards)
    assert any(c.get("id") == "backlog" for c in cards)


def test_ensure_product_doors_merges_briefs_into_existing_board():
    existing = [
        {"id": "backlog", "type": "link", "title": "Backlog", "url": "/backlog",
         "note": "", "icon": "🗂", "group": "", "items": []},
        {"id": "custom", "type": "link", "title": "Custom", "url": "https://example.com",
         "note": "", "icon": "", "group": "", "items": []},
    ]
    out = ab._ensure_product_doors(existing)
    assert any(c.get("url") == "/briefs" for c in out)
    # Preserves custom cards
    assert any(c.get("id") == "custom" for c in out)
    # Does not duplicate backlog
    assert sum(1 for c in out if c.get("id") == "backlog") == 1


def test_ensure_product_doors_skips_when_briefs_url_already_present():
    existing = [
        {"id": "x", "type": "link", "title": "Briefs library", "url": "/briefs",
         "note": "", "icon": "📚", "group": "Briefs", "items": []},
    ]
    out = ab._ensure_product_doors(existing)
    assert sum(1 for c in out if (c.get("url") or "").rstrip("/") == "/briefs") == 1


def test_presence_default_modules_include_briefs_tools():
    assert "tools.briefs_tools" in cfg._PRESENCE_DEFAULT_MODULES


def test_steward_and_merchant_universal_bind_briefs():
    src = open("src/graphs/channels.py").read()
    assert "tools.briefs_tools" in src
    # steward list and merchant append both mention it
    assert src.count("tools.briefs_tools") >= 2


def test_get_tool_modules_appends_briefs_when_missing(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: {"tools": {"modules": ["tools.memory_tools"]}},
    )
    mods = cfg.get_tool_modules()
    assert "tools.briefs_tools" in mods
    assert "tools.memory_tools" in mods
