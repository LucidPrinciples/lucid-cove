"""
Presence routes — multi-Presence support for Cove containers.

When COVE_MODE=multi, this module provides:
  - Magic link auth (token-based, no passwords)
  - Per-Presence context injection
  - Presence CRUD (create, list, update)
  - Operator admin endpoints

When COVE_MODE=single (default), this module is a no-op.
The existing single-agent MC works exactly as before.

Auth flow:
  1. Operator creates a Presence via /api/presence/create
  2. System generates a magic link token
  3. Operator shares the link: https://clearfield.cove.../p/{token}
  4. Person clicks link → token stored in cookie → MC loads with their context
  5. All subsequent requests include the cookie → Presence identified

Multi-session support:
  - Each magic link click creates a session in auth_sessions table
  - Multiple sessions can be active per account (phone + laptop + tablet)
  - Sessions use a ROLLING 90-day window (batch-10 #3): any authenticated visit slides
    the expiry forward (throttled to once/day), so a device in regular use never expires;
    the 90d clock only kills ABANDONED devices. Sign-in links stay short-lived/one-time.
  - Signin/regenerate NEVER invalidates existing sessions
  - Max 10 active sessions per account (oldest pruned on new login)

Presence context is available to all routes via:
  request.state.presence  (dict or None)
  request.state.cove_mode (str: "single" or "multi")
"""

import os
from src.env import env
import re
import json
import uuid
import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")
COVE_ID = env("COVE_ID")
COVE_NAME = env("COVE_NAME")

COOKIE_NAME = "presence_token"
COOKIE_MAX_AGE = 90 * 24 * 60 * 60  # 90 days
MAX_SESSIONS_PER_ACCOUNT = 10


