# =============================================================================
# matrix_spaces.py — steward-owned Cove Matrix Space (#137 Phase A)
# =============================================================================
# Each Cove's Connect tree is a Matrix Space + a Family room, OWNED by a steward
# Matrix identity (NOT any operator — operators come and go; the steward is the
# stable owner). This module:
#   - provisions the steward Matrix account once (admin, shared-secret),
#   - creates the Space + Family room once (idempotent — ids persisted in the
#     cove_matrix singleton row and reused),
#   - invites presences (all of them when first building; just the new one after).
#
# Replaces the old operator-owned, manually-run matrix-cove-setup.py. Called
# lazily from /api/matrix/token (first Connect open) and on presence creation.
# All server-side calls go to the INTERNAL homeserver url (MATRIX_HUB_URL); user
# ids are built from MATRIX_SERVER_NAME.
import logging
import os
from src.env import env
import urllib.parse as _up

import httpx
from fastapi import APIRouter, Request, HTTPException

from src.dashboard.routes.matrix import register_matrix_account, _try_login

log = logging.getLogger(__name__)
router = APIRouter()

STEWARD_LOCALPART = env("MATRIX_STEWARD_LOCALPART", "steward")


def _internal() -> str:
    return (env("MATRIX_HUB_URL") or "").rstrip("/")


def _server_name() -> str:
    # After a domain claim the Matrix identity is regenerated to matrix.{domain}
    # (Dendrite's real server_name), but the provision-stamped MATRIX_SERVER_NAME env
    # goes STALE (still matrix.{cove-id}.localhost). Prefer the live cove domain so the
    # steward-built Space/Family rooms AND the presence invites all carry ONE server_name
    # that matches Dendrite. Otherwise _uid() builds invites against the dead .localhost
    # name, Dendrite treats them as federation to an unresolvable host, and Space creation
    # 502s with M_FORBIDDEN — the "no family room in Connect" bug. Mirrors the host-aware
    # rule in matrix.py _client_homeserver + domain.py _matrix_server_name (matrix.{domain}).
    try:
        from src.config import load_cove_config
        dom = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".").lower()
        if dom:
            return "matrix.%s" % dom
    except Exception:
        pass
    return env("MATRIX_SERVER_NAME") or ""


def _uid(localpart: str) -> str:
    return "@%s:%s" % (localpart, _server_name())


def _configured() -> bool:
    return bool(_internal() and _server_name())


async def _has_state_table() -> bool:
    """True only if cove_matrix exists. Gates the steward-Space behavior so it stays
    DORMANT on existing Coves (Cove Cove / Clearfield) that predate this table and
    already run Connect with their own spaces — they get the code on a shared
    cove-core pull but not the behavior until deliberately migrated."""
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            r = await conn.execute("SELECT to_regclass('public.cove_matrix') AS t")
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


# ── Persistent state (cove_matrix singleton row) ─────────────────────────────

async def _state() -> dict:
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT steward_username, steward_password, space_id, family_room_id "
            "FROM cove_matrix WHERE id = 1")
        row = await r.fetchone()
    return dict(row) if row else {}


