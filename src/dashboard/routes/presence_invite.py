# =============================================================================
# presence_invite.py — self-onboard a Presence (member-initiated)
# =============================================================================
# An ADMIN mints a single-use, role-baked invite link for their Cove. The invitee
# opens it on THEIR own device (mobile-friendly), runs the add-presence wizard AS
# THEMSELVES — the agent wakes on the COVE's model (guided key), NOT the hub spark —
# and lands signed into their own Mission Control. Most families are co-located, so
# the link is meant to be opened on the device the person will actually use.
#
# Security model:
#   - MINTING is admin-gated (only cove_role='admin' can create invites).
#   - COMPLETION is gated by the invite token itself (the invitee is anonymous until
#     they finish; the token is the capability). Single-use + expiring. The role is
#     baked into the invite by the admin — the invitee cannot self-elevate.
#
# Spec: LP-Vault/Projects/OSS-Flip-Reorg/self-onboard-presence-spec.md
# =============================================================================
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse

from src.dashboard.routes.presence import (
    _hash_token, get_current_presence, _create_presence_record, mint_signin_door,
    _create_session, _handle_validity, _seed_birth_memory, _sanitize_name,
    _titlecase_name, COOKIE_NAME, COOKIE_MAX_AGE,
)

log = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_EXPIRY_DAYS = 7
# The invitee is a FULL operator, so /join lands them on the OPERATOR-profile step first
# (same flow the founder ran, minus naming a Cove), then the agent wizard. new-cove-setup
# runs in "join" mode: operator -> bio -> agent wizard, skipping the model + cove-name panels.
_WIZARD_COVE = "/static/action-board/new-cove-setup.html"
_WIZARD_AGENT = "/static/action-board/new-agent-setup.html"


def _cove_id() -> str:
    try:
        from src.config import get_instance
        return (get_instance().get("id") or "").strip()
    except Exception:
        return ""


def _cove_domain() -> str:
    try:
        from src.config import load_cove_config
        return (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".")
    except Exception:
        return ""


async def _require_admin(request: Request) -> dict:
    p = await get_current_presence(request)
    if not p or p.get("cove_role") != "admin":
        raise HTTPException(403, "Only a Cove admin can invite a Presence.")
    return p


# ── Seed + sign-in the invitee (they run the wizard AS THEMSELVES) ────────────

async def _seed_or_reuse_operator(inv: dict) -> str | None:
    """Seed a placeholder operator for this invite (once) and return its account id.

    The invitee is a full operator, so we mirror the founder's seed-and-sign-in: a
    placeholder operator (a `needs_username` handle) so the wizard opens editable, signed
    in, attached to THIS Cove. Seeded ONCE per invite — the id is stored on the invite row
    and REUSED on re-open, so re-opening the link never spawns a duplicate seed.

    Role is seeded as 'member' regardless of the invite's role. The member-finalize
    (/complete) elevates to the invite's real role server-side, so /p never trips the
    founder first-run detection (which keys on cove_role='admin' + empty identity) and the
    invitee can never self-elevate from the client."""
    from src.memory.database import get_db
    seeded = inv.get("seeded_account_id")
    if seeded:
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT id FROM accounts WHERE id = %s AND active = TRUE", (seeded,))
            if await r.fetchone():
                return str(seeded)
    acct_id = uuid.uuid4()
    placeholder_handle = "member-%s" % secrets.token_hex(2)      # matches ^.+-[0-9a-f]{4}$
    throwaway = _hash_token(secrets.token_urlsafe(16))            # replaced by the sign-in mint
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO accounts (id, display_name, username, cove_role, tier, auth_token)
               VALUES (%s, %s, %s, 'member', 'presence', %s)""",
            (acct_id, "New member", placeholder_handle, throwaway))
        await conn.execute(
            "UPDATE presence_invites SET seeded_account_id = %s WHERE id = %s",
            (acct_id, inv["id"]))
    return str(acct_id)


def _cookie_domain_for(request: Request) -> str | None:
    """Share the session across the Cove's subdomains (same rule the /p handler uses)."""
    try:
        from src.config import load_cove_config
        cd = (load_cove_config().get("domain") or "").strip().lower()
        host = (request.headers.get("x-forwarded-host")
                or request.headers.get("host") or "").split(":")[0].lower()
        if cd and (host == cd or host.endswith("." + cd)):
            return cd
    except Exception:
        pass
    return None