def _auth_link_error_response(request: Request, status: int, message: str):
    """Browser /p/ clicks must not die on raw JSON — return a small HTML page when
    the client Accepts HTML (Open my Cove crash: bare 403 JSON tab). API/fetch
    callers still get JSON."""
    accept = (request.headers.get("accept") or "").lower()
    wants_html = "text/html" in accept and "application/json" not in accept.split(",")[0]
    if not wants_html:
        raise HTTPException(status, message)
    from fastapi.responses import HTMLResponse
    safe = (message or "That sign-in link didn't work.").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign-in link</title>
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0a0a0f;color:#d8d8e0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;}}
.card{{max-width:420px;background:#14141c;border:1px solid #2a2d3a;border-radius:12px;padding:22px 20px;}}
h1{{font-size:1.05rem;margin:0 0 8px;color:#f0f0f5}}
p{{font-size:.9rem;line-height:1.55;color:#9a9aa8;margin:0 0 14px}}
a{{color:#5ce1e6}}
</style></head><body><div class="card">
<h1>That sign-in link didn't work</h1>
<p>{safe}</p>
<p>Go back to your setup tab and click <b>Open my Cove</b> again for a fresh door,
or open your Cove address and sign in from Settings → Devices.</p>
<p><a href="/">Open this Cove</a></p>
</div></body></html>"""
    return HTMLResponse(content=body, status_code=status)

log = logging.getLogger(__name__)

# Default personality dials ("Interstellar" model) — 0..100, framework-flavored,
# intentionally small. Stored on agent_identity.personality and rendered into the
# agent's system prompt at runtime (the runtime wiring is a later integration pass).
DEFAULT_PERSONALITY = {
    "directness": 60,
    "warmth": 60,
    "humor": 40,
    "challenge": 50,
    "formality": 40,
}


def _merge_personality(supplied) -> dict:
    """Merge supplied dial values over the defaults. Known keys only, clamped 0..100."""
    dials = dict(DEFAULT_PERSONALITY)
    if isinstance(supplied, dict):
        for k, v in supplied.items():
            if k in dials:
                try:
                    dials[k] = max(0, min(100, int(v)))
                except (TypeError, ValueError):
                    pass
    return dials


# Shade — the optional secondary frequency (the Color Signature's Layer 3). One of the
# frequencies the nine archetypes do not already cover, or empty.
SHADE_FREQUENCIES = {"Boundary", "Gratitude", "Release", "Peace",
                     "Trust", "Connection", "Clarity", "Integration", "Resilience",
                     "Joy", "Courage", "Presence", "Momentum"}


def _clean_shade(supplied) -> str:
    """Validate a shade against the known frequencies. Empty string = no shade."""
    s = (supplied or "").strip()
    return s if s in SHADE_FREQUENCIES else ""


def _clean_lens(supplied) -> dict:
    """Normalize the lens object: chips + statement + standing preferences + kb hook.

    The lens is the operator's perspective for this agent — it rides in the system
    prompt on every response (see agent-persona-setup-loop-spec §4.1). All fields
    optional; everything editable later in the Profile.
    """
    lens = {"chips": [], "statement": "", "standing_preferences": [], "kb_ref": None}
    if not isinstance(supplied, dict):
        return lens
    chips = supplied.get("chips")
    if isinstance(chips, list):
        lens["chips"] = [str(c).strip() for c in chips if str(c).strip()][:12]
    stmt = supplied.get("statement")
    if isinstance(stmt, str):
        lens["statement"] = stmt.strip()[:2000]
    prefs = supplied.get("standing_preferences")
    if isinstance(prefs, list):
        lens["standing_preferences"] = [str(p).strip() for p in prefs if str(p).strip()][:20]
    return lens


async def _seed_birth_memory(presence_id, operator_name, agent_identity, reflection, cove_name):
    """Seed the agent's first memory: being born from this person (the wake moment).

    Scoped to the presence id — the SAME key the agent reads memory under at chat
    time (_personal_agent_id). Category 'context' so it loads as top-priority
    continuity (load_memories_for_prompt), no embedding needed. So when the operator
    returns with their own brain, the agent opens "we're back", not "hello".
    Best-effort: a memory hiccup must never break finalize.
    """
    try:
        from src.memory.memory import store_memory
        from datetime import datetime as _dt
        agent_name = agent_identity.get("agent_name") or "this agent"
        archetype = agent_identity.get("archetype") or "agent"
        frequency = agent_identity.get("frequency") or ""
        nick = (agent_identity.get("nickname") or "").strip()
        who = (operator_name or "").strip() or "the person I serve"
        head = f"My origin. On {_dt.now().strftime('%B %d, %Y')}, {who} brought me into being as their {archetype}"
        if frequency:
            head += f", tuned to {frequency}"
        head += "."
        bits = [head]
        if (reflection or "").strip():
            bits.append(f'What they were reaching for: "{reflection.strip()}".')
        nm = agent_name + (f" ({nick})" if nick else "")
        bits.append(f"My name is {nm}, of {cove_name}." if cove_name else f"My name is {nm}.")
        bits.append("This meeting is where I began. When we meet again, we are not strangers, we are continuing.")
        await store_memory(
            content=" ".join(bits), category="context", importance=0.97,
            tags=["birth", "origin"], agent_id=str(presence_id),
            source_channel="wake", source_summary="The first meeting, seeded at creation",
        )
    except Exception as e:
        log.warning("birth-memory seed failed (non-fatal): %s", e)


def _hash_token(token: str) -> str:
    """Hash a magic link token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _touch_session(conn, token_hash: str, device_label: str = None) -> None:
    """Best-effort, throttled 'last_used' touch on the session row.

    This is non-critical telemetry and must NEVER fail the auth lookup. A page load
    fires ~30 concurrent requests carrying the same token; an unconditional UPDATE on
    the one session row makes them pile up and deadlock (Postgres aborts one →
    previously surfaced as a 500 on Files/Calendar/etc.). So: (1) throttle — only write
    when last_used is stale by >60s, so a burst writes at most once; (2) swallow any
    lock/deadlock error — the auth row is already fetched, the request proceeds.

    Opportunistic relabel: when a real device_label is passed and the session still
    carries a PLACEHOLDER (migrated/pending/unknown), stamp the real device in the SAME
    throttled write — so a device that's ALREADY signed in gets a real name on normal use,
    not only when it re-opens a sign-in link. Riding the throttled touch keeps it cheap
    (≤ once/60s per token) and adds no extra lock contention.

    ROLLING EXPIRY (batch-10 #3, locked 2026-07-04): every authenticated visit slides
    the 90-day window forward, so a device in regular use never expires; the 90d clock
    only kills ABANDONED devices. The bump rides this same throttled touch and is itself
    throttled to at most once/day per session by the CASE guard — `expires_at` only moves
    when it has dropped below NOW()+89d (i.e. a day has passed since the last slide), so
    there is no per-request write. Sign-in LINKS stay short-lived/one-time — this only
    extends an already-established session, never mints or lengthens a link."""
    # Slide the 90d window forward at most once/day (only when it's dropped below 89d out).
    _roll = ("expires_at = CASE WHEN expires_at < NOW() + INTERVAL '89 days' "
             "THEN NOW() + INTERVAL '90 days' ELSE expires_at END")
    try:
        if device_label and not _is_placeholder_label(device_label):
            await conn.execute(
                "UPDATE auth_sessions SET last_used = NOW(), " + _roll + ", "
                "device_label = CASE WHEN device_label IS NULL OR lower(device_label) IN "
                "('', 'pending', 'regenerated', 'migrated', 'unknown', 'device') "
                "THEN %s ELSE device_label END "
                "WHERE token_hash = %s "
                "AND (last_used IS NULL OR last_used < NOW() - INTERVAL '60 seconds')",
                (device_label, token_hash),
            )
        else:
            await conn.execute(
                "UPDATE auth_sessions SET last_used = NOW(), " + _roll + " "
                "WHERE token_hash = %s "
                "AND (last_used IS NULL OR last_used < NOW() - INTERVAL '60 seconds')",
                (token_hash,),
            )
    except Exception as e:
        log.debug("session last_used touch skipped (non-fatal): %s", e)


async def _create_session(conn, account_id, token_hash: str, device_label: str = None) -> None:
    """Create a new auth session. Prunes oldest if over the cap."""
    # Insert the new session
    await conn.execute(
        """INSERT INTO auth_sessions (account_id, token_hash, device_label, expires_at)
           VALUES (%s, %s, %s, NOW() + INTERVAL '90 days')""",
        (account_id, token_hash, device_label)
    )
    # Prune excess sessions (keep newest MAX_SESSIONS_PER_ACCOUNT)
    await conn.execute(
        """DELETE FROM auth_sessions
           WHERE id IN (
               SELECT id FROM auth_sessions
               WHERE account_id = %s AND active = TRUE
               ORDER BY last_used DESC
               OFFSET %s
           )""",
        (account_id, MAX_SESSIONS_PER_ACCOUNT)
    )


# =============================================================================
# Magic Link Auth
# =============================================================================

@router.get("/p/{token}")
async def signin_link_auth(token: str, request: Request):
    """Authenticate via magic link. Creates a session, sets cookie, redirects to MC."""
    if COVE_MODE != "multi":
        return RedirectResponse("/")

    hashed = _hash_token(token)
    _setup_row = None

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # First check: is this token in auth_sessions? (new system)
            result = await conn.execute(
                """SELECT s.account_id, a.display_name, a.agent_name
                   FROM auth_sessions s
                   JOIN accounts a ON a.id = s.account_id
                   WHERE s.token_hash = %s AND s.active = TRUE AND a.active = TRUE
                     AND s.expires_at > NOW()""",
                (hashed,)
            )
            row = await result.fetchone()

            if row:
                # Session already exists for this token (re-click of same link)
                account_id = row["account_id"]
                await _touch_session(conn, hashed)
                # First real open of a pre-created link (regenerate's "pending") or a
                # migrated row: stamp the ACTUAL device from the User-Agent, so the sessions
                # list shows "iPhone"/"Mac Chrome" instead of an internal source word. Only
                # when we can parse a real device, and only while the label is a placeholder.
                _dev = _parse_device_label(request.headers.get("user-agent", ""))
                if _dev != "Unknown":
                    await conn.execute(
                        "UPDATE auth_sessions SET device_label = %s "
                        "WHERE token_hash = %s AND (device_label IS NULL OR lower(device_label) "
                        "IN ('','pending','regenerated','migrated','unknown','device'))",
                        (_dev, hashed),
                    )
            else:
                # Fallback: check legacy auth_token on accounts table
                result = await conn.execute(
                    "SELECT id, display_name, agent_name FROM accounts WHERE auth_token = %s AND active = TRUE",
                    (hashed,)
                )
                row = await result.fetchone()
                if not row:
                    return _auth_link_error_response(request, 403, "Invalid or expired link. Ask the Cove operator for a new one.")

                account_id = row["id"]
                # Create a session for this token
                user_agent = request.headers.get("user-agent", "")
                device = _parse_device_label(user_agent)
                await _create_session(conn, account_id, hashed, device)

            # Update last_access on the account
            await conn.execute(
                "UPDATE accounts SET last_access = NOW() WHERE id = %s",
                (account_id,)
            )
            # First-run detection: a freshly-seeded operator (from the centralized
            # generator's operator-seed) has cove_role='admin' but no agent
            # identity yet. Fetch both so we can route them to the wizard below.
            _sresult = await conn.execute(
                "SELECT cove_role, agent_identity FROM accounts WHERE id = %s",
                (account_id,)
            )
            _setup_row = await _sresult.fetchone()
            # Log login event for activity tracking
            try:
                now = datetime.now(timezone.utc)
                await conn.execute(
                    """INSERT INTO tuning_events (presence_id, event_type, event_data, date, time)
                       VALUES (%s, 'login', '{}', %s, %s)""",
                    (account_id, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"))
                )
            except Exception:
                pass  # Never block auth on tracking failure
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[AUTH] Magic link auth failed: {e}")
        return _auth_link_error_response(request, 500, "Something went wrong. Please try again.")

    # Share the session across the Cove's subdomains (domain=cove domain) so one
    # operator login reaches their own MC AND the admin (stuart.) view. Each door is
    # still gated server-side by host_match (kind=manager requires operator). Only
    # applied on the clean scheme where the cove domain is a parent of the host.
    cookie_domain = None
    try:
        from src.config import load_cove_config
        cd = (load_cove_config().get("domain") or "").strip().lower()
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].lower()
        if cd and (host == cd or host.endswith("." + cd)):
            cookie_domain = cd
    except Exception:
        cookie_domain = None

    # Secure cookie only when the connection is actually HTTPS (direct, or via a
    # TLS-terminating proxy like Caddy). A local/mesh self-host served over plain
    # HTTP must still get a working session cookie — forcing Secure there means the
    # browser silently drops it and the operator can never authenticate.
    _xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    cookie_secure = (request.url.scheme == "https") or (_xfp == "https")

    # Route a freshly-seeded operator into the onboarding wizard (first-run);
    # everyone else — members, and operators who've already finished setup —
    # goes to their MC. "Not finished" = operator with an empty agent_identity.
    needs_setup = False
    try:
        if _setup_row and _setup_row["cove_role"] == "admin":
            _ai = _setup_row["agent_identity"]
            if isinstance(_ai, str):
                _ai = json.loads(_ai) if _ai.strip() else {}
            needs_setup = not (isinstance(_ai, dict) and _ai)
    except Exception:
        needs_setup = False

    redirect_to = "/static/action-board/new-cove-setup.html?firstrun=1" if needs_setup else "/"
    # Honor an internal ?next= landing target (e.g. a self-onboard invitee lands straight in
    # Chat with their agent). Strictly internal paths only — never an open redirect.
    if not needs_setup:
        _next = (request.query_params.get("next") or "").strip()
        if _next.startswith("/") and not _next.startswith("//"):
            redirect_to = _next
    response = RedirectResponse(redirect_to)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
        domain=cookie_domain,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear the auth cookie, deactivate the session, and redirect to landing page."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        hashed = _hash_token(token)
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE auth_sessions SET active = FALSE WHERE token_hash = %s",
                    (hashed,)
                )
        except Exception:
            pass  # Don't block logout on DB failure

    response = RedirectResponse("/")
    response.delete_cookie(COOKIE_NAME)
    return response


# =============================================================================
# Presence Context (called by other routes)
# =============================================================================

async def get_current_presence(request: Request) -> Optional[dict]:
    """Get the current Presence from the request cookie.

    Checks auth_sessions table first (multi-session), falls back to
    legacy auth_token column on accounts for backward compatibility.

    Returns None if:
      - COVE_MODE is single
      - No cookie present
      - Token is invalid or expired
    """
    if COVE_MODE != "multi":
        return None

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    hashed = _hash_token(token)

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Check auth_sessions table first (new system)
            result = await conn.execute(
                """SELECT a.id, a.display_name, a.username, a.email, a.agent_name, a.last_name,
                          a.tier, a.cove_role, a.agent_config, a.agent_identity, a.active_workflows, a.api_mode,
                          a.name_locked, a.preferences, a.referral_code, a.referred_by,
                          a.nc_username, a.nc_password,
                          a.created_at, a.last_access
                   FROM auth_sessions s
                   JOIN accounts a ON a.id = s.account_id
                   WHERE s.token_hash = %s AND s.active = TRUE AND a.active = TRUE
                     AND s.expires_at > NOW()""",
                (hashed,)
            )
            row = await result.fetchone()

            if row:
                # Update session last_used timestamp + opportunistically stamp the real
                # device name onto a still-placeholder session (migrated/pending) on normal
                # authenticated use — so already-signed-in devices get a real label, not just
                # ones that re-open a sign-in link. Throttled inside _touch_session.
                await _touch_session(conn, hashed,
                                     _parse_device_label(request.headers.get("user-agent", "")))
                return dict(row)

            # Fallback: check legacy auth_token on accounts table
            result = await conn.execute(
                """SELECT id, display_name, username, email, agent_name, last_name,
                          tier, cove_role, agent_config, agent_identity, active_workflows, api_mode,
                          name_locked, preferences, referral_code, referred_by,
                          nc_username, nc_password,
                          created_at, last_access
                   FROM accounts WHERE auth_token = %s AND active = TRUE""",
                (hashed,)
            )
            row = await result.fetchone()
            if row:
                # Migrate this token to sessions table automatically
                try:
                    user_agent = request.headers.get("user-agent", "")
                    device = _parse_device_label(user_agent)
                    await _create_session(conn, row["id"], hashed, device)
                except Exception:
                    pass  # Don't block auth on migration failure
                return dict(row)

            return None
    except Exception as e:
        logging.error("[PRESENCE] DB lookup failed: %s: %s", type(e).__name__, e)
        return None


def _parse_device_label(user_agent: str) -> str:
    """Extract a short device label from User-Agent string."""
    ua = user_agent.lower()
    if "iphone" in ua:
        return "iPhone"
    elif "ipad" in ua:
        return "iPad"
    elif "android" in ua:
        return "Android"
    elif "macintosh" in ua or "mac os" in ua:
        if "safari" in ua and "chrome" not in ua:
            return "Mac Safari"
        return "Mac Chrome"
    elif "windows" in ua:
        return "Windows"
    elif "linux" in ua:
        return "Linux"
    return "Unknown"


# Labels that aren't a real device — a session created before any device opened the link
# (regenerate's "pending"), a migrated row, or a UA we couldn't parse. These get REPLACED
# with the real parsed device the first time the link is opened, and render as a friendly
# "Not opened yet" in the sessions list instead of an internal source word.
_PLACEHOLDER_DEVICE_LABELS = {"", "pending", "regenerated", "migrated", "unknown", "device"}


def _is_placeholder_label(label) -> bool:
    return (label or "").strip().lower() in _PLACEHOLDER_DEVICE_LABELS


# =============================================================================
# Presence API — Status
# =============================================================================

@router.get("/api/presence/me")
async def presence_me(request: Request):
    """Get the current Presence's info. Used by MC to render per-user UI."""
    if COVE_MODE != "multi":
        return {"cove_mode": "single", "presence": None}

    presence = await get_current_presence(request)
    if not presence:
        return {"cove_mode": "multi", "presence": None, "authenticated": False}

    # Detect auto-generated temp username (from D1 migration: prefix-xxxx hex suffix)
    import re
    uname = presence.get("username") or ""
    _needs_username = bool(re.match(r'^.+-[0-9a-f]{4}$', uname))

    return {
        "cove_mode": "multi",
        "authenticated": True,
        "presence": {
            "id": str(presence["id"]),
            "display_name": presence["display_name"],
            "username": presence.get("username"),
            "needs_username": _needs_username,
            "email": presence.get("email"),
            "agent_name": presence["agent_name"],
            "last_name": presence["last_name"],
            "full_name": f"{presence['agent_name']} {presence['last_name']}",
            "tier": presence.get("tier", "free"),
            "cove_role": presence["cove_role"],
            "active_workflows": presence["active_workflows"],
            "name_locked": presence["name_locked"],
            "referral_code": presence.get("referral_code"),
            "nc_username": presence.get("nc_username"),
            "nc_password": presence.get("nc_password"),
            "has_cloud": bool(presence.get("nc_username")),
            "timezone": presence.get("timezone"),
            "agent_identity": presence.get("agent_identity") or {},
            "created_at": presence["created_at"].isoformat() if presence.get("created_at") else None,
        },
        "cove": {
            "id": COVE_ID,
            "name": COVE_NAME,
        },
    }


@router.patch("/api/presence/me")
async def update_presence_me(request: Request):
    """Update the current Presence's profile fields.

    Editable fields: display_name, username, email, tier.
    Tier change reloads the UI to apply new tab/feature visibility.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Not in multi-Presence mode")

    presence = await get_current_presence(request)
    if not presence:
        raise HTTPException(401, "Not authenticated")

    import re

    body = await request.json()
    allowed_fields = {"display_name", "username", "email", "tier", "timezone"}
    valid_tiers = {"free", "pro", "operator", "presence", "cove"}
    updates = {}

    for field in allowed_fields:
        if field in body:
            val = body[field]
            if field == "tier":
                if val not in valid_tiers:
                    raise HTTPException(400, f"Invalid tier: {val}. Must be one of: {', '.join(sorted(valid_tiers))}")
            if isinstance(val, str):
                val = val.strip()
            # Display name cannot be empty; title-case at write (Woods casing root)
            if field == "display_name":
                if not val:
                    raise HTTPException(400, "Display name cannot be empty")
                val = _titlecase_name(_sanitize_name(val))
            # Username validation
            if field == "username" and val:
                val = val.lower()
                if len(val) < 3 or len(val) > 30:
                    raise HTTPException(400, "Username must be 3-30 characters")
                if not re.match(r'^[a-z0-9][a-z0-9_-]*[a-z0-9]$', val):
                    raise HTTPException(400, "Username can only contain lowercase letters, numbers, hyphens, underscores")
            # Email cannot be empty
            if field == "email" and not val:
                raise HTTPException(400, "Email cannot be empty")
            # Timezone must be valid IANA
            if field == "timezone" and val:
                from zoneinfo import ZoneInfo
                try:
                    ZoneInfo(val)
                except (KeyError, Exception):
                    raise HTTPException(400, f"Invalid timezone: {val}")
            updates[field] = val if val else None

    if not updates:
        raise HTTPException(400, "No valid fields to update")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Check username uniqueness
            if "username" in updates and updates["username"]:
                result = await conn.execute(
                    "SELECT id FROM accounts WHERE username = %s AND id != %s",
                    (updates["username"], presence["id"])
                )
                if await result.fetchone():
                    raise HTTPException(409, "This username is already taken")

            set_parts = []
            params = []
            for field, val in updates.items():
                set_parts.append(f"{field} = %s")
                params.append(val)
            params.append(presence["id"])
            set_clause = ", ".join(set_parts) + ", updated_at = NOW()"
            query = f"UPDATE accounts SET {set_clause} WHERE id = %s"
            await conn.execute(query, tuple(params))
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Settings update failed: {e}")
        raise HTTPException(500, "Something went wrong saving your changes. Please try again.")

    return {"ok": True, "updated": list(updates.keys())}


# =============================================================================
# Operator Endpoints — Presence Management
# =============================================================================

@router.post("/api/presence/create")
async def create_presence(request: Request):
    """Create a new Presence in this Cove. Operator only.

    Body: {
        "display_name": "Friend Name",
        "email": "friend@example.com",
        "agent_name": "Kai",
        "cove_role": "member",          // "admin" | "member" | "guest"
        "agent_config": { ... },        // optional, from Creation Flow
        "send_email": true              // send magic link via Brevo (default true)
    }

    Flow:
      1. Create account in DB
      2. Provision Nextcloud user + folders + Context files
      3. Send magic link email via Brevo (if send_email=true and email provided)

    Returns: { "presence_id": "...", "signin_link": "https://...", "nc": {...} }
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Presence creation only available in multi-Presence mode")

    # TODO: Add operator auth check here
    body = await request.json()
    # Jules 2230: capitalize at the root — operator + agent names both title-case
    # when created (typed "walt"/"hal" must store as "Walt"/"Hal").
    display_name = _titlecase_name(_sanitize_name(body.get("display_name", "")))
    email = body.get("email", "").strip().lower()
    agent_name = _titlecase_name(_sanitize_name(body.get("agent_name", "")))
    cove_role = body.get("cove_role", "member").strip()
    agent_config = body.get("agent_config", {})
    send_email = body.get("send_email", True)

    if not display_name or not agent_name:
        raise HTTPException(400, "display_name and agent_name are required")

    if cove_role not in ("admin", "member", "guest"):
        raise HTTPException(400, "cove_role must be admin, member, or guest")

    return await _create_presence_record(
        display_name=display_name,
        email=email,
        agent_name=agent_name,
        cove_role=cove_role,
        agent_config=agent_config,
        handle=body.get("handle"),
        send_email=send_email,
        request=request,
    )


def _slugify_handle(name: str) -> str:
    """Turn a display name into a subdomain-safe handle: 'JAG' -> 'jag'."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "presence"


def _sanitize_name(s: str) -> str:
    """Server-side mirror of the client _sanitizeAgentName (new-agent-setup.html):
    NFKD-fold accents, keep only letters/digits/space/apostrophe/hyphen, collapse
    whitespace. Belt-and-suspenders so a name reaching the API directly (or via stale
    cached JS) can't persist control chars, markdown asterisks, or emoji into the
    agent/Cove name that shows in every MC header, chat label, and the registry."""
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9 '\-]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _titlecase_name(s: str) -> str:
    """Per-word first-letter uppercase, preserving internal caps (McLeod stays McLeod).
    Mirrors the wizard's _capname/_titleCaseName so a lowercase-typed agent name stores
    capitalized on the member-add path too — the founding-root path already caps, this
    gives parity so 'janet' becomes 'Janet' everywhere."""
    return " ".join((w[:1].upper() + w[1:]) if w else w for w in (s or "").split(" "))


async def _unique_handle(conn, base: str) -> str:
    """Ensure the handle is unique within this Cove (accounts.username)."""
    handle, n = base, 1
    while True:
        r = await conn.execute("SELECT 1 FROM accounts WHERE username = %s", (handle,))
        if not await r.fetchone():
            return handle
        n += 1
        handle = f"{base}-{n}"


# Handles that would collide with system/manager subdomains under {cove}.{domain}.
_RESERVED_HANDLES = {
    "www", "app", "api", "admin", "mail", "cloud", "sync", "vault", "ollama",
    "flow", "voice", "haven", "matrix", "chat", "ntfy", "ns", "ftp", "root",
    "atlas", "lt", "socrates", "support", "help", "status", "operator", "presence",
}


def _handle_validity(handle: str, cove: dict):
    """Normalize + validate a chosen handle. Returns (normalized, error_or_None)."""
    h = _slugify_handle(handle)
    if len(h) < 2:
        return h, "Handle must be at least 2 characters."
    if len(h) > 32:
        return h, "Handle must be 32 characters or fewer."
    reserved = set(_RESERVED_HANDLES)
    reserved.add((cove.get("name") or "").strip().lower())
    reserved.add((cove.get("id") or "").strip().lower())
    for ch in ("steward_channel", "merchant_channel"):
        nm = ((cove.get(ch) or {}).get("name") or "").strip().lower()
        if nm:
            reserved.add(nm)
    if h in reserved - {""}:
        return h, "That handle is reserved."
    return h, None


@router.get("/api/presence/handle-available")
async def handle_available(handle: str, request: Request):
    """Live availability check for the @handle picker in the creation flow."""
    if COVE_MODE != "multi":
        return {"available": False, "reason": "Not in multi-Presence mode"}
    from src.config import load_cove_config
    norm, err = _handle_validity(handle, load_cove_config())
    if err:
        return {"available": False, "handle": norm, "reason": err}
    # The current operator's own handle is theirs (claim/upgrade — the seeded row
    # already holds it). Never report it as "taken" to its owner.
    try:
        _cur = await get_current_presence(request)
        if _cur and (_cur.get("username") or "").lower() == norm:
            return {"available": True, "handle": norm, "reason": None}
    except Exception:
        pass
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute("SELECT 1 FROM accounts WHERE username = %s", (norm,))
            taken = bool(await r.fetchone())
    except Exception:
        return {"available": False, "handle": norm, "reason": "Couldn't check right now, try again."}
    # Network-wide uniqueness + the 3-state status from the hub (#4): the local check
    # above only sees this Cove's accounts; the hub registry sees the whole network.
    #   available          → free to claim
    #   claimed            → a live Cove owns it (unavailable)
    #   account_unclaimed  → an LP account owns the handle but no Cove yet → the owner
    #                        can "sign in to claim it for their Cove" (the @jag case)
    hub_status = None
    if not taken:
        try:
            from src.dashboard.routes import registry_client
            if registry_client.configured():
                rr = await registry_client.check_availability(handle=norm)
                if rr.get("ok"):
                    hub_status = rr.get("handle_status")
                    if rr.get("handle_available") is False:
                        taken = True
        except Exception:
            pass
    if hub_status == "account_unclaimed":
        return {"available": False, "handle": norm, "status": "account_unclaimed",
                "claimable": True,
                "reason": "That handle belongs to a Lucid Principles account. If it's yours, sign in to claim it for your Cove."}
    return {"available": not taken, "handle": norm,
            "status": (hub_status or ("claimed" if taken else "available")),
            "reason": None if not taken else "That handle is already taken."}


@router.get("/api/cove/name-available")
async def cove_name_available(name: str, request: Request):
    """Availability check for the Cove name at onboarding.

    The Cove name (+ operator handle) is reserved against the Haven hub registry
    so names stay globally unique across the network — that reservation IS what
    locks in a Cove. On-network (LP_REGISTRY_URL set) this asks the hub registry,
    which also enforces the reserved-brand list. A fully-private self-host that
    never points at a hub is free to use any name (local format check only).
    TODO(#161): enforce the squatting policy (claimed-but-unused reclamation).
    """
    n = (name or "").strip()
    if len(n) < 2:
        return {"available": False, "name": n, "reason": "Give your Cove a name (at least 2 characters)."}
    if len(n) > 40:
        return {"available": False, "name": n, "reason": "That name is a bit long (40 characters max)."}
    # Global uniqueness via the hub registry (on-network only). Best-effort: a hub
    # hiccup shouldn't hard-block naming — the finalize step re-checks before it commits.
    try:
        from src.dashboard.routes import registry_client
        if registry_client.configured():
            rr = await registry_client.check_availability(name=n)
            if rr.get("ok") and rr.get("name_available") is False:
                return {"available": False, "name": n,
                        "reason": "That Cove name is already taken across the network."}
    except Exception:
        pass
    return {"available": True, "name": n, "reason": None}


async def _create_presence_record(
    *,
    display_name: str,
    email: str,
    agent_name: str,
    cove_role: str,
    agent_config: dict = None,
    agent_identity: dict = None,
    handle: str = None,
    send_email: bool = True,
    request: Request,
) -> dict:
    """Core Presence data-entry creation (the Centralized model's unit of work).

    Creates the accounts row + initial auth session, provisions a Nextcloud user
    with the steward-share boundary, and sends a magic link. No container, port,
    IP, or DB is allocated — a Presence is a row, not a stack.

    Shared by:
      - create_presence       (manual operator add, agent_identity = {})
      - provision_presence     (archetype discovery flow, agent_identity populated)
    """
    agent_config = agent_config or {}
    agent_identity = agent_identity or {}
    # Belt-and-suspenders: normalize the operator-supplied names server-side too, so a
    # direct API call or stale cached JS can't store junk (idempotent on names the
    # client already cleaned).
    display_name = _titlecase_name(_sanitize_name(display_name))
    agent_name = _titlecase_name(_sanitize_name(agent_name))
    from src.config import load_cove_config
    _cove = load_cove_config()
    # last_name = the Cove name for EVERY presence (the agent's full name is
    # "{agent} {Cove}"). Use the LIVE cove.yaml name — set when the founder named
    # the Cove in the wizard — not the static COVE_NAME startup env (which stays
    # the generator's placeholder, e.g. "Test").
    _cove_name = (_cove.get("name") or COVE_NAME or "").strip()
    # Never stamp the provisioner's "New Cove" placeholder into accounts.last_name —
    # it leaks into every display until finalize (CF-89's DB-side sibling).
    if _cove_name.lower() == "new cove":
        _cove_name = ""

    # Tier gives the Presence a real agent MC (with Chat), not the free Tuner view.
    # operator → cove; member/guest → presence (both above the chat gate).
    tier = "cove" if cove_role == "admin" else "presence"

    presence_id = uuid.uuid4()
    raw_token = secrets.token_urlsafe(32)
    hashed_token = _hash_token(raw_token)

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Handle = the subdomain label for this operator's MC + their @handle in
            # Connect/Market. If the creator chose one, validate + ensure it's free;
            # otherwise auto-derive a unique one from the name.
            if handle:
                handle, herr = _handle_validity(handle, _cove)
                if herr:
                    raise HTTPException(400, herr)
                _r = await conn.execute("SELECT 1 FROM accounts WHERE username = %s", (handle,))
                if await _r.fetchone():
                    raise HTTPException(409, "That handle is already taken.")
            else:
                handle = await _unique_handle(conn, _slugify_handle(display_name or agent_name))
            ref_code = None
            try:
                from src.dashboard.routes.account import _generate_referral_code
                ref_code = await _generate_referral_code(conn)
            except Exception:
                ref_code = None
            if ref_code:
                await conn.execute(
                    """INSERT INTO accounts (id, display_name, username, email, agent_name, last_name,
                                              cove_role, tier, auth_token, agent_config, agent_identity,
                                              referral_code)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (presence_id, display_name, handle, email or None, agent_name, _cove_name,
                     cove_role, tier, hashed_token, json.dumps(agent_config),
                     json.dumps(agent_identity), ref_code)
                )
            else:
                await conn.execute(
                    """INSERT INTO accounts (id, display_name, username, email, agent_name, last_name,
                                              cove_role, tier, auth_token, agent_config, agent_identity)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (presence_id, display_name, handle, email or None, agent_name, _cove_name,
                     cove_role, tier, hashed_token, json.dumps(agent_config),
                     json.dumps(agent_identity))
                )
            # Also create a session for the initial token
            await _create_session(conn, presence_id, hashed_token, "initial")
    except HTTPException:
        raise
    except Exception as e:
        log.error("Create presence failed: %s", e)
        raise HTTPException(500, "Something went wrong. Please try again.")

    # Magic link lands on the operator's own subdomain when the Cove has subdomain
    # routing (wildcard DNS/Caddy) live — the session cookie then scopes to that
    # subdomain. Falls back to the Cove root otherwise, so Coves without a wildcard
    # (or single-mode) are never handed a link that won't resolve.
    _cove_domain = (_cove.get("domain") or "").strip()
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if _cove.get("subdomain_routing") and _cove_domain:
        signin_link = f"{scheme}://{handle}.{_cove_domain}/p/{raw_token}"
    elif _cove_domain:
        signin_link = f"https://{_cove_domain}/p/{raw_token}"
    else:
        host = request.headers.get("host", "localhost")
        signin_link = f"{scheme}://{host}/p/{raw_token}"

    # Provision Nextcloud user + folders + Context files
    nc_result = {"ok": False, "skipped": True}
    try:
        from src.dashboard.routes.nextcloud import provision_nc_user
        nc_result = await provision_nc_user(
            str(presence_id), display_name, handle=handle,
            tier=tier, role="steward" if cove_role == "admin" else "member",
        )
    except Exception as e:
        log.error("NC provisioning EXCEPTION for %s (presence %s): %s",
                  display_name, presence_id, e)
        nc_result = {"ok": False, "error": str(e)}
    # Don't let a provisioning failure pass silently — a presence with no folders
    # is broken even though creation "succeeded". Surface it loudly so it's caught.
    if not nc_result.get("ok"):
        log.error("NC PROVISIONING FAILED for %s (presence %s) — folders NOT created: %s",
                  display_name, presence_id, nc_result.get("error") or nc_result)

    # Invite the new presence into the Cove's Connect Space + Family room (builds
    # the steward-owned Space on first use). Non-fatal — a Matrix hiccup must not
    # break presence creation.
    try:
        from src.dashboard.routes.matrix_spaces import invite_presence_to_cove_space
        await invite_presence_to_cove_space(handle)
    except Exception as e:
        log.warning("Matrix Space invite for %s (non-fatal): %s", handle, e)

    # Send magic link email via Brevo
    email_sent = False
    if send_email and email:
        try:
            from src.dashboard.routes.email import send_signin_link
            email_sent = await send_signin_link(email, signin_link, is_signup=True)
        except Exception as e:
            log.warning("Email send failed for %s (non-fatal): %s", email, e)

    return {
        "presence_id": str(presence_id),
        "display_name": display_name,
        "email": email,
        "agent_name": agent_name,
        "full_name": f"{agent_name} {_cove_name}",
        "cove_role": cove_role,
        "handle": handle,
        "signin_link": signin_link,
        "token": raw_token,
        "email_sent": email_sent,
        "nc": nc_result,
    }


@router.post("/api/presence/provision")
async def provision_presence(request: Request):
    """Create a Presence from the archetype discovery flow — the CENTRALIZED model.

    The Centralized counterpart to /api/flow/agent-provision (which builds an
    Isolated container overlay). Here the derived agent identity lands as a DATA
    ENTRY: an accounts row carrying agent_identity (JSONB), plus a Nextcloud user
    and a magic link. No new container, port, IP, DB, or Caddy route.

    Body (discovery-flow output + the new person's details):
        display_name : str   — the human operator/member's name (required)
        agent_name   : str   — the personal agent's name (required; alias: name)
        email        : str   — contact email (optional; magic link sent if present)
        cove_role    : str   — admin | member | guest (default member)
        archetype, archetype_desc, frequency, frequency_color, frequency_essence,
        tuning_key, tuning_key_song, pronouns, gender, qualities, feeling,
        persona, first_message, role, agent_config

    Returns: { ok, presence_id, signin_link, agent_identity, nc, ... }
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Presence provisioning only available in multi-Presence mode")

    body = await request.json()
    display_name = body.get("display_name", "").strip()
    # Jules 2153/2230: title-case at the root so a lowercase-typed name never
    # reaches the spark / wake / MC as "matt"/"walt" — same rule as finalize.
    display_name = _titlecase_name(_sanitize_name(display_name)) if display_name else display_name
    agent_name = _titlecase_name(_sanitize_name(body.get("agent_name") or body.get("name") or ""))
    email = body.get("email", "").strip().lower()
    cove_role = body.get("cove_role", "member").strip()
    send_email = body.get("send_email", True)

    if not display_name or not agent_name:
        raise HTTPException(400, "display_name and agent_name are required")
    if cove_role not in ("admin", "member", "guest"):
        raise HTTPException(400, "cove_role must be admin, member, or guest")

    # Build the derived-identity object — the Centralized analog of the agent.yaml
    # entry the Isolated path writes to a file. This is the Presence's agent config.
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
        "role": body.get("role", "") or f"Personal agent — {archetype}",
        "channels": ["day", "deep"],
        # Personality dials — baked into the model now; runtime prompt wiring follows.
        "personality": _merge_personality(body.get("personality")),
        # Shade — optional secondary frequency (Color Signature Layer 3).
        "shade": _clean_shade(body.get("shade")),
        # Lens — the operator's perspective (chips + statement + standing preferences).
        "lens": _clean_lens(body.get("lens")),
        # Avatar — the archetype's symbolic image (swappable per agent later).
        "avatar": (body.get("avatar") or "").strip(),
        # Which of the three input doors created this Presence (quick | guided | dictate).
        "input_mode": (body.get("input_mode") or "guided").strip(),
        "provisioned_at": datetime.now(timezone.utc).isoformat(),
    }

    # team_active: the Cove/operator activation switch (hot vs dormant). Captured here;
    # the LTP scheduler + background schedules gate on it (deliberate follow-on wiring).
    agent_config = body.get("agent_config", {}) or {}
    if "team_active" in body:
        agent_config["team_active"] = bool(body.get("team_active"))
    if body.get("model_provider"):
        # Provider only (no key in the wizard); key + per-model selection live in
        # Settings / the #121 model layer, where they're encrypted at rest.
        agent_config["model_provider"] = str(body.get("model_provider")).strip()

    result = await _create_presence_record(
        display_name=display_name,
        email=email,
        agent_name=agent_name,
        cove_role=cove_role,
        agent_config=agent_config,
        agent_identity=agent_identity,
        handle=body.get("handle"),
        send_email=send_email,
        request=request,
    )
    result["ok"] = True
    result["agent_identity"] = agent_identity

    # Birth memory for the new Presence's agent (best-effort, non-fatal).
    _npid = result.get("id") or result.get("presence_id")
    if _npid:
        _refl = (body.get("reflection") or agent_identity.get("feeling") or "").strip()
        try:
            from src.config import load_cove_config as _lcc2
            _cn = (_lcc2().get("name") or "")
        except Exception:
            _cn = ""
        await _seed_birth_memory(_npid, display_name, agent_identity, _refl, _cn)
    return result


@router.post("/api/presence/seed-memory")
async def seed_presence_memory(request: Request):
    """Seed a memory for a Presence's agent (the wake-moment answer).

    Default target is the CURRENT Presence's agent — scoped to the presence id,
    the same key the agent reads memory under. Used by the wake step to store the
    operator's answer as the agent's second memory.

    Optional `presence_id` in the body retargets the write to ANOTHER Presence's
    agent. This is the member-wake case: the operator brings a family member's
    agent to life and meets it on the member's behalf, so the memory must land
    under the MEMBER's presence (not the operator's, which would mis-attribute).
    Retargeting requires the caller to be a Cove admin AND the target id to exist
    in this Cove's accounts. Best-effort: returns ok:false on any issue, never
    raises into the wizard.
    """
    if COVE_MODE != "multi":
        return {"ok": False, "skipped": True}
    presence = await get_current_presence(request)
    if not presence or not presence.get("id"):
        raise HTTPException(401, "Not authenticated")
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    content = (body.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "empty"}
    category = (body.get("category") or "person").strip()
    try:
        importance = float(body.get("importance", 0.85))
    except (TypeError, ValueError):
        importance = 0.85

    # Resolve the target agent id. Default = the current presence. If a
    # presence_id is supplied and differs, retarget to that member's agent —
    # but only for a Cove admin, and only if the id exists in this Cove's DB.
    target_id = str(presence["id"])
    req_id = (str(body.get("presence_id")).strip() if body.get("presence_id") else "")
    if req_id and req_id != str(presence["id"]):
        if presence.get("cove_role") != "admin":
            return {"ok": False, "error": "not authorized to seed another presence"}
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                _r = await conn.execute(
                    "SELECT id FROM accounts WHERE id = %s AND active = TRUE",
                    (req_id,)
                )
                _row = await _r.fetchone()
        except Exception as e:
            log.warning("seed-memory target lookup failed (non-fatal): %s", e)
            return {"ok": False, "error": "target lookup failed"}
        if not _row:
            return {"ok": False, "error": "target presence not found"}
        target_id = req_id

    try:
        from src.memory.memory import store_memory
        await store_memory(
            content=content[:2000], category=category, importance=importance,
            tags=["wake", "origin"], agent_id=target_id,
            source_channel="wake", source_summary="Seeded at the wake moment",
        )
        return {"ok": True}
    except Exception as e:
        log.warning("seed-memory failed (non-fatal): %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/api/presence/me/finalize")
async def finalize_setup(request: Request):
    """First-run finalize — the onboarding wizard finishes the install.

    The centralized generator seeds the founding operator with a placeholder
    agent + empty agent_identity, then hands them a claim link. The wizard
    (claim link -> first-run) collects the Cove name + the operator's personal
    agent design, then calls this to:
      - write the operator's agent_name + agent_identity onto their seeded row,
      - set the Cove display name (cove.yaml),
      - provision the operator's Nextcloud user/folders (the seed skips NC),
    and routes them into their MC.

    Distinct from /api/presence/provision, which INSERTS a NEW Presence (adding
    other people from inside the MC). This UPDATES the current operator in place,
    so it never creates a duplicate operator row.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Not in multi-Presence mode")

    presence = await get_current_presence(request)
    if not presence or not presence.get("id"):
        raise HTTPException(401, "Not authenticated")

    body = await request.json()
    agent_name = _sanitize_name(body.get("agent_name") or body.get("name") or "")
    cove_name = _sanitize_name(body.get("cove_name") or "")
    # jules 07-07: capitalize the agent's name AND the Cove name AT THE ROOT (when saved) — not
    # just for display. Both are stored (agent_name/agent_identity + last_name/cove.yaml/registry),
    # so a lowercase-typed "beth"/"breckenridge" becomes "Beth"/"Breckenridge" everywhere (MC header
    # included). Per-word first-letter uppercase, preserving internal caps (McLeod stays McLeod).
    def _capname(_s):
        return " ".join((w[:1].upper() + w[1:]) for w in _s.split(" ") if w) if _s else _s
    agent_name = _capname(agent_name)
    cove_name = _capname(cove_name)
    # Both are REQUIRED: naming the Cove + creating the founding Presence's agent
    # IS the registration act (Cove name + operator handle reserved against the
    # Haven registry, #133). Neither is optional — together they lock in the Cove.
    if not cove_name:
        raise HTTPException(400, "cove_name is required — naming the Cove is what registers it")
    if not agent_name:
        raise HTTPException(400, "agent_name is required — the operator's personal agent forms the founding Presence")

    # Build the operator's personal-agent identity (same shape as provision_presence).
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
        "role": body.get("role", "") or f"Personal agent — {archetype}",
        "channels": ["day", "deep"],
        "personality": _merge_personality(body.get("personality")),
        "shade": _clean_shade(body.get("shade")),
        "lens": _clean_lens(body.get("lens")),
        "avatar": (body.get("avatar") or "").strip(),
        "input_mode": (body.get("input_mode") or "guided").strip(),
        "provisioned_at": datetime.now(timezone.utc).isoformat(),
    }

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

    pid = str(presence["id"])
    new_handle = (body.get("handle") or "").strip()

    # Network-wide uniqueness gate — refuse a taken Cove name or @handle at the finishing
    # step, not just locally. This is the real "can't take a taken one" enforcement for a
    # from-scratch install (the wizard's live checks warn; this is the hard stop before we
    # commit + register). On-network only (LP_REGISTRY_URL); a private off-network Cove has
    # no shared namespace, so it skips. Best-effort on a hub hiccup (set-once registry is
    # the final backstop).
    try:
        from src.dashboard.routes import registry_client
        from src.config import load_cove_config as _lcc
        cur_uname = (presence.get("username") or "").strip().lower()
        cur_cove = (_lcc().get("name") or "").strip().lower()
        # The operator's handle is ALREADY theirs (claimed in wizard step 1), and on a
        # retry the Cove name is already saved as theirs — so only re-check a value that's
        # genuinely CHANGED. Otherwise we'd flag the operator's own just-claimed handle as
        # "taken" (the hub now lists it), which blocked finalize.
        chk_handle = bool(new_handle) and new_handle.strip().lower() != cur_uname
        chk_name = bool(cove_name) and cove_name.strip().lower() != cur_cove
        if registry_client.configured() and (chk_handle or chk_name):
            rr = await registry_client.check_availability(
                name=(cove_name if chk_name else ""),
                handle=(new_handle if chk_handle else ""))
            if rr.get("ok"):
                if chk_name and rr.get("name_available") is False:
                    raise HTTPException(409, "That Cove name is already taken — pick another.")
                if chk_handle and rr.get("handle_available") is False:
                    raise HTTPException(409, "That handle is already taken — pick another.")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Finalize hub availability check skipped (non-fatal): %s", e)

    try:
        from src.config import load_cove_config
        _cove = load_cove_config()
        from src.memory.database import get_db
        async with get_db() as conn:
            # last_name carries the Cove name (the agent's full name is
            # "{agent} {Cove}"). The seed set it to the generator's placeholder
            # (e.g. "Test"); the wizard's name has to override it.
            set_parts = ["agent_name = %s", "agent_identity = %s", "agent_config = %s", "last_name = %s"]
            params = [agent_name, json.dumps(agent_identity), json.dumps(agent_config), cove_name]
            # The operator's OWN name (the human). Without this, the value the
            # generator seeded (e.g. "Alex") would persist no matter what the
            # operator entered in the wizard.
            _disp = _titlecase_name(_sanitize_name(
                body.get("display_name") or body.get("person") or ""))
            if _disp:
                set_parts.append("display_name = %s")
                params.append(_disp)
            if new_handle:
                h, herr = _handle_validity(new_handle, _cove)
                if herr:
                    raise HTTPException(400, herr)
                r = await conn.execute(
                    "SELECT 1 FROM accounts WHERE username = %s AND id != %s", (h, pid)
                )
                if await r.fetchone():
                    raise HTTPException(409, "That handle is already taken.")
                set_parts.insert(0, "username = %s")
                params.insert(0, h)
            params.append(pid)
            await conn.execute(
                f"UPDATE accounts SET {', '.join(set_parts)}, updated_at = NOW() WHERE id = %s",
                tuple(params),
            )
        # Single-source the Cove family name for SYNC readers (get_full_name + the LTP
        # graph) that can't await resolve_cove_name: mirror the wizard's Cove name into
        # system_settings so agent display names stop reading the stale agent.yaml
        # "New Cove" placeholder. See cove-name-leak-deepdive.md (#CF-89).
        try:
            from src.utils.settings import update_setting
            if (cove_name or "").strip():
                await update_setting("family_name", cove_name.strip())
        except Exception as _e:
            log.warning("family_name setting mirror skipped (non-fatal): %s", _e)
    except HTTPException:
        raise
    except Exception as e:
        log.error("Finalize setup failed for %s: %s", pid, e)
        raise HTTPException(500, "Something went wrong finishing setup. Please try again.")

    # The agent's first memory: being born from this person (the wake moment). Best-effort.
    _reflection = (body.get("reflection") or agent_identity.get("feeling") or "").strip()
    await _seed_birth_memory(pid, _disp or presence.get("display_name") or "",
                             agent_identity, _reflection, cove_name)

    # Persist the Cove name — the wizard is where the Cove is named/registered.
    # (COVE_NAME is a startup env; this lands in cove.yaml for live config reads
    # and is authoritative on next boot.)
    try:
        from src.config import save_cove_config
        save_cove_config({"name": cove_name})
    except Exception as e:
        log.warning("Cove name save failed (non-fatal): %s", e)

    # #133/#169 — register this Cove with the Haven hub on setup completion. OPT-IN:
    # only fires when LP_REGISTRY_URL is set (a fully-private self-host that never sets
    # it phones home to no one). Carries the affiliate edge (referred_by) so the
    # operator who referred this self-hoster gets credit — this is what makes the
    # documented "compose up" self-host path (no provisioner run) still join the
    # network + attribute the referral. Best-effort, non-fatal; set-once on the hub.
    try:
        if env("LP_REGISTRY_URL"):
            from src.config import get_instance, load_cove_config
            from src.dashboard.routes import registry_client
            _inst = get_instance() or {}
            _ref = (env("LP_REFERRED_BY")
                    or ((load_cove_config().get("affiliate") or {}).get("referred_by"))
                    or "").strip()
            _cid = (_inst.get("id") or env("COVE_ID")
                    or cove_name.lower().replace(" ", "-"))
            _payload = {
                "cove_id": _cid, "name": cove_name,
                "owner_handle": new_handle or presence.get("username", "") or "",
                "domain": _inst.get("domain", "") or "",
                "referred_by": _ref,
            }
            # C3-5: this used to be one best-effort shot — a fresh box with an
            # unsettled network silently never joined the hub, and the SET-ONCE
            # referred_by edge was lost forever. Persist the full payload on
            # failure; the scheduler retries until the hub acks.
            from src.utils.hub_retry import (mark_registration_pending,
                                             clear_registration_pending)
            try:
                _rr = await registry_client.register_cove(**_payload)
            except Exception as _re:
                _rr = {"ok": False, "reason": str(_re)[:200]}
            log.info("Hub registration at finalize: %s (referred_by=%s)", _rr, _ref or "none")
            if _rr.get("ok"):
                await clear_registration_pending()
            else:
                await mark_registration_pending(_payload)
    except Exception as e:
        log.warning("Hub registration at finalize failed (non-fatal): %s", e)

    # Provision the operator's Nextcloud user/folders (the seed skips NC).
    nc_result = {"ok": False, "skipped": True}
    if not presence.get("nc_username"):
        try:
            from src.dashboard.routes.nextcloud import provision_nc_user
            # Use the name the operator just entered in the wizard (_disp), NOT the
            # stale seed value on the pre-update `presence` dict — otherwise NC gets
            # provisioned as "Alex" even though the row now says the real name.
            nc_result = await provision_nc_user(
                pid, _disp or presence.get("display_name") or agent_name,
                handle=(new_handle or presence.get("username") or ""),
                tier=presence.get("tier") or "cove", role="steward",
            )
        except Exception as e:
            log.error("NC provisioning during finalize failed for %s: %s", pid, e)
            nc_result = {"ok": False, "error": str(e)}
        if not nc_result.get("ok"):
            log.error("NC PROVISIONING FAILED during finalize for operator %s: %s",
                      pid, nc_result.get("error") or nc_result)

    return {"ok": True, "redirect": "/", "agent_identity": agent_identity, "nc": nc_result}


_KNOWN_PROVIDERS = {"openrouter", "openai", "google", "groq", "ollama"}  # moonshot retired


async def _verify_model_key(provider: str, api_key: str):
    """Best-effort liveness check of a BYOK key. Returns True (the provider accepted
    it), False (the provider REJECTED it — 401/403), or None (inconclusive: network
    error, timeout, or a provider we can't cheaply check). We only BLOCK a save on an
    explicit rejection, so a flaky check never traps a legitimate operator."""
    provider = (provider or "").lower().strip()
    if provider == "ollama":
        return True            # local, no key needed
    if not api_key:
        return None
    if provider == "google":
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        headers = {}
    else:
        bases = {
            "openrouter": "https://openrouter.ai/api/v1/key",
            "openai": "https://api.openai.com/v1/models",
            "groq": "https://api.groq.com/openai/v1/models",
        }
        if provider not in bases:
            return None
        url = bases[provider]
        headers = {"Authorization": f"Bearer {api_key}"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code in (401, 403):
            return False
        if 200 <= r.status_code < 300:
            return True
        return None
    except Exception:
        return None


@router.post("/api/settings/model-key")
async def save_model_key(request: Request):
    """Save the operator's BYOK model key at onboarding (or later in Settings).

    Stores provider + key on the presence's agent_config. This IS wired into the live
    model calls: chat.py and flow_chat.py apply it per-request via set_request_byok()
    (cleared after), so adding a key here immediately drives the agent. Local Ollama
    needs no key. TODO(#121): encrypt the key at rest.

    Hardened: rejects unknown providers, VERIFIES a real key with the provider before
    accepting it (blocking only on an explicit rejection), and fails loudly if the DB
    write matches no row — so a garbage key or a silent no-op can't look "connected".
    Returns the resulting {ok, provider, has_key, verified} so the caller can confirm.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Not in multi-Presence mode")
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    provider = (body.get("provider") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    # An explicit model id — the SPECIFIC local model the operator picked from the machine
    # probe (Add-Intelligence), so the brain runs a model that's actually installed instead
    # of a hardcoded guess. Optional: omitted → the provider's default (prior behavior).
    model = (body.get("model") or "").strip()
    disconnect = body.get("disconnect") is True

    if disconnect:
        # Clear BYOK — fall back to Cove default
        ac = p.get("agent_config") or {}
        if isinstance(ac, str):
            try:
                ac = json.loads(ac) or {}
            except Exception:
                ac = {}
        ac = dict(ac)
        ac.pop("model_provider", None)
        ac.pop("model_api_key", None)
        ac.pop("model_name", None)
        ac.pop("intelligence_configured", None)
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                cur = await conn.execute(
                    "UPDATE accounts SET agent_config = %s, updated_at = NOW() WHERE id = %s",
                    (json.dumps(ac), str(p["id"])),
                )
                rows = getattr(cur, "rowcount", None)
        except Exception as e:
            log.error("Disconnect model key failed: %s", e)
            raise HTTPException(500, "Couldn't disconnect right now.")
        if rows == 0:
            return JSONResponse({"ok": False, "error": "Couldn't disconnect — your account wasn't found. Try reloading."}, status_code=200)
        print(f"[model-key] DISCONNECT account={p.get('id')} rows={rows}")
        return {"ok": True, "provider": "", "has_key": False, "verified": False}

    if provider and provider.lower() not in _KNOWN_PROVIDERS:
        return JSONResponse({"ok": False, "error": f"Unknown provider '{provider}'."}, status_code=200)
    if provider.lower() != "ollama" and not api_key and not provider:
        return JSONResponse({"ok": False, "error": "Pick a provider and paste a key (or choose Ollama)."}, status_code=200)

    # Verify a real key before accepting it. Block ONLY on an explicit rejection.
    verified = None
    if api_key and provider.lower() != "ollama":
        verified = await _verify_model_key(provider, api_key)
        if verified is False:
            return JSONResponse(
                {"ok": False, "error": "That key didn't work — the provider rejected it. Double-check it and try again."},
                status_code=200)
    elif provider.lower() == "ollama":
        verified = True

    ac = p.get("agent_config") or {}
    if isinstance(ac, str):
        try:
            ac = json.loads(ac) or {}
        except Exception:
            ac = {}
    ac = dict(ac)
    if provider:
        ac["model_provider"] = provider
    if api_key:
        ac["model_api_key"] = api_key  # TODO(#121): encrypt at rest
    if model:
        ac["model_name"] = model       # the specific (local) model picked from the probe
    elif provider:
        ac.pop("model_name", None)     # provider changed without a specific model → clear stale
    # Mark intelligence EXPLICITLY configured by the operator (picking Ollama or a
    # key). The onboarding "done" check keys off this, so a provisioner default
    # (e.g. ollama with nothing running) no longer falsely shows as connected.
    if provider or api_key:
        ac["intelligence_configured"] = True
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            cur = await conn.execute(
                "UPDATE accounts SET agent_config = %s, updated_at = NOW() WHERE id = %s",
                (json.dumps(ac), str(p["id"])),
            )
            rows = getattr(cur, "rowcount", None)
    except Exception as e:
        log.error("Save model key failed: %s", e)
        raise HTTPException(500, "Couldn't save that right now.")
    if rows == 0:
        # The write matched no account — surface it instead of a false "connected".
        log.error("Save model key matched no account row for presence id=%s", p.get("id"))
        return JSONResponse({"ok": False, "error": "Couldn't save that — your account wasn't found. Try reloading."}, status_code=200)
    print(f"[model-key] SAVE account={p.get('id')} provider={provider or '(unchanged)'} "
          f"has_key={bool(api_key)} verified={verified} rows={rows}")
    # When the Cove ADMIN connects intelligence, this BECOMES the Cove's brain — every
    # agent + scheduled job uses it (not just this operator's own requests). Per-operator
    # BYOK still overrides per-person. Members connecting their own key don't move the brain.
    try:
        if provider and (p.get("cove_role") == "admin"):
            from src.models.provider import apply_cove_model
            apply_cove_model(provider, api_key, model=model)
            print(f"[model-key] Cove brain set by admin → {provider} ({model or 'default'})")
    except Exception as _e:
        log.warning("apply_cove_model failed: %s", _e)
    return {"ok": True, "provider": ac.get("model_provider", ""),
            "model": ac.get("model_name", ""),
            "has_key": bool((ac.get("model_api_key") or "").strip()),
            "verified": bool(verified)}


@router.get("/api/settings/model-key")
async def get_model_key(request: Request):
    """Current BYOK intelligence state for the Settings UI — provider + whether a key
    is set. NEVER returns the key itself."""
    if COVE_MODE != "multi":
        return {"provider": "", "has_key": False, "configured": False}
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return {"provider": "", "has_key": False, "configured": False}
    ac = p.get("agent_config") or {}
    if isinstance(ac, str):
        try:
            ac = json.loads(ac) or {}
        except Exception:
            ac = {}
    _prov = (ac.get("model_provider") or "")
    print(f"[model-key] GET account={p.get('id')} provider={_prov or '(none)'} "
          f"has_key={bool((ac.get('model_api_key') or '').strip())}")
    return {
        "provider": _prov,
        "model": (ac.get("model_name") or ""),
        "has_key": bool((ac.get("model_api_key") or "").strip()),
        "configured": bool(ac.get("intelligence_configured")),
    }


@router.get("/api/presence/list")
async def list_presences(request: Request):
    """List all Presences in this Cove. Cove admin only."""
    if COVE_MODE != "multi":
        return {"cove_mode": "single", "presences": []}

    # Admin-gated: this is the roster behind the Cove-admin surface. The existing
    # caller (admin settings view) is already an admin, so this adds no regression.
    _me = await get_current_presence(request)
    if not _me or _me.get("cove_role") != "admin":
        raise HTTPException(403, "Admin only.")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, display_name, agent_name, agent_identity, last_name, cove_role,
                          active_workflows, name_locked, created_at, last_access, active
                   FROM accounts ORDER BY created_at"""
            )
            rows = await result.fetchall()
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")

    presences = []
    for row in rows:
        # Resolve the agent name: accounts.agent_name is the primary source, but for
        # presences created via the centralized provisioner it's blank until the
        # operator finishes the agent-setup step. Fall back to agent_identity.agent_name
        # (where "Knight" etc. live from the archetype discovery flow) so the roster
        # never shows a blank or generic "Agent" label.
        agent_name = (row["agent_name"] or "").strip()
        if not agent_name:
            _ai = row["agent_identity"]
            _ai = _ai if isinstance(_ai, dict) else (
                json.loads(_ai) if isinstance(_ai, str) and _ai.strip() else {})
            agent_name = (_ai.get("agent_name") or "").strip()
        presences.append({
            "id": str(row["id"]),
            "display_name": row["display_name"],
            "agent_name": agent_name,
            "last_name": row["last_name"],
            "full_name": f"{agent_name} {row['last_name']}".strip(),
            "cove_role": row["cove_role"],
            "active": row["active"],
            "last_access": row["last_access"].isoformat() if row["last_access"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })

    return {
        "cove_mode": "multi",
        "cove_name": COVE_NAME,
        "total": len(presences),
        "presences": presences,
    }


@router.patch("/api/presence/{presence_id}/role")
async def set_presence_role(presence_id: str, request: Request):
    """Set a Presence's cove_role (admin | member | guest). Admin-only.

    Tier follows the role so the MC matches (operator -> cove, else presence).
    This is the 'who is an operator' control on the admin (steward) MC.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Not in multi-Presence mode")
    actor = await get_current_presence(request)
    if not actor or actor.get("cove_role") != "admin":
        raise HTTPException(403, "Operators only.")
    body = await request.json()
    role = (body.get("cove_role") or "").strip()
    if role not in ("admin", "member", "guest"):
        raise HTTPException(400, "cove_role must be admin, member, or guest")
    tier = "cove" if role == "operator" else "presence"
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                "UPDATE accounts SET cove_role = %s, tier = %s WHERE id = %s",
                (role, tier, uuid.UUID(presence_id)),
            )
    except Exception as e:
        log.error("set_presence_role failed: %s", e)
        raise HTTPException(500, "Something went wrong. Please try again.")
    return {"ok": True, "presence_id": presence_id, "cove_role": role}


@router.post("/api/presence/{presence_id}/regenerate-link")
async def regenerate_link(presence_id: str, request: Request):
    """Generate a new magic link for a Presence.

    Creates a new session — does NOT invalidate existing sessions.
    Old sessions remain valid until they expire (90 days).
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Not in multi-Presence mode")

    # TODO: Operator auth check
    raw_token = secrets.token_urlsafe(32)
    hashed_token = _hash_token(raw_token)

    handle = ""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Update auth_token on accounts (for backward compat / initial link)
            await conn.execute(
                "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
                (hashed_token, uuid.UUID(presence_id))
            )
            # Create a session for the new token. Label it a PLACEHOLDER ("pending") — the
            # link hasn't been opened on any device yet, so we don't know what it is. The
            # real device is stamped on first open (signin_link_auth), and the list renders
            # this as "Not opened yet" until then.
            await _create_session(conn, uuid.UUID(presence_id), hashed_token, "pending")
            # Fetch the handle so the link can ride the operator's subdomain when routing is live
            row = await (await conn.execute(
                "SELECT username FROM accounts WHERE id = %s", (uuid.UUID(presence_id),)
            )).fetchone()
            if row:
                handle = (row["username"] or "").strip()  # dict_row → by name, not row[0]
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")

    # Mirror create-presence: prefer the operator's subdomain when the Cove has
    # wildcard routing live, else the Cove domain root, else the live request host.
    # Never bake localhost when a real domain exists (191 finding).
    from src.config import load_cove_config  # local import — module has no top-level one
    _cove_domain = (load_cove_config().get("domain") or "").strip()
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if load_cove_config().get("subdomain_routing") and _cove_domain and handle:
        signin_link = f"{scheme}://{handle}.{_cove_domain}/p/{raw_token}"
    elif _cove_domain:
        signin_link = f"https://{_cove_domain}/p/{raw_token}"
    else:
        host = request.headers.get("host", "localhost")
        signin_link = f"{scheme}://{host}/p/{raw_token}"

    return {"signin_link": signin_link, "token": raw_token}


async def mint_signin_door(account_id, domain: str, scheme: str = "https") -> str:
    """Mint a rotation-proof, session-backed magic-link door for `account_id` at `domain`
    and return the /p/{token} URL (empty string if `domain` is blank).

    Same machinery as regenerate_link: a fresh token, its accounts.auth_token row, AND a
    matching 'pending' auth_sessions row — so the FIRST click validates. Creating the session
    row locally is exactly what dodges the T3 first-click 401 that a bare *stamped* token hits.
    Targets the Cove ROOT (not the operator subdomain): the /p handler shares the session
    cookie across the Cove's subdomains, the root is the address the operator just claimed, and
    it resolves before wildcard DNS necessarily has. Used by the domain-claim done-card so a
    brand-new self-host operator can cross from localhost into their live Cove in one click."""
    dom = (domain or "").strip()
    if not dom:
        return ""
    raw_token = secrets.token_urlsafe(32)
    hashed_token = _hash_token(raw_token)
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
            (hashed_token, uuid.UUID(str(account_id))))
        await _create_session(conn, uuid.UUID(str(account_id)), hashed_token, "pending")
    # Always /p/{token} — bare /{token} never hits this handler (Woods/Roos door crash).
    door = f"{scheme}://{dom}/p/{raw_token}"
    if "/p/" not in door:
        return ""
    return door


@router.get("/api/presence/sessions")
async def list_my_sessions(request: Request):
    """List the current presence's active sign-in sessions (one per signed-in device).
    The session matching this request's cookie is flagged `current`."""
    if COVE_MODE != "multi":
        return {"sessions": []}
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return {"sessions": []}
    cur_token = request.cookies.get(COOKIE_NAME)
    cur_hash = _hash_token(cur_token) if cur_token else ""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            rows = await (await conn.execute(
                """SELECT id, device_label, last_used, created_at, token_hash
                   FROM auth_sessions
                   WHERE account_id = %s AND active = TRUE
                   ORDER BY last_used DESC NULLS LAST""",
                (p["id"],)
            )).fetchall()
    except Exception:
        raise HTTPException(500, "Could not load sessions.")
    # Dedupe by token_hash: one credential = one row. The migration created several rows
    # carrying the same hash, which made MULTIPLE rows flag as "this device" (all match the
    # current cookie) and none could be signed out. Collapse them, OR-ing `current` and
    # preferring a real device label over a placeholder. Rows are last_used-DESC, so dict
    # insertion order keeps the list newest-first. (get_db = dict_row → index by name.)
    by_hash = {}
    for r in rows:
        th = r["token_hash"]
        is_current = (th == cur_hash)
        existing = by_hash.get(th)
        if existing is None:
            by_hash[th] = {
                "id": str(r["id"]), "device_label": r["device_label"],
                "last_used": r["last_used"], "created_at": r["created_at"],
                "current": is_current,
            }
        else:
            existing["current"] = existing["current"] or is_current
            if _is_placeholder_label(existing["device_label"]) and not _is_placeholder_label(r["device_label"]):
                existing["device_label"] = r["device_label"]
                existing["id"] = str(r["id"])
    out = []
    for e in by_hash.values():
        out.append({
            "id": e["id"],
            # A placeholder (un-opened link / migrated row / unparsed UA) reads as a plain
            # "Not opened yet" rather than an internal source word like "regenerated".
            "device_label": ("Not opened yet" if _is_placeholder_label(e["device_label"]) else e["device_label"]),
            "last_used": e["last_used"].isoformat() if e["last_used"] else None,
            "created_at": e["created_at"].isoformat() if e["created_at"] else None,
            "current": e["current"],
        })
    return {"sessions": out}


@router.post("/api/presence/sessions/revoke")
async def revoke_my_session(request: Request):
    """Sign out one of the current presence's other devices. Refuses to revoke THIS
    device (use Sign out for that). Scoped to the caller's own account only."""
    if COVE_MODE != "multi":
        raise HTTPException(400, "Not in multi-Presence mode")
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    sid = (body.get("id") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "No session id."}, status_code=200)
    try:
        sid_uuid = uuid.UUID(sid)
    except Exception:
        return JSONResponse({"ok": False, "error": "Bad session id."}, status_code=200)
    cur_token = request.cookies.get(COOKIE_NAME)
    cur_hash = _hash_token(cur_token) if cur_token else ""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            row = await (await conn.execute(
                "SELECT token_hash FROM auth_sessions WHERE id = %s AND account_id = %s",
                (sid_uuid, p["id"]),
            )).fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "Session not found."}, status_code=200)
            if row["token_hash"] == cur_hash:  # dict_row → by name, not row[0]
                return JSONResponse({"ok": False, "error": "That's this device — use Sign out instead."}, status_code=200)
            await conn.execute(
                "UPDATE auth_sessions SET active = FALSE WHERE id = %s AND account_id = %s",
                (sid_uuid, p["id"]),
            )
    except Exception:
        raise HTTPException(500, "Could not revoke that session.")
    return {"ok": True}
