"""AUDIT-F1 — xAI OAuth token refresh must be single-flight and rotation-safe.

Without serialization, concurrent callers all present the same refresh_token; a
rotating server rejects the losers as invalid_grant, which used to wipe the token
cache and wedge the (primary) provider until a manual device-code re-auth. These
tests lock: (1) concurrent refreshes coalesce to ONE network refresh, and (2) an
invalid_grant caused by a rotation elsewhere reuses the fresh on-disk token instead
of deleting the cache; only a genuine revoke (token unchanged) clears it.
"""
import asyncio
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import src.models.xai_oauth as xo  # noqa: E402


class _MockResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = str(json_data)

    def json(self):
        return self._json


class _MockClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return self._resp


@pytest.mark.asyncio
async def test_concurrent_refreshes_coalesce_to_one(monkeypatch):
    """Ten callers with an expired token trigger exactly ONE refresh; the rest reuse."""
    cache = {"access_token": "old", "refresh_token": "r1", "expires_at": 0}  # expired
    monkeypatch.setattr(xo, "_load_cached_tokens", lambda: cache)

    calls = {"n": 0}

    async def _fake_refresh(refresh_token):
        calls["n"] += 1
        await asyncio.sleep(0)  # yield so other coroutines pile up on the lock
        cache["access_token"] = "new"
        cache["refresh_token"] = "r2"
        cache["expires_at"] = xo.time.time() + 3600
        return dict(cache)

    monkeypatch.setattr(xo, "refresh_access_token", _fake_refresh)

    results = await asyncio.gather(*[xo.get_valid_access_token() for _ in range(10)])

    assert calls["n"] == 1, "refresh must happen exactly once under the lock"
    assert all(r == "new" for r in results)


@pytest.mark.asyncio
async def test_invalid_grant_reuses_rotated_token(monkeypatch):
    """If the on-disk refresh_token was rotated by another refresher, invalid_grant
    reuses that fresh token rather than deleting the cache."""
    monkeypatch.setattr(xo, "_get_oauth_config", lambda: {"client_id": "x", "client_secret": "y"})
    # Disk already holds a NEWER token than the one we present.
    monkeypatch.setattr(xo, "_load_cached_tokens",
                        lambda: {"refresh_token": "r_new", "access_token": "good"})
    deleted = {"n": 0}
    monkeypatch.setattr(xo, "_delete_cached_tokens", lambda: deleted.__setitem__("n", deleted["n"] + 1))
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _MockClient(_MockResp(400, {"error": "invalid_grant"})))

    result = await xo.refresh_access_token("r_old")   # stale token we held

    assert result["access_token"] == "good"           # reused the rotated token
    assert deleted["n"] == 0                           # cache NOT wiped


@pytest.mark.asyncio
async def test_invalid_grant_genuine_revoke_deletes_and_raises(monkeypatch):
    """A real revoke (on-disk token unchanged from the one presented) clears the
    cache and raises for re-auth."""
    monkeypatch.setattr(xo, "_get_oauth_config", lambda: {"client_id": "x", "client_secret": "y"})
    monkeypatch.setattr(xo, "_load_cached_tokens",
                        lambda: {"refresh_token": "r_same", "access_token": "old"})
    deleted = {"n": 0}
    monkeypatch.setattr(xo, "_delete_cached_tokens", lambda: deleted.__setitem__("n", deleted["n"] + 1))
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _MockClient(_MockResp(400, {"error": "invalid_grant"})))

    with pytest.raises(ValueError):
        await xo.refresh_access_token("r_same")
    assert deleted["n"] == 1