async def _signin_seed(request: Request, acct_id: str) -> str:
    """Mint a fresh session for the seeded account and return the raw cookie token.
    Same machinery as mint_signin_door, but we set the cookie directly on the redirect
    (no domain dependency) so the invitee is signed in the moment the wizard loads."""
    raw = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
            (hashed, uuid.UUID(str(acct_id))))
        await _create_session(conn, uuid.UUID(str(acct_id)), hashed, "invite")
    return raw


# ── Mint / list / revoke (admin) ─────────────────────────────────────────────

@router.post("/api/presence/invite")
async def create_invite(request: Request):
    """Admin mints a self-onboard invite. Body: {role, handle?, label?, expires_days?}.
    Returns {ok, token, join_url, role, expires_at}."""
    admin = await _require_admin(request)
    body = await request.json()
    role = (body.get("role") or "member").strip().lower()
    if role not in ("admin", "member"):
        raise HTTPException(400, "role must be admin or member")
    handle = (body.get("handle") or "").strip().lstrip("@").lower() or None
    label = (body.get("label") or "").strip() or None
    try:
        days = int(body.get("expires_days") or _DEFAULT_EXPIRY_DAYS)
    except (TypeError, ValueError):
        days = _DEFAULT_EXPIRY_DAYS
    expires = datetime.now(timezone.utc) + timedelta(days=max(1, min(days, 30)))

    raw = secrets.token_urlsafe(24)
    token_hash = _hash_token(raw)
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO presence_invites
               (token_hash, cove_id, role, reserved_handle, invited_label, inviter_id, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (token_hash, _cove_id(), role, handle, label,
             uuid.UUID(str(admin["id"])) if admin.get("id") else None, expires))

    dom = _cove_domain()
    base = ("https://%s" % dom) if dom else str(request.base_url).rstrip("/")
    return {"ok": True, "token": raw, "join_url": "%s/join/%s" % (base, raw),
            "role": role, "expires_at": expires.isoformat()}


@router.get("/api/presence/invites")
async def list_invites(request: Request):
    """Admin: open (unconsumed, unexpired) invites for this Cove."""
    await _require_admin(request)
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            """SELECT id, role, reserved_handle, invited_label, created_at, expires_at
               FROM presence_invites
               WHERE cove_id = %s AND consumed_at IS NULL
                 AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY created_at DESC""", (_cove_id(),))
        rows = await r.fetchall()
    return {"invites": [dict(x) for x in rows]}


@router.post("/api/presence/invite/{invite_id}/revoke")
async def revoke_invite(invite_id: str, request: Request):
    """Admin: void an unused invite."""
    await _require_admin(request)
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE presence_invites SET consumed_at = NOW() "
            "WHERE id = %s AND cove_id = %s AND consumed_at IS NULL",
            (uuid.UUID(invite_id), _cove_id()))
    return {"ok": True}


# ── Open the link (public) ───────────────────────────────────────────────────

async def _valid_invite(raw_token: str) -> dict | None:
    """Return the invite row if the raw token is valid + open, else None."""
    if not raw_token:
        return None
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            """SELECT id, cove_id, role, reserved_handle, invited_label, expires_at,
                      consumed_at, seeded_account_id
               FROM presence_invites WHERE token_hash = %s""", (_hash_token(raw_token),))
        row = await r.fetchone()
    if not row:
        return None
    if row["consumed_at"] is not None:
        return None
    exp = row["expires_at"]
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
    return dict(row)


def _closed_page(msg: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<body style='font:16px/1.6 -apple-system,system-ui,sans-serif;background:#0d1117;color:#e6edf3;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px;margin:0'>"
        "<div><div style='font-size:22px;font-weight:700;margin-bottom:8px'>This invite can’t be opened</div>"
        "<div style='color:#8b949e'>%s</div></div></body>" % msg, status_code=410)


