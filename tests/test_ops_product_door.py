"""Ops as a product door — every Cove, upgrade-safe, no instance seeds."""

from src.dashboard.routes import action_board as ab


def test_default_links_include_ops_door():
    cards = ab._default_links()
    urls = {c.get("url") for c in cards}
    ids = {c.get("id") for c in cards}
    assert "/ops" in urls
    assert "ops" in ids
    # Fundamentals stay present
    assert "/backlog" in urls
    assert "/jules" in urls
    assert "/briefs" in urls


def test_ensure_product_doors_empty_board_includes_ops():
    cards = ab._ensure_product_doors([])
    assert any(c.get("url") == "/ops" for c in cards)
    assert any(c.get("id") == "ops" for c in cards)


def test_ensure_product_doors_merges_ops_into_existing_board():
    existing = [
        {"id": "backlog", "type": "link", "title": "Backlog", "url": "/backlog",
         "note": "", "icon": "🗂", "group": "", "items": []},
        {"id": "briefs", "type": "link", "title": "Briefs", "url": "/briefs",
         "note": "", "icon": "📄", "group": "", "items": []},
        {"id": "custom", "type": "link", "title": "Custom", "url": "https://example.com",
         "note": "", "icon": "", "group": "", "items": []},
    ]
    out = ab._ensure_product_doors(existing)
    assert any(c.get("url") == "/ops" for c in out)
    assert any(c.get("id") == "custom" for c in out)
    assert sum(1 for c in out if c.get("id") == "backlog") == 1
    assert sum(1 for c in out if c.get("id") == "briefs") == 1


def test_ensure_product_doors_skips_when_ops_url_already_present():
    existing = [
        {"id": "x", "type": "link", "title": "Ops visibility", "url": "/ops",
         "note": "", "icon": "🛠", "group": "", "items": []},
    ]
    out = ab._ensure_product_doors(existing)
    assert sum(1 for c in out if (c.get("url") or "").rstrip("/") == "/ops") == 1


def test_ensure_product_doors_still_merges_briefs_and_ops():
    existing = [
        {"id": "backlog", "type": "link", "title": "Backlog", "url": "/backlog",
         "note": "", "icon": "🗂", "group": "", "items": []},
    ]
    out = ab._ensure_product_doors(existing)
    ids = {c.get("id") for c in out}
    assert "briefs" in ids
    assert "ops" in ids
