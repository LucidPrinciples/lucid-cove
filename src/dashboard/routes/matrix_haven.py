# =============================================================================
# matrix_haven.py — steward-owned Haven Matrix Space (#160 / #137 Phase B)
# =============================================================================
# A Haven connects Coves. It is a Matrix Space + a Commons room OWNED by the Haven
# steward (a durable agent on the founding Cove, ensure_haven_steward) so it survives
# operator churn (§2, 2026-07-06); the founding operator is the recorded human owner
# and is invited in. Nest + invite also execute through the steward token.
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
# Cross-homeserver federation between per-Cove Dendrites is PROVEN delivering (2026-07-06:
# two co-located Coves on real matrix.{domain} names, resolving over the mesh, formed a
# Haven and federated the Commons end to end). Requirements: each Cove reachable at its
# matrix.{domain} (deny_networks allows the 100.64.0.0/10 mesh; remote Coves need public DNS).
# =============================================================================
import logging
import os
from src.env import env
import urllib.parse as _up

import httpx
from fastapi import APIRouter, Request, HTTPException

from src.dashboard.routes.matrix import register_matrix_account, _try_login
from src.dashboard.routes.presence import get_current_presence
from src.dashboard.routes import registry_client

log = logging.getLogger(__name__)
router = APIRouter()

# The durable Haven steward's Matrix localpart. Dot-free + distinct from the Cove steward's
# ("steward"), so the two namespaces can't collide on the founding Cove's homeserver. v1
# assumes one Haven per founding Cove (matches haven._owned_haven's LIMIT 1).
HAVEN_STEWARD_LOCALPART = env("MATRIX_HAVEN_STEWARD_LOCALPART", "havensteward")


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

async def _operator_matrix_id(request: Request) -> tuple[str, str]:
    """(@matrix_id, handle) for the CURRENT operator managing the Haven — the human owner.
    Derived from accounts.matrix_username (set on first Connect) WITHOUT logging in as them:
    the steward now owns the rooms, so we only need the operator's id to invite them in and to
    record them as the Haven's human owner in the registry."""
    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Sign in to manage a Haven")
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT matrix_username FROM accounts WHERE id = %s", (presence["id"],))
        row = await r.fetchone()
    mu = (row or {}).get("matrix_username") or ""
    handle = (presence.get("username") or "").lstrip("@").strip().lower()
    sn = _server_name()
    mx = ("@%s:%s" % (mu, sn)) if (mu and sn) else ""
    return mx, handle


async def ensure_haven_steward(haven_id: str) -> dict:
    """Return {user, pw, token} for the Haven's steward Matrix identity, provisioning it once
    (admin account, shared-secret) on the FOUNDING Cove's homeserver. The steward OWNS the Haven
    Space + Commons so they survive operator churn (§2) — the Haven-level mirror of
    matrix_spaces.ensure_steward. SELF-HEALING: stored creds that fail login with M_FORBIDDEN mean
    the account was cleared under us; re-register once, persist durably, retry. If it still fails,
    surface ONE actionable host command instead of looping."""
    st = await _haven_state(haven_id)
    user, pw = st.get("steward_username"), st.get("steward_password")
    hs = _internal()
    if not (user and pw):
        user, pw = await register_matrix_account(HAVEN_STEWARD_LOCALPART, admin=True)
        await _save_haven(haven_id, steward_username=user, steward_password=pw)

    login = await _try_login(hs, user, pw)
    if not login.get("ok"):
        ec = (login.get("errcode") or "").upper()
        if login.get("unreachable"):
            raise HTTPException(502, "Matrix homeserver unreachable: %s" % login.get("body"))
        if ec == "M_LIMIT_EXCEEDED":
            raise HTTPException(429, "Matrix is warming up (rate limited) — retry shortly.")
        if ec in ("M_FORBIDDEN", "M_USER_DEACTIVATED", "M_UNKNOWN"):
            try:
                user, pw = await register_matrix_account(HAVEN_STEWARD_LOCALPART, admin=True)
                await _save_haven(haven_id, steward_username=user, steward_password=pw)
                login = await _try_login(hs, user, pw)
            except HTTPException as re:
                log.warning("haven steward self-heal re-register failed: %s", getattr(re, "detail", re))
        if not login.get("ok"):
            raise HTTPException(
                503,
                "Haven steward Matrix account is stuck (registered but login forbidden — a "
                "partial delete left a ghost). On the Cove host run: python3 "
                "/cove-core/provision/set_domain.py --remove-matrix-user %s --cove-id <cove_id>, "
                "then retry." % HAVEN_STEWARD_LOCALPART,
            )
    return {"user": login["data"]["user_id"], "pw": pw, "token": login["data"]["access_token"]}


# ── cove_haven state ─────────────────────────────────────────────────────────

async def _haven_state(haven_id: str) -> dict:
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT haven_id, name, owner_user, space_id, commons_id, "
            "steward_username, steward_password FROM cove_haven WHERE haven_id = %s",
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


