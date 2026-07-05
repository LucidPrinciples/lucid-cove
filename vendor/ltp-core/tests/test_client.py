"""DropClient — fetch, verify, cache, offline fallback (SPEC Sections 8, 9)."""

import copy
import json

import httpx
import pytest

import lucid_tuner_protocol as ltp
from lucid_tuner_protocol.drop import client as client_mod


class _MockTransport:
    """Route DropClient HTTP calls to fixture content."""

    def __init__(self, routes: dict, fail: bool = False):
        self.routes = routes
        self.fail = fail

    def __call__(self, *args, **kwargs):
        outer = self

        class _Client:
            def __init__(self, *a, **k): ...
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def get(self, url):
                if outer.fail:
                    raise httpx.ConnectError("network down")
                for suffix, body in outer.routes.items():
                    if url.endswith(suffix):
                        req = httpx.Request("GET", url)
                        if isinstance(body, dict):
                            return httpx.Response(200, json=body, request=req)
                        return httpx.Response(200, text=body, request=req)
                req = httpx.Request("GET", url)
                resp = httpx.Response(404, request=req)
                raise httpx.HTTPStatusError("404", request=req, response=resp)

        return _Client()


def _client(tmp_path, monkeypatch, real_drop, publisher_pem, fail=False):
    transport = _MockTransport(
        {"/latest.json": real_drop, "/keys/ltp-publisher.pub": publisher_pem},
        fail=fail,
    )
    monkeypatch.setattr(client_mod.httpx, "Client", transport)
    return ltp.DropClient(cache_dir=tmp_path / "cache")


def test_today_fetches_verifies_and_caches(tmp_path, monkeypatch, real_drop, publisher_pem):
    c = _client(tmp_path, monkeypatch, real_drop, publisher_pem)
    drop = c.today()
    assert drop.sequence == 1
    assert (tmp_path / "cache" / "latest.json").exists()
    assert (tmp_path / "cache" / "drop-000001.json").exists()


def test_offline_falls_back_to_cache(tmp_path, monkeypatch, real_drop, publisher_pem):
    # First call online: populates cache
    c = _client(tmp_path, monkeypatch, real_drop, publisher_pem)
    c.today()
    # Second call offline: serves the cached verified drop
    c2 = _client(tmp_path, monkeypatch, real_drop, publisher_pem, fail=True)
    drop = c2.today()
    assert drop.sequence == 1


def test_offline_with_no_cache_raises(tmp_path, monkeypatch, real_drop, publisher_pem):
    c = _client(tmp_path, monkeypatch, real_drop, publisher_pem, fail=True)
    with pytest.raises(ltp.DropUnavailable):
        c.today()


def test_tampered_drop_from_network_rejected_then_cache_used(
    tmp_path, monkeypatch, real_drop, publisher_pem
):
    # Populate cache with the good drop
    c = _client(tmp_path, monkeypatch, real_drop, publisher_pem)
    c.today()

    # Server now returns a tampered drop — client must fall back to cache
    tampered = copy.deepcopy(real_drop)
    tampered["context_block"] = "injected"
    transport = _MockTransport(
        {"/latest.json": tampered, "/keys/ltp-publisher.pub": publisher_pem}
    )
    monkeypatch.setattr(client_mod.httpx, "Client", transport)
    c2 = ltp.DropClient(cache_dir=tmp_path / "cache")
    drop = c2.today()
    assert drop.context_block == real_drop["context_block"]  # cache, not tampered


def test_pinned_public_key(tmp_path, monkeypatch, real_drop, publisher_pem):
    """Subscribers can pin the publisher key instead of fetching it."""
    transport = _MockTransport({"/latest.json": real_drop})  # no key route
    monkeypatch.setattr(client_mod.httpx, "Client", transport)
    c = ltp.DropClient(cache_dir=tmp_path / "cache", public_key_pem=publisher_pem)
    assert c.today().sequence == 1
