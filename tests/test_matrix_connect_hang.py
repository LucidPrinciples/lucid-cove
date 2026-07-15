# Connect hang regression (task #23 / Quietgrove):
# /api/matrix/token must return as soon as Matrix login succeeds. Cove Space
# ensure/invite used to run on the request path — slow/stuck Dendrite work held
# the HTTP response and the browser sat on a silent "Connecting…" for 10–15m.
# Space ensure is now fire-and-forget; this locks that contract.
import asyncio
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.matrix as m  # noqa: E402


class _Req:
    def __init__(self, host="quietgrove.lucidcove.org"):
        self.url = MagicMock()
        self.url.hostname = host


@pytest.mark.asyncio
async def test_kick_space_ensure_schedules_background_task(monkeypatch):
    """_kick_space_ensure must not await invite — it schedules a task."""
    called = {"n": 0}
    started = asyncio.Event()

    async def slow_invite(handle):
        called["n"] += 1
        called["handle"] = handle
        started.set()
        await asyncio.sleep(60)  # would hang the request if awaited

    import src.dashboard.routes.matrix_spaces as ms
    monkeypatch.setattr(ms, "invite_presence_to_cove_space", slow_invite)

    m._kick_space_ensure("ernie")
    # Give the task a tick to start — but _kick itself must have returned already.
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert called["n"] == 1
    assert called["handle"] == "ernie"


@pytest.mark.asyncio
async def test_matrix_token_multi_returns_before_space_finishes(monkeypatch):
    """Token response must not wait on invite_presence_to_cove_space."""
    monkeypatch.setattr(m, "COVE_MODE", "multi")
    monkeypatch.setattr(m, "env", lambda k, d=None: {
        "MATRIX_HUB_URL": "http://dendrite:8008",
    }.get(k, d if d is not None else ""))

    presence = {"id": "p1", "username": "ernie"}
    monkeypatch.setattr(m, "get_current_presence", AsyncMock(return_value=presence))

    class _Conn:
        async def execute(self, *a, **k):
            class _R:
                async def fetchone(self_inner):
                    return {"matrix_username": "ernie", "matrix_password": "pw"}
            return _R()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _DB:
        def __call__(self):
            return _Conn()

    monkeypatch.setattr("src.memory.database.get_db", _DB())

    async def ok_login(hs, user, pw):
        return {
            "ok": True,
            "data": {
                "homeserver": hs,
                "user_id": "@ernie:matrix.quietgrove.lucidcove.org",
                "access_token": "tok",
                "device_id": "D1",
            },
        }

    monkeypatch.setattr(m, "_try_login", ok_login)
    monkeypatch.setattr(m, "_client_homeserver", lambda req: "https://matrix.quietgrove.lucidcove.org")

    space_entered = asyncio.Event()
    space_released = asyncio.Event()

    async def blocking_invite(handle):
        space_entered.set()
        await space_released.wait()

    import src.dashboard.routes.matrix_spaces as ms
    monkeypatch.setattr(ms, "invite_presence_to_cove_space", blocking_invite)

    t0 = asyncio.get_event_loop().time()
    result = await asyncio.wait_for(m.matrix_token(_Req()), timeout=1.0)
    elapsed = asyncio.get_event_loop().time() - t0

    assert result["access_token"] == "tok"
    assert result["homeserver"] == "https://matrix.quietgrove.lucidcove.org"
    # Must return without waiting on the blocked space ensure.
    assert elapsed < 0.9
    # Background task should have started (or be about to).
    await asyncio.wait_for(space_entered.wait(), timeout=1.0)
    space_released.set()


@pytest.mark.asyncio
async def test_matrix_token_single_rewrites_homeserver_for_browser(monkeypatch):
    monkeypatch.setattr(m, "COVE_MODE", "single")

    def _env(k, d=None):
        return {
            "MATRIX_HOMESERVER": "http://dendrite:8008",
            "MATRIX_OPERATOR_USER": "operator",
            "MATRIX_OPERATOR_PASSWORD": "secret",
        }.get(k, d if d is not None else "")

    monkeypatch.setattr(m, "env", _env)

    async def ok_login(hs, user, pw):
        return {
            "homeserver": hs,
            "user_id": "@operator:matrix.example.org",
            "access_token": "tok",
            "device_id": "D1",
        }

    monkeypatch.setattr(m, "_login", ok_login)
    monkeypatch.setattr(m, "_client_homeserver", lambda req: "https://matrix.example.org")
    monkeypatch.setattr(m, "_kick_space_ensure", lambda handle: None)

    result = await m.matrix_token(_Req(host="example.org"))
    assert result["access_token"] == "tok"
    assert result["homeserver"] == "https://matrix.example.org"