async def _set_steward_displayname(token: str, user_id: str, name: str) -> None:
    """Give the Haven steward a human-readable Matrix display name ("{name} Haven") so it
    stops showing its raw localpart (havensteward) in Commons member lists + "created the
    room" system events. Idempotent (PUT), best-effort — a failure here must never block
    Haven creation."""
    label = ("%s Haven" % name).strip()
    if not (token and user_id and label):
        return
    try:
        s, _ = await _http("PUT", "/_matrix/client/v3/profile/%s/displayname" % _up.quote(user_id),
                           token, {"displayname": label})
        if s != 200:
            log.info("haven steward displayname set returned %s", s)
    except Exception as e:
        log.info("haven steward displayname set skipped: %s", str(e)[:100])


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
    """Idempotently ensure the STEWARD-OWNED Haven Space + Commons exist and that the founding
    operator + `members` (federated @user ids) are invited. The durable Haven steward (an agent on
    this founding Cove) OWNS the rooms so they survive operator churn (§2); the human operator is
    recorded as the Haven owner and invited in. Mirrors ids to the registrar."""
    if not _configured():
        return {"ok": False, "reason": "matrix not configured"}
    if not await _has_state_table():
        return {"ok": False, "reason": "cove_haven table absent — Haven Spaces disabled here"}
    owner_mx, owner_handle = await _operator_matrix_id(request)
    steward = await ensure_haven_steward(haven_id)
    tok, steward_user = steward["token"], steward["user"]
    await _set_steward_displayname(tok, steward_user, name)
    members = [m for m in (members or []) if m and m != steward_user and m != owner_mx]
    invitees = ([owner_mx] if owner_mx else []) + members

    st = await _haven_state(haven_id)
    space_id, commons_id = st.get("space_id"), st.get("commons_id")
    if space_id and commons_id:
        await _invite(tok, [space_id, commons_id], invitees)
        await _sync_registry(haven_id, name, owner_handle, space_id, commons_id, members)
        return {"ok": True, "haven_id": haven_id, "space_id": space_id,
                "commons_id": commons_id, "created": False}

    # Haven Space (m.space) — owned by the steward, operator + members invited
    s, r = await _http("POST", "/_matrix/client/v3/createRoom", tok, {
        # jules 07-07: name the Space "{name} Haven" so its invite is clearly distinct from the
        # "{name} Commons" chat invite — an operator receives BOTH, and they were two blank prompts.
        "name": "%s Haven" % name, "topic": "A haven of connected Coves",
        "creation_content": {"type": "m.space"},
        "preset": "private_chat", "visibility": "private", "invite": invitees})
    if s != 200:
        raise HTTPException(502, "Create Haven Space failed: %s" % r)
    space_id = r["room_id"]

    # Commons room
    s, r = await _http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": "%s Commons" % name, "topic": "Commons for %s" % name,
        "preset": "private_chat", "visibility": "private", "invite": invitees})
    if s != 200:
        raise HTTPException(502, "Create Commons room failed: %s" % r)
    commons_id = r["room_id"]

    sn = _server_name()
    await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.child/%s"
                % (_up.quote(space_id), _up.quote(commons_id)), tok, {"via": [sn]})
    await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.parent/%s"
                % (_up.quote(commons_id), _up.quote(space_id)), tok, {"via": [sn], "canonical": True})

    await _save_haven(haven_id, name=name, owner_user=owner_mx or owner_handle,
                      space_id=space_id, commons_id=commons_id)
    await _sync_registry(haven_id, name, owner_handle, space_id, commons_id, members)
    log.info("Built steward-owned Haven Space %s (Commons %s) for %s", space_id, commons_id, name)
    return {"ok": True, "haven_id": haven_id, "space_id": space_id, "commons_id": commons_id, "created": True}


async def nest_member_cove(request: Request, haven_id: str, cove_key: str) -> dict:
    """Nest a member Cove's Space under the Haven Space (m.space.child → the Cove's
    space_id, via the Cove's homeserver). Resolves the Cove from the registrar."""
    st = await _haven_state(haven_id)
    if not st.get("space_id"):
        raise HTTPException(409, "Build the Haven Space first")
    await _operator_matrix_id(request)  # enforce operator sign-in
    steward = await ensure_haven_steward(haven_id)
    tok = steward["token"]
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
    # jules 07-07: auto-invite the member Cove's operator to the Haven Space + Commons — nesting
    # alone only writes the structure (m.space.child); without this the Commons never shows on their
    # side until a SEPARATE invite. Best-effort; needs the member's registered owner_handle.
    _mop = (cove.get("owner_handle") or "").strip().lstrip("@")
    if _mop and via and st.get("commons_id"):
        await _invite(tok, [st["space_id"], st["commons_id"]], ["@%s:%s" % (_mop, via)])
    # Record the membership in the registrar (best-effort).
    await registry_client.add_haven_member(
        haven_id, cove={"cove_id": cove.get("cove_id"), "space_id": child_space, "homeserver": via})
    _cn = cove.get("name") or cove.get("cove_id") or "The Cove"
    return {"ok": True, "nested": child_space, "via": via,
            "message": "Nested %s into the Haven." % _cn}


async def _sync_registry(haven_id, name, owner_handle, space_id, commons_id, members):
    owner_handle = (owner_handle or "").lstrip("@").split(":")[0].strip().lower()
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
    await _operator_matrix_id(request)  # enforce operator sign-in
    steward = await ensure_haven_steward(haven_id)
    tok = steward["token"]
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