async def _save_state(**kw):
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute("INSERT INTO cove_matrix (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        if kw:
            # Column names are internal constants, never user input.
            sets = ", ".join("%s = %%s" % k for k in kw)
            await conn.execute(
                "UPDATE cove_matrix SET %s, updated_at = NOW() WHERE id = 1" % sets,
                tuple(kw.values()))


# ── Steward identity ─────────────────────────────────────────────────────────

async def ensure_steward() -> dict:
    """Return {user, pw, token} for the Cove's steward Matrix identity, provisioning
    it once (admin account, shared-secret). The steward OWNS the Cove Space.

    SELF-HEALING (batch-10 #5, B9 bug 9b — extends the batch-9 #2 operator pattern to
    the steward): stored creds that fail login with M_FORBIDDEN mean the account is gone
    under the app's stale creds (the run-3 register-200-ghost). Heal ONCE — re-register a
    fresh password, persist DURABLY, retry. If it STILL fails after the heal, the localpart
    registers (200) but never lands / login stays forbidden — a PARTIAL userapi_* delete
    left a ghost that register can't reset. Looping is futile, so we surface ONE actionable
    line (the host command that clears the account from ALL userapi_* tables) instead of the
    non-fatal forever-loop that burned run-3."""
    st = await _state()
    user, pw = st.get("steward_username"), st.get("steward_password")
    hs = _internal()
    if not (user and pw):
        user, pw = await register_matrix_account(STEWARD_LOCALPART, admin=True)
        await _save_state(steward_username=user, steward_password=pw)

    login = await _try_login(hs, user, pw)
    if not login.get("ok"):
        ec = (login.get("errcode") or "").upper()
        if login.get("unreachable"):
            raise HTTPException(502, "Matrix homeserver unreachable: %s" % login.get("body"))
        if ec == "M_LIMIT_EXCEEDED":
            # Rate-limited — re-registering only compounds the storm (run-3). Back off.
            raise HTTPException(429, "Matrix is warming up (rate limited) — retry shortly.")
        if ec in ("M_FORBIDDEN", "M_USER_DEACTIVATED", "M_UNKNOWN"):
            try:
                user, pw = await register_matrix_account(STEWARD_LOCALPART, admin=True)
                await _save_state(steward_username=user, steward_password=pw)
                login = await _try_login(hs, user, pw)
            except HTTPException as re:
                # Re-register itself failed (localpart taken and not resettable via
                # shared-secret) — fall through to the actionable line below.
                log.warning("steward self-heal re-register failed: %s", getattr(re, "detail", re))
        if not login.get("ok"):
            raise HTTPException(
                503,
                "Steward Matrix account is stuck (registered but login forbidden — a "
                "partial delete left a ghost). On the Cove host run: "
                "python3 /cove-core/provision/set_domain.py --remove-matrix-user %s "
                "--cove-id <cove_id>, then reopen Connect." % STEWARD_LOCALPART,
            )
    return {"user": user, "pw": pw, "token": login["data"]["access_token"]}


async def _live_cove_name() -> str:
    """The Cove's LIVE name — same source the header uses (core.py): prefer the
    founding operator's account.last_name (written by the wizard finalize), then
    cove.yaml, then env. The generator seeds cove.yaml with a placeholder (e.g.
    'Test') that lingers until/unless the cove.yaml write persists, so last_name is
    the reliable truth (matches the 'Alex' lesson: don't surface the placeholder)."""
    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT last_name FROM accounts WHERE active = TRUE "
                "AND cove_role = 'admin' AND last_name IS NOT NULL AND last_name <> '' "
                "ORDER BY created_at LIMIT 1")
            row = await r.fetchone()
        if row and (row.get("last_name") or "").strip():
            return row["last_name"].strip()
    except Exception:
        pass
    from src.config import load_cove_config
    return (load_cove_config().get("name") or env("COVE_NAME") or "Cove").strip()


async def _presence_handles() -> list:
    """@handles of every active presence in this Cove (operators + members)."""
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT username FROM accounts "
            "WHERE active = TRUE AND username IS NOT NULL AND username <> '' "
            "AND tier IN ('cove', 'presence')")
        rows = await r.fetchall()
    return [row["username"] for row in rows if (row or {}).get("username")]


async def _invite(token: str, rooms: list, user_ids: list):
    for room in rooms:
        if not room:
            continue
        rq = _up.quote(room)
        for uid in user_ids:
            s, r = await _http("POST", "/_matrix/client/v3/rooms/%s/invite" % rq, token,
                               {"user_id": uid})
            # Already-joined / already-invited come back M_FORBIDDEN — that's fine.
            if s != 200 and r.get("errcode") != "M_FORBIDDEN":
                log.warning("matrix invite %s -> %s: %s %s", uid, room, s, r.get("error"))