@router.get("/join/{token}")
async def open_invite(token: str, request: Request):
    """The invitee opens their link → seed + sign them in as a full operator, then land
    them on the OPERATOR-profile step (new-cove-setup in join mode). Role + Cove come from
    the invite (not the URL), so the invitee can't self-elevate."""
    inv = await _valid_invite(token)
    if not inv:
        return _closed_page("It may have already been used, been revoked, or expired. "
                            "Ask whoever sent it for a fresh link.")
    from urllib.parse import urlencode
    # The operator-profile step, then the agent wizard — self + join mode, invite threaded.
    q = {"invite": token, "role": inv["role"], "self": "1", "join": "1"}
    if inv.get("reserved_handle"):
        q["handle"] = inv["reserved_handle"]
    wizard = "%s?%s" % (_WIZARD_COVE, urlencode(q))

    # Seed the invitee as a full operator and sign them in (mirror the founder). After this
    # they hold a real session, so claim-operator / profile / avatar / the agent wizard all
    # work via the session — no per-endpoint invite-cookie allowlisting needed.
    acct_id = await _seed_or_reuse_operator(inv)
    if not acct_id:
        return _closed_page("Something went wrong starting your setup. Ask for a fresh link.")
    raw = await _signin_seed(request, acct_id)

    resp = RedirectResponse(wizard, status_code=302)
    _xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    resp.set_cookie(
        key=COOKIE_NAME, value=raw, max_age=COOKIE_MAX_AGE, httponly=True,
        samesite="lax", secure=(request.url.scheme == "https") or (_xfp == "https"),
        domain=_cookie_domain_for(request),
    )
    # Keep the capability cookie too (belt-and-suspenders for the middleware allowlist and to
    # carry the invite token through the wizard). Cleared at /complete; single-use anyway.
    resp.set_cookie("lp_invite", token, httponly=True, samesite="lax", max_age=3600, path="/")
    return resp


# ── Complete (public, gated by the token) ────────────────────────────────────

