# #D19 — phone-shortcut identity for the Links/resource-hub page ("Deck"). A
# per-page web-app manifest with short_name = "{CoveName} Deck" (Cove-name
# templated) + matching title + the icon head-block, so an add-to-home-screen
# shortcut defaults to a telling name instead of a generic one.
import pathlib

import pytest

import src.dashboard.routes.action_board as ab

LINKS = (pathlib.Path(__file__).resolve().parents[1]
         / "src" / "dashboard" / "static" / "action-board" / "links.html").read_text()


def _manifest_body(resp):
    import json
    return json.loads(bytes(resp.body).decode())


@pytest.mark.asyncio
async def test_manifest_short_name_is_cove_name_plus_deck(monkeypatch):
    monkeypatch.setattr("src.config.get_instance",
                        lambda: {"family_name": "Rivera", "name": "Rivera Cove"})
    resp = await ab.deck_manifest()
    body = _manifest_body(resp)
    assert body["short_name"] == "Rivera Deck"
    assert body["start_url"] == "/static/action-board/links.html"
    assert body["display"] == "standalone"
    assert resp.media_type == "application/manifest+json"


@pytest.mark.asyncio
async def test_manifest_falls_back_to_plain_deck(monkeypatch):
    monkeypatch.setattr("src.config.get_instance", lambda: {"family_name": "", "name": ""})
    body = _manifest_body(await ab.deck_manifest())
    assert body["short_name"] == "Deck"


@pytest.mark.asyncio
async def test_manifest_never_raises_on_config_error(monkeypatch):
    def _boom():
        raise RuntimeError("no config")
    monkeypatch.setattr("src.config.get_instance", _boom)
    body = _manifest_body(await ab.deck_manifest())
    assert body["short_name"] == "Deck"  # best-effort default


def test_label_is_a_single_constant_for_easy_rename():
    # "Deck" isn't locked — the suffix must live in one place (the DECK_LABEL const)
    assert ab.DECK_LABEL == "Deck"
    assert 'const DECK_LABEL' in LINKS  # and once on the client side too


def test_links_page_points_at_the_dedicated_manifest():
    assert 'rel="manifest" href="/deck-manifest.webmanifest"' in LINKS
    assert 'href="/static/manifest.json"' not in LINKS  # not the generic MC manifest


def test_links_page_has_the_icon_and_ios_head_block():
    assert 'rel="apple-touch-icon"' in LINKS
    assert 'name="apple-mobile-web-app-title"' in LINKS
    assert 'name="apple-mobile-web-app-capable"' in LINKS


def test_links_page_fills_the_title_from_cove_name():
    assert "function setDeckIdentity(" in LINKS
    assert "document.title = label" in LINKS
    assert "setDeckIdentity(config)" in LINKS
