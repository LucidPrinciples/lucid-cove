"""Daily Tuner archive auto-publish: id parse, playlist fallback, no-card merge.

TUNEARCH1: lucidtuner.com /tunings ships without an Attention card.
site_deploy stays gated for other sites.
"""
from src.utils.tunings_publish import (
    TUNER_DOMAIN,
    allowed_domain,
    in_morning_window,
    parse_playlist_feed,
    parse_youtube_id,
    patch_sitemap,
    upsert_catalog,
)
from datetime import datetime
from zoneinfo import ZoneInfo


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>UzzgVzFHJN0</yt:videoId>
    <title>Daily Tuning #237 — Gratitude | Lucid Tuner</title>
  </entry>
  <entry>
    <yt:videoId>x-mkpzWu2vY</yt:videoId>
    <title>Daily Tuning #236 — Release | Lucid Tuner</title>
  </entry>
</feed>
"""


def test_parse_youtube_id_plain_and_url():
    assert parse_youtube_id("UzzgVzFHJN0\n") == "UzzgVzFHJN0"
    assert parse_youtube_id("https://www.youtube.com/watch?v=UzzgVzFHJN0") == "UzzgVzFHJN0"
    assert parse_youtube_id("not-an-id") == ""
    assert parse_youtube_id("") == ""


def test_playlist_feed_picks_echo_then_newest():
    assert parse_playlist_feed(FEED, echo=237) == "UzzgVzFHJN0"
    assert parse_playlist_feed(FEED, echo=236) == "x-mkpzWu2vY"
    assert parse_playlist_feed(FEED) == "UzzgVzFHJN0"


def test_domain_lock_is_lucidtuner_only():
    assert allowed_domain("lucidtuner.com") is True
    assert allowed_domain("lucidprinciples.com") is False
    assert allowed_domain("lucidcove.org") is False


def test_catalog_upsert_puts_today_first():
    data = {
        "entries": [
            {"date": "2026-10-01", "youtube_id": "x-mkpzWu2vY"},
        ]
    }
    out = upsert_catalog(data, {"date": "2026-10-02", "youtube_id": "UzzgVzFHJN0"})
    assert out["entries"][0]["date"] == "2026-10-02"
    assert out["entries"][1]["date"] == "2026-10-01"
    again = upsert_catalog(out, {"date": "2026-10-02", "youtube_id": "AAAAAAAAAAA"})
    assert [e["date"] for e in again["entries"]].count("2026-10-02") == 1
    assert again["entries"][0]["youtube_id"] == "AAAAAAAAAAA"


def test_sitemap_inserts_day_once():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://lucidtuner.com/tunings/</loc><lastmod>2026-10-01</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>
  <url><loc>https://lucidtuner.com/tunings/2026-10-01/</loc><lastmod>2026-10-01</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>
</urlset>
"""
    patched = patch_sitemap(xml, "2026-10-02")
    assert patched.count("https://lucidtuner.com/tunings/2026-10-02/") == 1
    assert "lastmod>2026-10-02<" in patched
    twice = patch_sitemap(patched, "2026-10-02")
    assert twice.count("https://lucidtuner.com/tunings/2026-10-02/") == 1


def test_morning_window_covers_sidecar_wait():
    tz = ZoneInfo("America/New_York")
    assert in_morning_window(datetime(2026, 10, 2, 7, 35, tzinfo=tz)) is True
    assert in_morning_window(datetime(2026, 10, 2, 8, 40, tzinfo=tz)) is True
    assert in_morning_window(datetime(2026, 10, 2, 6, 15, tzinfo=tz)) is False
    assert in_morning_window(datetime(2026, 10, 2, 12, 0, tzinfo=tz)) is False


def test_deploy_core_accepts_no_card_flag():
    import inspect
    from src.dashboard.routes.sites import _deploy_site_core

    params = inspect.signature(_deploy_site_core).parameters
    assert "raise_approval" in params
    assert params["raise_approval"].default is True


def test_tuner_domain_constant():
    assert TUNER_DOMAIN == "lucidtuner.com"
