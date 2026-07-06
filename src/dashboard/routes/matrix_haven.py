# =============================================================================
# matrix_haven.py — operator-owned Haven Matrix Space (#160 / #137 Phase B)
# =============================================================================
# A Haven connects Coves. It is a Matrix Space + a Commons room, OWNED by the
# operator who forms it (their per-Cove Matrix account, @handle:matrix.{cove}...).
# This is the Haven-level mirror of matrix_spaces.py (which builds the Cove Space):
#   - ensure_haven_space(): create the Haven Space + Commons once (idempotent,
#     ids persisted in cove_haven and mirrored to the Hub registrar #133),
#   - nest member Coves: m.space.child in the Haven Space pointing at each member
#     Cove's Space (resolved via the registrar) so a whole Cove shows up nested
#     under the Haven in clients,
#   - invite federated member @handles to the Space + Commons.
#
# Replaces the manual matrix-haven-setup.py / matrix-haven-sync.py + the
# hand-edited network.yaml (the registrar is now the source of truth).
#
# Cross-homeserver federation between per-Cove Dendrites must be proven on the box
# first (the founder homeserver needs the deny_networks mesh fix; member Coves need
# real domains + DNS). Until then these calls succeed locally but federated invites
# to other homeservers won't deliver. See the integration-test runbook.
# =============================================================================
import logging
import os
from src.env import env
import urllib.parse as _up

import httpx
from fastapi import APIRouter, Request, HTTPException

from src.dashboard.routes.matrix import _login
from src.dashboard.routes.presence import get_current_presence
from src.dashboard.routes import registry_client

log = logging.getLogger(__name__)
router = APIRouter()


def _internal() -> str:
    return (env("MATRIX_HUB_URL") or "").rstrip("/")


def _server_name() -> str:
    # Same staleness fix as matrix_spaces._server_name: after a domain claim the
    # homeserver is matrix.{domain}, but the provision-stamped MATRIX_SERVER_NAME env
    # stays matrix.{cove-id}.localhost. The Haven layer builds cross-Cove invites from
    # this Cove's server_name, so a stale value makes every Haven invite federate to a
    # dead .localhost host. Prefer the live cove domain.
    try:
        from src.config import load_cove_config
        dom = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".").lower()
        if dom:
            return "matrix.%s" % dom
    except Exception:
        pass
    return env("MATRIX_SERVER_NAME") or ""


def _configured() -> bool:
    return bool(_internal() and _server_name())


async def _has_state_table() -> bool:
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            r = await conn.execute("SELECT to_regclass('public.cove_haven') AS t")
            row = await r.fetchone()
        return bool(row and row.get("t"))
    except Exception:
        return False


