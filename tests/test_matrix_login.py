# batch-9 #2: the Matrix login self-heal hinges on _try_login returning a STRUCTURED
# result (errcode, not just a 502) so matrix_token can branch: M_FORBIDDEN → re-register
# once; M_LIMIT_EXCEEDED → back off, never re-register. This is the run-3 "register 200'd
# but login M_FORBIDDEN" regression surface (the account was gone under stale app creds).
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.matrix as m  # noqa: E402


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return self._resp


def _patch(monkeypatch, resp):
    monkeypatch.setattr(m.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))


@pytest.mark.asyncio
async def test_try_login_success(monkeypatch):
    _patch(monkeypatch, _Resp(200, {"user_id": "@ernie:matrix.x.org",
                                    "access_token": "tok", "device_id": "D1"}))
    r = await m._try_login("http://dendrite:8008", "ernie", "pw")
    assert r["ok"] is True
    assert r["data"]["access_token"] == "tok"
    assert r["data"]["user_id"] == "@ernie:matrix.x.org"


@pytest.mark.asyncio
async def test_try_login_forbidden_surfaces_errcode(monkeypatch):
    _patch(monkeypatch, _Resp(403, {"errcode": "M_FORBIDDEN", "error": "bad"},
                              text='{"errcode":"M_FORBIDDEN"}'))
    r = await m._try_login("http://dendrite:8008", "ernie", "pw")
    assert r["ok"] is False
    assert r["errcode"] == "M_FORBIDDEN"   # what triggers the re-register self-heal


@pytest.mark.asyncio
async def test_try_login_rate_limited_surfaces_errcode(monkeypatch):
    _patch(monkeypatch, _Resp(429, {"errcode": "M_LIMIT_EXCEEDED"},
                              text='{"errcode":"M_LIMIT_EXCEEDED"}'))
    r = await m._try_login("http://dendrite:8008", "ernie", "pw")
    assert r["ok"] is False
    assert r["errcode"] == "M_LIMIT_EXCEEDED"   # back-off path, NOT re-register


@pytest.mark.asyncio
async def test_try_login_unreachable(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connrefused")

    monkeypatch.setattr(m.httpx, "AsyncClient", lambda *a, **k: _Boom())
    r = await m._try_login("http://dendrite:8008", "ernie", "pw")
    assert r["ok"] is False
    assert r.get("unreachable") is True
