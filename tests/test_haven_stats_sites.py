"""Haven Traffic Umami stats proxy (#HAVEN-MC1 Phase 2)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient, Request, Response

from src.dashboard.routes import haven as haven_mod


def test_default_stats_sites_are_four_brand_properties():
    sites = haven_mod._DEFAULT_STATS_SITES
    domains = {s["domain"] for s in sites}
    assert domains == {
        "lucidprinciples.com",
        "lucidtuner.com",
        "lucidcove.org",
        "chordsoftruth.com",
    }
    for s in sites:
        assert s["umami_website_id"]


def test_parse_stats_sites_from_env_json(monkeypatch):
    payload = [
        {
            "name": "Personal",
            "domain": "example.com",
            "umami_website_id": "11111111-1111-1111-1111-111111111111",
        }
    ]
    monkeypatch.setenv("HAVEN_STATS_SITES", json.dumps(payload))
    sites = haven_mod._parse_stats_sites()
    assert len(sites) == 1
    assert sites[0]["domain"] == "example.com"


def test_parse_stats_sites_falls_back_on_bad_json(monkeypatch):
    monkeypatch.setenv("HAVEN_STATS_SITES", "not-json")
    sites = haven_mod._parse_stats_sites()
    assert len(sites) == 4


def test_metric_value_reads_nested_and_flat():
    assert haven_mod._metric_value(3) == 3
    assert haven_mod._metric_value({"value": 9}) == 9
    assert haven_mod._metric_value(None) is None


@pytest.mark.asyncio
async def test_haven_stats_sites_missing_key(monkeypatch):
    monkeypatch.setenv("UMAMI_INTERNAL_URL", "http://umami.test:3000")
    monkeypatch.setenv("UMAMI_API_KEY", "")
    monkeypatch.setenv("HAVEN_STATS_SITES", "")
    haven_mod._STATS_CACHE["ts"] = 0.0
    haven_mod._STATS_CACHE["payload"] = None

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(haven_mod.router)

    async def _admin(_request):
        return {"id": "op"}

    with patch.object(haven_mod, "_require_admin", new=AsyncMock(side_effect=_admin)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/haven/stats/sites")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"] == "umami_api_key_missing"
    assert len(body["sites"]) == 4
    assert body["sites"][0]["error"] == "umami_api_key_missing"


@pytest.mark.asyncio
async def test_haven_stats_sites_ok_shape(monkeypatch):
    monkeypatch.setenv("UMAMI_INTERNAL_URL", "http://umami.test:3000")
    monkeypatch.setenv("UMAMI_API_KEY", "test-key")
    monkeypatch.setenv(
        "HAVEN_STATS_SITES",
        json.dumps(
            [
                {
                    "name": "Lucid Principles",
                    "domain": "lucidprinciples.com",
                    "umami_website_id": "14603d08-8822-4283-a6fd-65268c089cb6",
                }
            ]
        ),
    )
    haven_mod._STATS_CACHE["ts"] = 0.0
    haven_mod._STATS_CACHE["payload"] = None

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(haven_mod.router)

    async def _admin(_request):
        return {"id": "op"}

    calls = {"n": 0}

    async def fake_get(url, params=None, headers=None):
        calls["n"] += 1
        req = Request("GET", str(url))
        return Response(
            200,
            json={"pageviews": {"value": 10 + calls["n"]}, "visitors": {"value": 3}},
            request=req,
        )

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(haven_mod, "_require_admin", new=AsyncMock(side_effect=_admin)):
        with patch("httpx.AsyncClient", return_value=mock_client):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/haven/stats/sites")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert len(body["sites"]) == 1
    site = body["sites"][0]
    assert site["domain"] == "lucidprinciples.com"
    assert site["today"] is not None
    assert site["visitors_today"] == 3
    assert site["pageviews_7d"] is not None
    assert calls["n"] == 3
