"""After set_domain Matrix regen, Dendrite is wiped but cove_matrix may still
hold Space/Family room ids from the old homeserver. ensure must not treat those
as success — it must detect dead rooms, clear ids, and recreate.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.dashboard.routes import matrix_spaces as ms

MS_SRC = Path("src/dashboard/routes/matrix_spaces.py").read_text()
NET_SRC = Path("provision/netconfig.py").read_text()


@pytest.mark.asyncio
async def test_ensure_recreates_when_persisted_rooms_are_dead():
    calls = {"http": [], "save": []}
    create_n = {"n": 0}

    async def fake_http(method, path, token=None, body=None):
        calls["http"].append((method, path, body))
        if method == "GET" and "/state/m.room.create" in path:
            return 404, {"errcode": "M_NOT_FOUND"}
        if method == "POST" and path.endswith("/createRoom"):
            create_n["n"] += 1
            if (body or {}).get("creation_content", {}).get("type") == "m.space":
                return 200, {"room_id": "!newspace:matrix.example"}
            return 200, {"room_id": "!newfam:matrix.example"}
        if method == "PUT":
            return 200, {}
        return 200, {}

    async def fake_save(**kw):
        calls["save"].append(kw)

    with patch.object(ms, "_configured", return_value=True), \
         patch.object(ms, "_has_state_table", AsyncMock(return_value=True)), \
         patch.object(ms, "ensure_steward", AsyncMock(return_value={
             "user": "steward", "pw": "x", "token": "tok"})), \
         patch.object(ms, "_live_cove_name", AsyncMock(return_value="Ridgedale")), \
         patch.object(ms, "_presence_handles", AsyncMock(return_value=["thomas"])), \
         patch.object(ms, "_state", AsyncMock(return_value={
             "space_id": "!deadspace:old", "family_room_id": "!deadfam:old"})), \
         patch.object(ms, "_save_state", side_effect=fake_save), \
         patch.object(ms, "_http", side_effect=fake_http), \
         patch.object(ms, "_invite", AsyncMock()), \
         patch.object(ms, "_sync_space_to_registry", AsyncMock()), \
         patch.object(ms, "_server_name", return_value="matrix.example"), \
         patch.object(ms, "_uid", side_effect=lambda h: f"@{h}:matrix.example"):
        res = await ms.ensure_cove_space()

    assert res.get("ok") is True
    assert res.get("created") is True
    assert res.get("space_id") == "!newspace:matrix.example"
    assert res.get("room_id") == "!newfam:matrix.example"
    assert any(
        s.get("space_id") is None and s.get("family_room_id") is None
        for s in calls["save"]
    )
    assert any(s.get("sync_next_batch") is None for s in calls["save"])
    assert create_n["n"] == 2


@pytest.mark.asyncio
async def test_ensure_keeps_live_rooms():
    async def fake_http(method, path, token=None, body=None):
        if method == "GET" and "/state/m.room.create" in path:
            return 200, {"type": "m.room.create"}
        return 200, {}

    with patch.object(ms, "_configured", return_value=True), \
         patch.object(ms, "_has_state_table", AsyncMock(return_value=True)), \
         patch.object(ms, "ensure_steward", AsyncMock(return_value={
             "user": "steward", "pw": "x", "token": "tok"})), \
         patch.object(ms, "_live_cove_name", AsyncMock(return_value="Ridgedale")), \
         patch.object(ms, "_presence_handles", AsyncMock(return_value=["thomas"])), \
         patch.object(ms, "_state", AsyncMock(return_value={
             "space_id": "!live:s", "family_room_id": "!live:f"})), \
         patch.object(ms, "_http", side_effect=fake_http), \
         patch.object(ms, "_invite", AsyncMock()) as inv, \
         patch.object(ms, "_sync_space_to_registry", AsyncMock()), \
         patch.object(ms, "_uid", side_effect=lambda h: f"@{h}:x"):
        res = await ms.ensure_cove_space()

    assert res == {
        "ok": True, "space_id": "!live:s", "room_id": "!live:f", "created": False,
    }
    inv.assert_awaited()


def test_regen_clears_cove_matrix_helper_exists_and_is_called():
    assert "def _clear_cove_matrix_space_ids" in NET_SRC
    assert "_clear_cove_matrix_space_ids(" in NET_SRC
    assert 'core = {k: steps[k] for k in ("stop", "db_wipe", "config", "start")' in NET_SRC


def test_merchant_mxid_prefers_login_user_id():
    assert ms.merchant_mxid({
        "user": "mercer",
        "user_id": "@mercer:matrix.clearfield.localhost",
    }) == "@mercer:matrix.clearfield.localhost"
    rebuilt = ms.merchant_mxid({"user": "mercer", "user_id": ""})
    assert rebuilt.startswith("@mercer:")


def test_ensure_uses_login_mxid_not_rebuilt_uid():
    src = MS_SRC if "merchant_mxid(merchant)" in MS_SRC else Path(
        "src/dashboard/routes/matrix_spaces.py"
    ).read_text()
    assert "mid = merchant_mxid(merchant)" in src
    assert 'mid = _uid(merchant["user"])' not in src
    assert "user_id" in src.split("async def ensure_merchant")[1].split("async def _cove_merchant_label")[0]


@pytest.mark.asyncio
async def test_ensure_merchant_in_family_invites_live_mxid():
    invited = []
    joined = []

    async def fake_invite(token, rooms, user_ids):
        invited.append((token, rooms, user_ids))

    async def fake_join(token, room_id):
        joined.append((token, room_id))

    merchant = {
        "user": "mercer",
        "token": "mtok",
        "user_id": "@mercer:matrix.clearfield.localhost",
    }
    with patch.object(ms, "_joined_rooms", AsyncMock(return_value=set())), \
         patch.object(ms, "_invite", side_effect=fake_invite), \
         patch.object(ms, "_join_room", side_effect=fake_join):
        await ms.ensure_merchant_in_family(
            merchant,
            steward_token="stok",
            space_id="!space",
            room_id="!fam",
        )

    assert invited == [("stok", ["!space", "!fam"], ["@mercer:matrix.clearfield.localhost"])]
    assert joined == [("mtok", "!space"), ("mtok", "!fam")]


@pytest.mark.asyncio
async def test_ensure_merchant_in_family_skips_join_when_already_in():
    invited = []
    joined = []

    async def fake_invite(token, rooms, user_ids):
        invited.append((token, rooms, user_ids))

    async def fake_join(token, room_id):
        joined.append((token, room_id))

    merchant = {
        "user": "mercer",
        "token": "mtok",
        "user_id": "@mercer:matrix.clearfield.localhost",
    }
    with patch.object(ms, "_joined_rooms", AsyncMock(return_value={"!space", "!fam"})), \
         patch.object(ms, "_invite", side_effect=fake_invite), \
         patch.object(ms, "_join_room", side_effect=fake_join):
        await ms.ensure_merchant_in_family(
            merchant,
            steward_token="stok",
            space_id="!space",
            room_id="!fam",
        )

    assert invited == []
    assert joined == []


@pytest.mark.asyncio
async def test_invite_skips_users_already_joined():
    calls = []

    async def fake_http(method, path, token=None, body=None):
        calls.append((method, path, body))
        if method == "GET" and path.endswith("/joined_members"):
            return 200, {"joined": {"@mercer:x": {}, "@jag:x": {}}}
        return 200, {}

    with patch.object(ms, "_http", side_effect=fake_http):
        await ms._invite("stok", ["!fam"], ["@mercer:x", "@new:x"])

    posts = [c for c in calls if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2] == {"user_id": "@new:x"}


@pytest.mark.asyncio
async def test_join_room_skips_when_already_joined():
    calls = []

    async def fake_http(method, path, token=None, body=None):
        calls.append((method, path))
        if method == "GET" and path.endswith("/joined_rooms"):
            return 200, {"joined_rooms": ["!fam"]}
        return 200, {}

    with patch.object(ms, "_http", side_effect=fake_http):
        await ms._join_room("mtok", "!fam")

    assert not any(c[0] == "POST" for c in calls)


@pytest.mark.asyncio
async def test_displayname_put_skipped_when_already_set():
    calls = []

    async def fake_http(method, path, token=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {"displayname": "Stuart"}
        return 200, {}

    with patch.object(ms, "_http", side_effect=fake_http):
        await ms._set_cove_steward_displayname("tok", "@steward:x", "Stuart")

    assert [c[0] for c in calls] == ["GET"]


@pytest.mark.asyncio
async def test_displayname_put_when_label_changed():
    calls = []

    async def fake_http(method, path, token=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {"displayname": "steward"}
        return 200, {}

    with patch.object(ms, "_http", side_effect=fake_http):
        await ms._set_cove_steward_displayname("tok", "@steward:x", "Stuart")

    assert [c[0] for c in calls] == ["GET", "PUT"]
    assert calls[1][2] == {"displayname": "Stuart"}


def test_invite_presence_routes_through_ensure():
    fn = MS_SRC.split("async def invite_presence_to_cove_space")[1].split(
        "@router.post"
    )[0]
    assert "return await ensure_cove_space()" in fn
    assert "await _invite(steward" not in fn
