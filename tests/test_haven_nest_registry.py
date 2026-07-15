"""
Haven nest registry self-heal (2026-07-15) — product path, not admin checklist.

A new user must never be told to POST /api/admin/matrix/ensure-space or hand-edit
registry rows to nest a Cove. Nest either:
  - self-publishes when the target is THIS Cove, or
  - returns product language: open Connect on the member Cove once.

These tests pin the error copy and the local self-heal branch without live Matrix/hub.
"""

import sys
import pathlib
from unittest.mock import AsyncMock

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.matrix_haven as mh  # noqa: E402


def test_missing_registry_message_is_product_language_not_admin():
    msg = mh._nest_missing_registry_message("Mann")
    assert "Mann" in msg
    assert "Connect" in msg
    assert "ensure-space" not in msg.lower()
    assert "registry" not in msg.lower() or "not on the network" in msg
    assert "POST" not in msg
    assert "admin" not in msg.lower() or "No admin tools" in msg


def test_missing_space_message_points_at_connect():
    msg = mh._nest_missing_space_message("Mann", "Mann")
    assert "Connect" in msg
    assert "Space" in msg
    assert "ensure-space" not in msg.lower()


@pytest.mark.asyncio
async def test_nest_missing_remote_cove_product_error(monkeypatch):
    """Remote Cove not on hub → 404 with product copy, no admin ceremony."""
    monkeypatch.setattr(mh, "_haven_state", AsyncMock(return_value={
        "space_id": "!haven:x", "commons_id": "!commons:x",
    }))
    monkeypatch.setattr(mh, "_operator_matrix_id", AsyncMock(return_value=("@op:x", "op")))
    monkeypatch.setattr(mh, "ensure_haven_steward", AsyncMock(return_value={
        "token": "tok", "user": "@havensteward:x", "pw": "p",
    }))
    monkeypatch.setattr(mh.registry_client, "resolve_cove", AsyncMock(return_value={
        "ok": False, "reason": "No Cove 'Mann' in the registry",
    }))
    monkeypatch.setattr(mh, "_try_publish_local_if_matching", AsyncMock(return_value={
        "ok": False, "reason": "not local cove",
    }))

    with pytest.raises(Exception) as ei:
        await mh.nest_member_cove(request=object(), haven_id="woods", cove_key="Mann")
    exc = ei.value
    detail = getattr(exc, "detail", str(exc))
    assert getattr(exc, "status_code", 404) == 404
    assert "Mann" in detail
    assert "Connect" in detail
    assert "ensure-space" not in detail.lower()


@pytest.mark.asyncio
async def test_nest_self_heals_when_target_is_local(monkeypatch):
    """If nest key matches this Cove and local publish succeeds, nest continues."""
    monkeypatch.setattr(mh, "_haven_state", AsyncMock(return_value={
        "space_id": "!haven:x", "commons_id": "!commons:x",
    }))
    monkeypatch.setattr(mh, "_operator_matrix_id", AsyncMock(return_value=("@op:x", "op")))
    monkeypatch.setattr(mh, "ensure_haven_steward", AsyncMock(return_value={
        "token": "tok", "user": "@havensteward:x", "pw": "p",
    }))
    monkeypatch.setattr(mh, "_server_name", lambda: "matrix.woods.example")

    resolves = [
        {"ok": False, "reason": "missing"},
        {
            "ok": True, "cove_id": "mann", "name": "Mann",
            "space_id": "!child:x", "homeserver": "matrix.mann.example",
            "owner_handle": "bob",
        },
    ]

    async def _resolve(key):
        return resolves.pop(0) if resolves else resolves[-1:]

    # After self-heal, second resolve returns the row
    async def _resolve_seq(key):
        if not hasattr(_resolve_seq, "n"):
            _resolve_seq.n = 0
        _resolve_seq.n += 1
        if _resolve_seq.n == 1:
            return {"ok": False, "reason": "missing"}
        return {
            "ok": True, "cove_id": "mann", "name": "Mann",
            "space_id": "!child:x", "homeserver": "matrix.mann.example",
            "owner_handle": "bob",
        }

    monkeypatch.setattr(mh.registry_client, "resolve_cove", AsyncMock(side_effect=_resolve_seq))
    monkeypatch.setattr(mh, "_try_publish_local_if_matching", AsyncMock(return_value={
        "ok": True, "cove_id": "mann", "name": "Mann", "space_id": "!child:x",
    }))
    monkeypatch.setattr(mh, "_http", AsyncMock(return_value=(200, {})))
    monkeypatch.setattr(mh, "_invite", AsyncMock(return_value=[]))
    monkeypatch.setattr(mh.registry_client, "add_haven_member", AsyncMock(return_value={"ok": True}))

    out = await mh.nest_member_cove(request=object(), haven_id="woods", cove_key="Mann")
    assert out.get("ok") is True
    assert out.get("nested") == "!child:x"
    assert "Mann" in (out.get("message") or "")


@pytest.mark.asyncio
async def test_publish_cove_to_registry_skips_unnamed(monkeypatch):
    import src.dashboard.routes.matrix_spaces as ms
    import src.dashboard.routes.registry_client as rc

    # publish imports registry_client inside the function body
    monkeypatch.setattr(rc, "configured", lambda: True)
    monkeypatch.setattr(ms, "_live_cove_name", AsyncMock(return_value="New Cove"))
    monkeypatch.setattr(ms, "_state", AsyncMock(return_value={}))
    monkeypatch.setattr(ms, "_admin_handle_and_domain", AsyncMock(return_value=("", "")))

    res = await ms.publish_cove_to_registry(cove_name="New Cove")
    assert res.get("ok") is False
    assert "not named" in (res.get("reason") or "").lower()


@pytest.mark.asyncio
async def test_publish_cove_to_registry_registers_and_clears_pending(monkeypatch):
    import src.dashboard.routes.matrix_spaces as ms
    import src.dashboard.routes.registry_client as rc

    monkeypatch.setattr(rc, "configured", lambda: True)
    monkeypatch.setattr(rc, "register_cove", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(ms, "_admin_handle_and_domain", AsyncMock(return_value=("jag", "woods.example")))
    monkeypatch.setattr(ms, "_server_name", lambda: "matrix.woods.example")
    monkeypatch.setattr(ms, "_state", AsyncMock(return_value={"space_id": "!s:x"}))
    monkeypatch.setattr(ms, "env", lambda k, d="": {
        "COVE_ID": "woods-1",
    }.get(k, d))

    cleared = {"n": 0}

    async def _clear():
        cleared["n"] += 1

    monkeypatch.setattr("src.utils.hub_retry.clear_registration_pending", _clear)

    res = await ms.publish_cove_to_registry(cove_name="Woods", space_id="!s:x")
    assert res.get("ok") is True
    assert res.get("name") == "Woods"
    assert res.get("space_id") == "!s:x"
    assert cleared["n"] == 1
    rc.register_cove.assert_awaited()