async def _http(method: str, path: str, token: str = None, body: dict = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(method, _internal() + path, headers=headers, json=body)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


# ── Owner identity (the operator forming the Haven) ──────────────────────────

async def _owner_token(request: Request) -> tuple[str, str]:
    """Log in as the current operator's per-Cove Matrix account. Returns (user_id, token).
    The operator must have a Matrix account already (auto-provisioned on first Connect)."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to manage a Haven")
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT matrix_username, matrix_password FROM accounts WHERE id = %s", (presence["id"],))
        row = await r.fetchone()
    user = (row or {}).get("matrix_username")
    pw = (row or {}).get("matrix_password")
    if not (user and pw):
        raise HTTPException(409, "Open Connect once first so your Matrix identity exists")
    login = await _login(_internal(), user, pw)
    return login["user_id"], login["access_token"]


# ── cove_haven state ─────────────────────────────────────────────────────────

async def _haven_state(haven_id: str) -> dict:
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT haven_id, name, owner_user, space_id, commons_id FROM cove_haven WHERE haven_id = %s",
            (haven_id,))
        row = await r.fetchone()
    return dict(row) if row else {}


async def _save_haven(haven_id: str, **kw):
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO cove_haven (haven_id) VALUES (%s) ON CONFLICT (haven_id) DO NOTHING", (haven_id,))
        if kw:
            sets = ", ".join("%s = %%s" % k for k in kw)  # keys are internal constants
            await conn.execute(
                "UPDATE cove_haven SET %s, updated_at = NOW() WHERE haven_id = %%s" % sets,
                tuple(kw.values()) + (haven_id,))


async def _invite(token: str, rooms: list, user_ids: list) -> list:
    """Invite user_ids into rooms. Returns a list of FAILURE dicts (batch-10 #4c) so the
    caller can surface a real 'couldn't deliver' message instead of the old silent swallow
    that made the T9 federation invite failure invisible. Already-invited/joined (M_FORBIDDEN)
    is NOT a failure."""
    failures = []
    for room in rooms:
        if not room:
            continue
        rq = _up.quote(room)
        for uid in user_ids:
            if not uid:
                continue
            s, r = await _http("POST", "/_matrix/client/v3/rooms/%s/invite" % rq, token, {"user_id": uid})
            if s != 200 and r.get("errcode") != "M_FORBIDDEN":
                log.warning("haven invite %s -> %s: %s %s", uid, room, s, r.get("error"))
                failures.append({"user": uid, "room": room, "status": s,
                                 "error": r.get("error") or r.get("errcode") or ("HTTP %s" % s)})
    return failures


# ── Build / sync the Haven Space ─────────────────────────────────────────────

async def ensure_haven_space(request: Request, haven_id: str, name: str, members: list = None) -> dict:
    """Idempotently ensure the operator-owned Haven Space + Commons exist and that
    `members` (federated @user ids) are invited. Mirrors ids to the registrar."""
    if not _configured():
        return {"ok": False, "reason": "matrix not configured"}
    if not await _has_state_table():
        return {"ok": False, "reason": "cove_haven table absent — Haven Spaces disabled here"}
    owner_user, tok = await _owner_token(request)
    members = [m for m in (members or []) if m and m != owner_user]

    st = await _haven_state(haven_id)
    space_id, commons_id = st.get("space_id"), st.get("commons_id")
    if space_id and commons_id:
        await _invite(tok, [space_id, commons_id], members)
        await _sync_registry(haven_id, name, owner_user, space_id, commons_id, members)
        return {"ok": True, "haven_id": haven_id, "space_id": space_id,
                "commons_id": commons_id, "created": False}

    # Haven Space (m.space)
    s, r = await _http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": name, "topic": "A haven of connected Coves",
        "creation_content": {"type": "m.space"},
        "preset": "private_chat", "visibility": "private", "invite": members})
    if s != 200:
        raise HTTPException(502, "Create Haven Space failed: %s" % r)
    space_id = r["room_id"]

    # Commons room
    s, r = await _http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": "%s Commons" % name, "topic": "Commons for %s" % name,
        "preset": "private_chat", "visibility": "private", "invite": members})
    if s != 200:
        raise HTTPException(502, "Create Commons room failed: %s" % r)
    commons_id = r["room_id"]

    sn = _server_name()
    await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.child/%s"
                % (_up.quote(space_id), _up.quote(commons_id)), tok, {"via": [sn]})
    await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.parent/%s"
                % (_up.quote(commons_id), _up.quote(space_id)), tok, {"via": [sn], "canonical": True})

    await _save_haven(haven_id, name=name, owner_user=owner_user, space_id=space_id, commons_id=commons_id)
    await _sync_registry(haven_id, name, owner_user, space_id, commons_id, members)
    log.info("Built Haven Space %s (Commons %s) for %s", space_id, commons_id, name)
    return {"ok": True, "haven_id": haven_id, "space_id": space_id, "commons_id": commons_id, "created": True}


async def nest_member_cove(request: Request, haven_id: str, cove_key: str) -> dict:
    """Nest a member Cove's Space under the Haven Space (m.space.child → the Cove's
    space_id, via the Cove's homeserver). Resolves the Cove from the registrar."""
    st = await _haven_state(haven_id)
    if not st.get("space_id"):
        raise HTTPException(409, "Build the Haven Space first")
    owner_user, tok = await _owner_token(request)
    cove = await registry_client.resolve_cove(cove_key)
    if not cove.get("ok"):
        raise HTTPException(404, "Cove not in registry: %s" % cove.get("reason"))
    child_space = cove.get("space_id")
    via = cove.get("homeserver") or _server_name()
    if not child_space:
        raise HTTPException(409, "That Cove has no Space registered yet")
    s, r = await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.child/%s"
                       % (_up.quote(st["space_id"]), _up.quote(child_space)), tok, {"via": [via]})
    if s != 200:
        raise HTTPException(502, "Nest member Cove failed: %s" % r)
    # Record the membership in the registrar (best-effort).
    await registry_client.add_haven_member(
        haven_id, cove={"cove_id": cove.get("cove_id"), "space_id": child_space, "homeserver": via})
    _cn = cove.get("name") or cove.get("cove_id") or "The Cove"
    return {"ok": True, "nested": child_space, "via": via,
            "message": "Nested %s into the Haven." % _cn}


async def _sync_registry(haven_id, name, owner_user, space_id, commons_id, members):
    owner_handle = owner_user.lstrip("@").split(":")[0] if owner_user else ""
    res = await registry_client.upsert_haven(
        haven_id=haven_id, name=name, owner_handle=owner_handle,
        space_id=space_id, commons_id=commons_id, members=members)
    if not res.get("ok"):
        log.info("Haven registry sync skipped: %s", res.get("reason"))


# ── API ──────────────────────────────────────────────────────────────────────

@router.post("/api/haven/create")
async def api_create_haven(request: Request):
    """Form a Haven. Body: haven_id, name, members[] (federated @user ids, optional)."""
    body = await request.json()
    hid = (body.get("haven_id") or "").strip()
    name = (body.get("name") or "").strip()
    if not (hid and name):
        raise HTTPException(400, "haven_id and name are required")
    return await ensure_haven_space(request, hid, name, body.get("members") or [])


@router.post("/api/haven/{haven_id}/nest")
async def api_nest_cove(haven_id: str, request: Request):
    """Nest a member Cove under the Haven. Body: cove (id or name)."""
    body = await request.json()
    cove_key = (body.get("cove") or "").strip()
    if not cove_key:
        raise HTTPException(400, "cove (id or name) is required")
    return await nest_member_cove(request, haven_id, cove_key)


@router.post("/api/haven/{haven_id}/invite")
async def api_invite_member(haven_id: str, request: Request):
    """Invite a federated member @handle to the Haven Space + Commons."""
    body = await request.json()
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(400, "user_id (@handle:server) is required")
    st = await _haven_state(haven_id)
    if not st.get("space_id"):
        raise HTTPException(409, "Build the Haven Space first")
    owner_user, tok = await _owner_token(request)
    failures = await _invite(tok, [st["space_id"], st["commons_id"]], [user_id])
    await registry_client.add_haven_member(haven_id, handle=user_id.lstrip("@").split(":")[0])
    # The membership is recorded either way; report whether the Matrix invite actually
    # landed so the UI shows a real result instead of a blanket "Invited." (T9).
    if failures:
        return {"ok": True, "delivered": False, "user_id": user_id,
                "message": "Added %s to the Haven, but the chat invite couldn't be delivered "
                           "(%s). They'll join when their Cove is reachable." % (user_id, failures[0]["error"])}
    return {"ok": True, "delivered": True, "user_id": user_id, "message": "Invited %s." % user_id}


@router.get("/api/haven/{haven_id}")
async def api_get_haven(haven_id: str):
    st = await _haven_state(haven_id)
    if not st.get("space_id"):
        # fall back to the registry view
        reg = await registry_client.resolve_haven(haven_id)
        if reg.get("ok"):
            return reg
        raise HTTPException(404, "Haven not found")
    return st