@router.post("/api/presence/invite/{token}/complete")
async def complete_invite(token: str, request: Request):
    """The agent wizard calls this on finish. The invitee is already signed in as their
    seeded operator (from /join), so this is a member-FINALIZE — it UPDATEs the signed-in
    account (like the founder's /api/presence/me/finalize, minus naming a Cove), stamps the
    role FROM THE INVITE (never the client → no self-elevation), consumes the invite
    single-use, and lands them in Chat with their agent. Returns {ok, presence_id, door}."""
    import json
    from src.config import load_cove_config
    from src.memory.database import get_db

    inv = await _valid_invite(token)
    if not inv:
        raise HTTPException(410, "This invite is no longer valid.")

    # The signed-in seed. Bind it to THIS invite so a token can't be finalized under an
    # unrelated session.
    presence = await get_current_presence(request)
    if not presence or not presence.get("id"):
        raise HTTPException(401, "Not authenticated")
    pid = str(presence["id"])
    seeded = inv.get("seeded_account_id")
    if seeded and str(seeded) != pid:
        raise HTTPException(403, "This invite is bound to a different sign-in.")

    body = await request.json()
    display_name = _sanitize_name(body.get("display_name") or body.get("person") or "")
    agent_name = _titlecase_name(_sanitize_name(body.get("agent_name") or body.get("name") or ""))
    if not agent_name:
        raise HTTPException(400, "agent_name is required — your personal agent forms your Presence")

    archetype = body.get("archetype", "The Guide")
    agent_identity = {
        "agent_name": agent_name,
        "archetype": archetype,
        "archetype_desc": body.get("archetype_desc", "") or body.get("role", ""),
        "frequency": body.get("frequency", "Peace"),
        "frequency_color": body.get("frequency_color", "") or "#5ce1e6",
        "frequency_essence": body.get("frequency_essence", ""),
        "tuning_key": body.get("tuning_key", ""),
        "tuning_key_song": body.get("tuning_key_song", ""),
        "pronouns": body.get("pronouns", "it/its"),
        "gender": body.get("gender", "neutral"),
        "qualities": body.get("qualities", []),
        "feeling": body.get("feeling", ""),
        "persona": body.get("persona", ""),
        "first_message": body.get("first_message", ""),
        "nickname": (body.get("nickname") or "").strip(),
        "perspective": (body.get("perspective") or "").strip(),
        "role": body.get("role", "") or ("Personal agent — %s" % archetype),
        "channels": ["day", "deep"],
        "avatar": (body.get("avatar") or "").strip(),
        "input_mode": (body.get("input_mode") or "guided").strip(),
        "provisioned_at": datetime.now(timezone.utc).isoformat(),
    }

    # Agent-led onboarding: keep the agent's birth voice (first_message) + a proactive offer
    # to show them around. The agent IS the orientation, in Chat, in its own words.
    _tour = (" Want me to walk you through where everything is?" if inv["role"] == "admin"
             else " Want me to show you around?")
    _fm = (agent_identity.get("first_message") or "").rstrip()
    agent_identity["first_message"] = (_fm + _tour) if _fm else _tour.strip()

    # last_name = the live Cove name (the agent's full name is "{agent} {Cove}").
    _cove = load_cove_config()
    _cove_name = (_cove.get("name") or "").strip()
    if _cove_name.lower() == "new cove":
        _cove_name = ""

    # Role + tier come from the INVITE. Elevating the seed's placeholder 'member' to the
    # invite's real role here (server-side) is the ONLY place the role is set.
    role = inv["role"] if inv["role"] in ("admin", "member") else "member"
    tier = "cove" if role == "admin" else "presence"

    # The handle was claimed in the operator step (written onto the seed row); accept an
    # explicit one too (reserved handle) and validate if it changed.
    new_handle = (body.get("handle") or inv.get("reserved_handle") or "").strip().lstrip("@").lower()

    agent_config = presence.get("agent_config") or {}
    if isinstance(agent_config, str):
        try:
            agent_config = json.loads(agent_config) or {}
        except Exception:
            agent_config = {}
    agent_config = dict(agent_config)
    if "team_active" in body:
        agent_config["team_active"] = bool(body.get("team_active"))
    if body.get("model_provider"):
        agent_config["model_provider"] = str(body.get("model_provider")).strip()

    try:
        async with get_db() as conn:
            set_parts = ["agent_name = %s", "agent_identity = %s", "agent_config = %s",
                         "last_name = %s", "cove_role = %s", "tier = %s"]
            params = [agent_name, json.dumps(agent_identity), json.dumps(agent_config),
                      _cove_name, role, tier]
            if display_name:
                set_parts.append("display_name = %s"); params.append(display_name)
            if new_handle:
                h, herr = _handle_validity(new_handle, _cove)
                if herr:
                    raise HTTPException(400, herr)
                r = await conn.execute(
                    "SELECT 1 FROM accounts WHERE username = %s AND id != %s", (h, pid))
                if await r.fetchone():
                    raise HTTPException(409, "That handle is already taken.")
                set_parts.insert(0, "username = %s"); params.insert(0, h)
            params.append(pid)
            await conn.execute(
                f"UPDATE accounts SET {', '.join(set_parts)}, updated_at = NOW() WHERE id = %s",
                tuple(params))
    except HTTPException:
        raise
    except Exception as e:
        log.error("Member finalize failed for %s: %s", pid, e)
        raise HTTPException(500, "Something went wrong finishing setup. Please try again.")

    # The agent's first memory: being born from this person (best-effort, non-fatal).
    _refl = (body.get("reflection") or agent_identity.get("feeling") or "").strip()
    try:
        await _seed_birth_memory(pid, display_name or presence.get("display_name") or "",
                                 agent_identity, _refl, _cove_name)
    except Exception as e:
        log.warning("Birth-memory seed failed (non-fatal): %s", e)

    # Provision the member's Nextcloud user/folders (the seed skips NC).
    if not presence.get("nc_username"):
        try:
            from src.dashboard.routes.nextcloud import provision_nc_user
            await provision_nc_user(
                pid, display_name or presence.get("display_name") or agent_name,
                handle=(new_handle or presence.get("username") or ""),
                tier=tier, role=("steward" if role == "admin" else "member"))
        except Exception as e:
            log.error("NC provisioning during member finalize failed for %s: %s", pid, e)

    # Invite the new Presence into the Cove's Connect Space + Family room (non-fatal).
    try:
        from src.dashboard.routes.matrix_spaces import invite_presence_to_cove_space
        await invite_presence_to_cove_space(new_handle or presence.get("username") or "")
    except Exception as e:
        log.warning("Matrix Space invite (non-fatal): %s", e)

    # Consume the invite (single-use).
    async with get_db() as conn:
        await conn.execute(
            "UPDATE presence_invites SET consumed_at = NOW(), consumed_by = %s "
            "WHERE id = %s AND consumed_at IS NULL",
            (uuid.UUID(pid), inv["id"]))

    # Land them straight in Chat with their agent. mint_signin_door rotates a fresh
    # session-backed token on the Cove ROOT (their {handle}.{cove} subdomain is mesh-only,
    # so root is the reachable address for a remote invitee). The /p door honors an internal
    # ?next=. We thread `?as={handle}` so an ADMIN invitee gets their PERSONAL home+chat, not
    # the Cove-admin apex surface (core.py forces personal view on ?as=<own handle>); it's a
    # harmless no-op for a member (who never gets the admin apex anyway).
    from urllib.parse import urlencode
    _final_handle = (new_handle or presence.get("username") or "").lstrip("@")
    _next = ("/?as=%s&tab=chat" % _final_handle) if _final_handle else "/?tab=chat"
    dom = _cove_domain()
    door = await mint_signin_door(pid, dom) if dom else _next
    if door and dom:
        door = door + ("&" if "?" in door else "?") + urlencode({"next": _next})
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True, "presence_id": pid, "door": door, "role": role})
    resp.delete_cookie("lp_invite", path="/")   # onboarding capability consumed
    return resp