# ── The Cove Space ───────────────────────────────────────────────────────────

async def ensure_cove_space() -> dict:
    """Idempotently ensure the steward-owned Cove Space + Family room exist and that
    all current presences are invited. Safe to call repeatedly."""
    if not _configured():
        return {"ok": False, "reason": "matrix not configured"}
    if not await _has_state_table():
        return {"ok": False, "reason": "cove_matrix table absent — steward Space disabled here"}

    steward = await ensure_steward()
    tok = steward["token"]
    cove_name = await _live_cove_name()
    invites = [_uid(h) for h in await _presence_handles()]

    st = await _state()
    space_id, room_id = st.get("space_id"), st.get("family_room_id")
    if space_id and room_id:
        await _invite(tok, [space_id, room_id], invites)
        await _sync_space_to_registry(cove_name, space_id)  # self-healing
        return {"ok": True, "space_id": space_id, "room_id": room_id, "created": False}

    # Create the Space (m.space) — steward-owned.
    s, r = await _http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": cove_name, "topic": "%s — your Cove" % cove_name,
        "creation_content": {"type": "m.space"},
        "preset": "private_chat", "visibility": "private", "invite": invites})
    if s != 200:
        raise HTTPException(502, "Create Cove Space failed: %s" % r)
    space_id = r["room_id"]

    # Create the Family room inside it.
    s, r = await _http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": "%s — Family" % cove_name, "topic": "Family room for %s" % cove_name,
        "preset": "private_chat", "visibility": "private", "invite": invites})
    if s != 200:
        raise HTTPException(502, "Create Family room failed: %s" % r)
    room_id = r["room_id"]

    # Link room <-> space (Matrix Spaces hierarchy).
    sn = _server_name()
    await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.child/%s"
                % (_up.quote(space_id), _up.quote(room_id)), tok, {"via": [sn]})
    await _http("PUT", "/_matrix/client/v3/rooms/%s/state/m.space.parent/%s"
                % (_up.quote(room_id), _up.quote(space_id)), tok,
                {"via": [sn], "canonical": True})

    await _save_state(space_id=space_id, family_room_id=room_id)
    log.info("Built Cove Space %s (Family room %s) for %s", space_id, room_id, cove_name)
    await _sync_space_to_registry(cove_name, space_id)
    return {"ok": True, "space_id": space_id, "room_id": room_id, "created": True}


async def _sync_space_to_registry(cove_name: str, space_id: str):
    """Register this Cove's Space id with the Hub registrar (#133) so a Haven can nest
    it (m.space.child). Best-effort + idempotent — runs on every ensure_cove_space so
    a Cove that came up before its registry config was set still self-heals. Never let
    a registry hiccup affect Connect."""
    try:
        from src.dashboard.routes import registry_client
        if registry_client.configured() and space_id:
            await registry_client.register_cove(
                cove_id=env("COVE_ID") or cove_name.lower().replace(" ", "-"),
                name=cove_name, homeserver=_server_name(), space_id=space_id)
    except Exception as e:
        log.info("Cove Space registry sync skipped: %s", e)


async def invite_presence_to_cove_space(handle: str) -> dict:
    """Invite one presence (@handle) to the Cove Space + Family room. If the Space
    doesn't exist yet, build it (which invites everyone, including this handle)."""
    if not (_configured() and handle):
        return {"ok": False}
    if not await _has_state_table():
        return {"ok": False, "reason": "cove_matrix table absent — steward Space disabled here"}
    st = await _state()
    if not st.get("space_id"):
        return await ensure_cove_space()
    steward = await ensure_steward()
    await _invite(steward["token"], [st["space_id"], st["family_room_id"]], [_uid(handle)])
    return {"ok": True}


@router.post("/api/admin/matrix/ensure-space")
async def api_ensure_cove_space(request: Request):
    """Manual trigger / backfill for the Cove Space (operator-gated by middleware)."""
    return await ensure_cove_space()
