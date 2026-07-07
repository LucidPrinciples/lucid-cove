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
)

log = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_EXPIRY_DAYS = 7
_WIZARD = "/static/action-board/new-agent-setup.html"


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
            """SELECT id, cove_id, role, reserved_handle, invited_label, expires_at, consumed_at
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
    """The invitee opens their link → run the wizard in self mode. Role + Cove come
    from the invite (not the URL), so the invitee can't self-elevate."""
    inv = await _valid_invite(token)
    if not inv:
        return _closed_page("It may have already been used, been revoked, or expired. "
                            "Ask whoever sent it for a fresh link.")
    from urllib.parse import urlencode
    q = {"invite": token, "role": inv["role"], "self": "1", "embedded": "1"}
    if inv.get("reserved_handle"):
        q["handle"] = inv["reserved_handle"]
    resp = RedirectResponse("%s?%s" % (_WIZARD, urlencode(q)), status_code=302)
    # Onboarding capability cookie: the invitee has no session yet, so this authorizes ONLY
    # the wizard's onboarding endpoints (middleware checks it). httponly (server-read only),
    # 1-hour window, cleared at /complete; the invite is single-use so it self-closes anyway.
    resp.set_cookie("lp_invite", token, httponly=True, samesite="lax", max_age=3600, path="/")
    return resp


# ── Complete (public, gated by the token) ────────────────────────────────────

@router.post("/api/presence/invite/{token}/complete")
async def complete_invite(token: str, request: Request):
    """The wizard calls this on finish. Validates the invite, provisions the Presence
    (role from the INVITE, never the client), consumes the invite single-use, and mints
    the invitee's own sign-in door. Returns {ok, presence_id, door}."""
    inv = await _valid_invite(token)
    if not inv:
        raise HTTPException(410, "This invite is no longer valid.")

    body = await request.json()
    display_name = (body.get("display_name") or "").strip()
    agent_name = (body.get("agent_name") or body.get("name") or "").strip()
    if not display_name or not agent_name:
        raise HTTPException(400, "display_name and agent_name are required")

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
    }

    # Agent-led onboarding: keep the agent's birth voice (first_message from the wizard) and
    # add a proactive offer to show them around. This IS the member's orientation — in the
    # agent's own words, in Chat — instead of home-screen nag cards (the direction founder
    # onboarding moves too). Admin gets a manage-oriented offer, member a find-your-way one.
    _tour = (" Want me to walk you through where everything is?" if inv["role"] == "admin"
             else " Want me to show you around?")
    _fm = (agent_identity.get("first_message") or "").rstrip()
    agent_identity["first_message"] = (_fm + _tour) if _fm else _tour.strip()

    # Role comes from the INVITE, not the client payload — no self-elevation.
    result = await _create_presence_record(
        display_name=display_name,
        email=(body.get("email") or "").strip().lower(),
        agent_name=agent_name,
        cove_role=inv["role"],
        agent_config=body.get("agent_config", {}),
        agent_identity=agent_identity,
        handle=(body.get("handle") or inv.get("reserved_handle") or None),
        send_email=bool(body.get("send_email", False)),
        request=request,
    )
    presence_id = (result or {}).get("presence_id") or (result or {}).get("id")
    if not presence_id:
        raise HTTPException(500, "Could not create the Presence.")

    # Consume the invite (single-use) now that the Presence exists.
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE presence_invites SET consumed_at = NOW(), consumed_by = %s "
            "WHERE id = %s AND consumed_at IS NULL",
            (uuid.UUID(str(presence_id)), inv["id"]))

    dom = _cove_domain()
    door = await mint_signin_door(presence_id, dom) if dom else (result or {}).get("signin_link", "")
    if door:
        # Land them straight in Chat with their agent (the /p door honors an internal ?next=).
        from urllib.parse import urlencode
        door = door + ("&" if "?" in door else "?") + urlencode({"next": "/?tab=chat"})
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True, "presence_id": str(presence_id), "door": door, "role": inv["role"]})
    resp.delete_cookie("lp_invite", path="/")   # onboarding capability consumed
    return resp
