"""
Self-service account routes — signup and signin for the shared container.

When COVE_MODE=multi, provides:
  - POST /api/account/create  — Free-tier self-signup
  - POST /api/account/signin  — Email-based magic link signin

These endpoints are called by landing.html's signup/signin forms.
Magic link emails sent via Brevo transactional API (see email.py).
"""

import hmac
import os
from src.env import env
import json
import uuid
import hashlib
import secrets
import random
import string
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")
COVE_NAME = env("COVE_NAME")
UPGRADE_SECRET = env("SHARED_CONTAINER_SECRET")


def _hash_token(token: str) -> str:
    """Hash a magic link token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _generate_referral_code(conn) -> str:
    """Generate a unique referral code like LP-A3KN9F.

    Format: LP- + 6 alphanumeric chars from a safe alphabet
    (no 0/O/1/I/L to avoid confusion when shared verbally).
    Pool: 29^6 = ~594 million combinations.
    """
    safe_chars = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 29 chars, no 0/O/1/I/L
    for _ in range(50):
        suffix = "".join(random.choices(safe_chars, k=6))
        code = f"LP-{suffix}"
        result = await conn.execute(
            "SELECT 1 FROM accounts WHERE referral_code = %s", (code,)
        )
        if not await result.fetchone():
            return code
    # Extremely unlikely fallback: 8 chars
    suffix = "".join(random.choices(safe_chars, k=8))
    return f"LP-{suffix}"


# =============================================================================
# Referral Bounce — cross-domain cookie setter
# =============================================================================

ALLOWED_REDIRECT_DOMAINS = {
    "lucidtuner.com", "www.lucidtuner.com",
    "lucidcove.org", "www.lucidcove.org", "app.lucidcove.org",
    "lucidprinciples.com", "www.lucidprinciples.com",
}
DEFAULT_REDIRECT = "https://lucidtuner.com"


@router.get("/r/{code}")
async def referral_bounce(code: str, to: Optional[str] = None):
    """Set a 90-day referral cookie on app.lucidcove.org, then redirect.

    Usage: app.lucidcove.org/r/LP-XXXXXX
           app.lucidcove.org/r/LP-XXXXXX?to=https://lucidprinciples.com

    Any site can detect ?ref= and bounce through this URL to set the cookie
    on the domain where signup actually happens. The redirect is instant.
    """
    # Validate redirect target to prevent open redirect
    redirect_url = DEFAULT_REDIRECT
    if to:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(to)
            if parsed.hostname in ALLOWED_REDIRECT_DOMAINS:
                redirect_url = to
        except Exception:
            pass

    # Append ?ref= to redirect URL so destination page can capture the code
    # (cookie is domain-locked to app.lucidcove.org, won't be readable on other sites)
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
    parsed_redir = urlparse(redirect_url)
    existing_params = parse_qs(parsed_redir.query)
    existing_params['ref'] = [code.upper()]
    new_query = urlencode(existing_params, doseq=True)
    redirect_url = urlunparse(parsed_redir._replace(query=new_query))

    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key="lp_ref",
        value=code.upper(),
        max_age=90 * 86400,
        path="/",
        httponly=False,
        samesite="lax",
    )
    return response


# =============================================================================
# Self-Service Signup
# =============================================================================

@router.post("/api/account/create")
async def create_account(request: Request):
    """Create a free-tier account. No operator auth required.

    Body: {
        "email": "user@example.com",
        "username": "handle",
        "display_name": "Display Name",  // optional
        "referred_by": "affiliate-code"  // optional, future use
    }

    Returns: { "signin_link": "https://..." } for immediate signin.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Account creation not available in single mode")

    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    username = (body.get("username") or "").strip().lower()
    display_name = (body.get("display_name") or "").strip()
    referred_by = (body.get("referred_by") or "").strip()
    signup_source = (body.get("source") or "direct").strip()
    frequency_choice = (body.get("frequency_choice") or "").strip()

    if not email:
        raise HTTPException(400, "Email is required")
    if not username:
        raise HTTPException(400, "Username is required")

    # Basic validation
    if "@" not in email or "." not in email:
        raise HTTPException(400, "Invalid email address")
    if len(username) < 2 or len(username) > 30:
        raise HTTPException(400, "Username must be 2-30 characters")
    if not username.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "Username can only contain letters, numbers, hyphens, underscores")

    presence_id = uuid.uuid4()
    raw_token = secrets.token_urlsafe(32)
    hashed_token = _hash_token(raw_token)

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Check for duplicate email
            result = await conn.execute(
                "SELECT id FROM accounts WHERE email = %s", (email,)
            )
            if await result.fetchone():
                raise HTTPException(409, "An account with this email already exists. Try signing in instead.")

            # Check for duplicate username
            result = await conn.execute(
                "SELECT id FROM accounts WHERE username = %s", (username,)
            )
            if await result.fetchone():
                raise HTTPException(409, "This username is taken. Try another one.")

            # Resolve referral code of referring account
            referrer_id = None
            if referred_by:
                ref_result = await conn.execute(
                    "SELECT id FROM accounts WHERE referral_code = %s AND active = TRUE",
                    (referred_by.upper(),)
                )
                ref_row = await ref_result.fetchone()
                if ref_row:
                    referrer_id = ref_row["id"]

            # Generate unique referral code for this new account
            referral_code = await _generate_referral_code(conn)

            # Create the account
            await conn.execute(
                """INSERT INTO accounts (id, display_name, username, email, agent_name,
                                          last_name, tier, cove_role, auth_token, active,
                                          referred_by, referral_code)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)""",
                (presence_id,
                 display_name or username,
                 username,
                 email,
                 "",
                 COVE_NAME,
                 "free",
                 "member",
                 hashed_token,
                 referrer_id,
                 referral_code)
            )
            # Create a session for the initial token
            from src.dashboard.routes.presence import _create_session
            await _create_session(conn, presence_id, hashed_token, "signup")

            # Affiliate edge (#169): reserve this handle in the Haven registry and record
            # who recruited them — the top of the funnel for the marketplace affiliate
            # program. `referred_by` is the captured referral code/handle; reserve_handle
            # resolves it → canonical handle, validates, and stores it set-once. Best-
            # effort so a registry hiccup never blocks signup.
            try:
                from src.dashboard.routes.registry import reserve_handle
                await reserve_handle(conn, username, referred_by=referred_by)
            except Exception as _reg_err:
                import logging
                logging.warning(f"registry handle reserve skipped for {username}: {_reg_err}")
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"Account creation failed: {e}")
        detail = "Something went wrong creating your account. Please try again."
        err_str = str(e).lower()
        if "duplicate key" in err_str or "unique" in err_str:
            if "email" in err_str:
                detail = "An account with this email already exists"
            elif "username" in err_str:
                detail = "This username is already taken"
        raise HTTPException(500, detail)

    # Register the new account as a marketplace member (#128), fire-and-forget so a
    # marketplace outage never blocks signup. Every Operator becomes a customers row
    # (keyed by external_id = stable account id) → can buy/sell from day one.
    try:
        market_url = env("MARKETPLACE_API_URL")
        if market_url and UPGRADE_SECRET:
            import httpx
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(
                    market_url.rstrip("/") + "/api/marketplace/member",
                    headers={"X-Shared-Secret": UPGRADE_SECRET},
                    json={"external_id": str(presence_id), "email": email,
                          "name": display_name or username},
                )
    except Exception as e:
        import logging
        logging.warning(f"Marketplace member registration skipped: {e}")

    # Log signup source event
    try:
        from datetime import datetime, timezone as tz
        now = datetime.now(tz.utc)
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO tuning_events (presence_id, event_type, event_data, date, time)
                   VALUES (%s, 'signup', %s, %s, %s)""",
                (presence_id,
                 json.dumps({"source": signup_source, "referred_by": referred_by or None, "frequency_choice": frequency_choice or None}),
                 now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"))
            )
    except Exception:
        pass  # Never block signup on tracking failure

    # Build magic link
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host", "localhost")
    signin_link = f"{scheme}://{host}/p/{raw_token}"

    # Send magic link via email + add to Brevo contact list
    from src.dashboard.routes.email import send_signin_link, add_to_brevo_list
    email_sent = await send_signin_link(email, signin_link, is_signup=True)
    await add_to_brevo_list(email, display_name or username)

    resp = {
        "ok": True,
        "presence_id": str(presence_id),
        "display_name": display_name or username,
        "email_sent": email_sent,
    }
    if not email_sent:
        resp["signin_link"] = signin_link
    return resp


# =============================================================================
# Email-based Signin
# =============================================================================

@router.post("/api/account/signin")
async def signin(request: Request):
    """Look up account by email and return a magic link.

    Body: { "email": "user@example.com" }

    Until email sending is wired, returns signin_link directly.
    Future: send link via email, return success message only.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Signin not available in single mode")

    body = await request.json()
    email = (body.get("email") or "").strip().lower()

    if not email:
        raise HTTPException(400, "Email is required")

    try:
        from src.memory.database import get_db
        from src.dashboard.routes.presence import _create_session
        async with get_db() as conn:
            # Find the Presence by email
            result = await conn.execute(
                "SELECT id, display_name, active, username FROM accounts WHERE email = %s",
                (email,)
            )
            row = await result.fetchone()
            if not row:
                raise HTTPException(404, "No account found with this email. Create one first.")

            if not row["active"]:
                raise HTTPException(403, "This account has been deactivated. Contact the Cove operator.")

            # Generate a new token — does NOT invalidate existing sessions
            raw_token = secrets.token_urlsafe(32)
            hashed_token = _hash_token(raw_token)

            # Update auth_token on accounts (for backward compat)
            await conn.execute(
                "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
                (hashed_token, row["id"])
            )
            # Create a session for the new token
            await _create_session(conn, row["id"], hashed_token, "signin")
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"Signin failed: {e}")
        raise HTTPException(500, "Something went wrong. Please try again.")

    # Build the sign-in link. On a Cove with wildcard routing, land the operator on
    # THEIR OWN subdomain ({handle}.{domain}) — consistent with everyone, including the
    # founder (no special root path). Falls back to the request host (the shared app,
    # or a Cove with no domain yet). Mirrors presence.py.
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    _handle = (row.get("username") or "").lstrip("@").strip().lower()
    try:
        from src.config import load_cove_config as _lcc
        _cove = _lcc()
        _cdom = (_cove.get("domain") or "").strip()
    except Exception:
        _cove, _cdom = {}, ""
    if _cove.get("subdomain_routing") and _cdom and _handle:
        signin_link = f"{scheme}://{_handle}.{_cdom}/p/{raw_token}"
    elif _cdom:
        signin_link = f"https://{_cdom}/p/{raw_token}"
    else:
        host = request.headers.get("host", "localhost")
        signin_link = f"{scheme}://{host}/p/{raw_token}"

    # Send magic link via email
    from src.dashboard.routes.email import send_signin_link
    email_sent = await send_signin_link(email, signin_link, is_signup=False)

    resp = {
        "ok": True,
        "email_sent": email_sent,
    }
    # If email wasn't sent, return magic link directly (P620 Coves, dev mode)
    if not email_sent:
        resp["signin_link"] = signin_link
    return resp


@router.post("/api/account/connect-key")
async def connect_key(request: Request):
    """#200 / Path B (#4): a signed-in operator retrieves a 'connect key' (their operator
    token) to connect a self-hosted Cove to their existing handle. Rotates the account's
    auth_token and returns the new raw token ONCE. Requires a valid session.
    NOTE: rotation invalidates any previous key — fine for the upgrade case (no Cove yet)."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        raise HTTPException(401, "Sign in first to get your connect key.")
    import secrets as _secrets, hashlib
    raw = _secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    from src.memory.database import get_db
    async with get_db() as conn:
        await conn.execute(
            "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
            (h, str(p["id"])))
    return {"ok": True, "handle": p.get("username"), "connect_key": raw}


# =============================================================================
# Tier Upgrade (called by Socrates webhook after Stripe payment)
# =============================================================================

VALID_TIERS = {"free", "pro", "operator", "presence", "cove"}

@router.post("/api/account/upgrade")
async def upgrade_account(request: Request):
    """Upgrade an account's tier after successful payment.

    Called by Socrates commerce webhook. Protected by shared secret.

    Body: {
        "secret": "shared-secret",
        "email": "user@example.com",
        "tier": "pro"
    }
    """
    body = await request.json()
    secret = (body.get("secret") or "").strip()
    email = (body.get("email") or "").strip().lower()
    new_tier = (body.get("tier") or "").strip().lower()

    if not UPGRADE_SECRET or not hmac.compare_digest(secret, UPGRADE_SECRET):
        raise HTTPException(403, "Invalid secret")

    if not email:
        raise HTTPException(400, "Email is required")
    if new_tier not in VALID_TIERS:
        raise HTTPException(400, f"Invalid tier: {new_tier}")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT id, tier FROM accounts WHERE email = %s AND active = TRUE",
                (email,)
            )
            row = await result.fetchone()
            if not row:
                raise HTTPException(404, "No active account found with this email")

            old_tier = row["tier"]
            row_id = str(row["id"])
            await conn.execute(
                "UPDATE accounts SET tier = %s, updated_at = NOW() WHERE id = %s",
                (new_tier, row_id)
            )

        # Auto-provision Nextcloud OUTSIDE the DB context to avoid row-lock deadlock
        if new_tier in ("operator", "presence", "cove") and old_tier in ("free", "pro"):
            try:
                from src.dashboard.routes.nextcloud import provision_nc_user
                nc_result = await provision_nc_user(
                    row_id, email.split("@")[0], tier=new_tier,
                )
                if not nc_result.get("ok"):
                    import logging
                    logging.getLogger("account").warning(
                        "NC provision failed for %s: %s", email, nc_result.get("error", "unknown")
                    )
            except Exception as nc_err:
                import logging
                logging.getLogger("account").warning("NC provision error for %s: %s", email, nc_err)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")

    return {"ok": True, "email": email, "old_tier": old_tier, "new_tier": new_tier}


# =============================================================================
# Referral Code Lookup (for checkout flow — frontend needs the ref code)
# =============================================================================

@router.get("/api/account/referral-code")
async def get_referral_code(request: Request):
    """Get the referral code of whoever referred the current user.

    Used by the upgrade flow to pass the referral attribution to Stripe checkout.
    Returns the referrer's code (not the current user's code).
    """
    if COVE_MODE != "multi":
        return {"ref": None}

    from src.dashboard.routes.presence import get_current_presence
    presence = await get_current_presence(request)
    if not presence:
        return {"ref": None}

    referred_by = presence.get("referred_by")
    if not referred_by:
        return {"ref": None}

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT referral_code FROM accounts WHERE id = %s",
                (referred_by,)
            )
            row = await result.fetchone()
            if row and row["referral_code"]:
                return {"ref": row["referral_code"]}
    except Exception:
        pass

    return {"ref": None}


# =============================================================================
# Affiliate Reporting (for Reports → Affiliates tab)
# =============================================================================

@router.get("/api/account/affiliates")
async def get_affiliates(request: Request):
    """Get affiliate stats for the current user.

    Returns the user's referral code, all accounts referred by them,
    and basic stats (total signups, upgraded count).

    Auth modes:
      - Multi mode (shared container): session-based presence auth
      - Multi mode + X-Shared-Secret header + ?email=: inter-service auth (Stuart/Atlas)
      - Single mode: proxies to SHARED_CONTAINER_URL with secret
    """
    if COVE_MODE != "multi":
        # Single-operator mode — proxy to shared container for live stats
        shared_url = env("SHARED_CONTAINER_URL")
        shared_secret = env("SHARED_CONTAINER_SECRET")
        operator_account_id = env("OPERATOR_ACCOUNT_ID")
        if not shared_url or not shared_secret or not operator_account_id:
            return {"referrals": [], "stats": {}, "error": "Shared container not configured"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{shared_url}/api/account/affiliates",
                    params={"account_id": operator_account_id},
                    headers={"X-Shared-Secret": shared_secret},
                )
                return resp.json()
        except Exception:
            return {"referrals": [], "stats": {}}

    # Inter-service auth: secret + account_id param (from Stuart/Atlas)
    secret_header = request.headers.get("X-Shared-Secret", "")
    account_id_param = request.query_params.get("account_id", "")
    if secret_header and account_id_param and UPGRADE_SECRET and hmac.compare_digest(secret_header, UPGRADE_SECRET):
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                acct = await conn.execute(
                    "SELECT id, referral_code FROM accounts WHERE id = %s AND active = TRUE",
                    (account_id_param,)
                )
                acct_row = await acct.fetchone()
                if not acct_row:
                    return {"referrals": [], "stats": {}}
                my_id = acct_row["id"]
                my_code = acct_row["referral_code"]
                return await _get_affiliate_data(conn, my_id, my_code)
        except Exception:
            return {"referrals": [], "stats": {}}

    # Session-based auth (normal multi-user login)
    from src.dashboard.routes.presence import get_current_presence
    presence = await get_current_presence(request)
    if not presence:
        return {"referrals": [], "stats": {}}

    my_id = presence.get("id")
    my_code = presence.get("referral_code")
    if not my_id:
        return {"referrals": [], "stats": {}}

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            return await _get_affiliate_data(conn, my_id, my_code)
    except Exception:
        return {"referrals": [], "stats": {}}


async def _get_affiliate_data(conn, account_id: str, referral_code: str):
    """Shared helper — query affiliate stats for a given account."""
    result = await conn.execute(
        """SELECT display_name, username, email, tier, created_at
           FROM accounts
           WHERE referred_by = %s AND active = TRUE
           ORDER BY created_at DESC""",
        (account_id,)
    )
    rows = await result.fetchall()

    referrals = []
    upgraded_count = 0
    for r in rows:
        is_paid = r["tier"] not in ("free", None)
        if is_paid:
            upgraded_count += 1
        referrals.append({
            "display_name": r["display_name"],
            "username": r["username"],
            "email": r["email"],
            "tier": r["tier"],
            "is_paid": is_paid,
            "joined": r["created_at"].isoformat() if r["created_at"] else None,
        })

    # L2 referrals (people referred by YOUR referrals)
    l1_ids = []
    if rows:
        l1_result = await conn.execute(
            """SELECT id FROM accounts WHERE referred_by = %s AND active = TRUE""",
            (account_id,)
        )
        l1_ids = [r["id"] for r in await l1_result.fetchall()]

    l2_count = 0
    if l1_ids:
        placeholders = ",".join(["%s"] * len(l1_ids))
        l2_result = await conn.execute(
            f"SELECT COUNT(*) as cnt FROM accounts WHERE referred_by IN ({placeholders}) AND active = TRUE",
            tuple(l1_ids),
        )
        l2_row = await l2_result.fetchone()
        l2_count = l2_row["cnt"] if l2_row else 0

    return {
        "referral_code": referral_code,
        "referrals": referrals,
        "stats": {
            "total_signups": len(referrals),
            "upgraded": upgraded_count,
            "l2_referrals": l2_count,
        },
    }


# =============================================================================
# Referral Lookup (called by Socrates webhook to resolve referral codes)
# =============================================================================

@router.get("/api/account/lookup-referral")
async def lookup_referral(code: str = "", email: str = "", secret: str = ""):
    """Look up an account by referral code or email. Protected by shared secret.

    Used by Socrates commerce webhook to:
    - Resolve referral codes into account info for commission attribution
    - Look up a paying user's referral code by their email

    Returns: { "found": true, "email": "...", "account_id": "...", "referral_code": "..." }
    """
    if not UPGRADE_SECRET or not hmac.compare_digest(secret, UPGRADE_SECRET):
        raise HTTPException(403, "Invalid secret")

    if not code and not email:
        return {"found": False}

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            if code:
                result = await conn.execute(
                    """SELECT id, email, display_name, username, referral_code
                       FROM accounts WHERE referral_code = %s AND active = TRUE""",
                    (code.upper(),)
                )
            else:
                result = await conn.execute(
                    """SELECT id, email, display_name, username, referral_code
                       FROM accounts WHERE email = %s AND active = TRUE""",
                    (email.lower(),)
                )
            row = await result.fetchone()
            if row:
                return {
                    "found": True,
                    "account_id": str(row["id"]),
                    "email": row["email"],
                    "display_name": row["display_name"],
                    "username": row["username"],
                    "referral_code": row["referral_code"],
                }
    except Exception:
        pass

    return {"found": False}


# =============================================================================
# Admin API — Haven MC proxy endpoints (protected by shared secret)
# =============================================================================

@router.get("/api/admin/accounts")
async def admin_list_accounts(request: Request, secret: str = ""):
    """List all accounts with key fields for Haven MC admin dashboard."""
    if not UPGRADE_SECRET or not hmac.compare_digest(secret, UPGRADE_SECRET):
        raise HTTPException(403, "Invalid secret")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, display_name, username, email, tier,
                          referral_code, referred_by, stripe_customer_id,
                          active, created_at, last_access, updated_at
                   FROM accounts ORDER BY created_at"""
            )
            rows = await result.fetchall()
            accounts = []
            for r in rows:
                accounts.append({
                    "id": str(r["id"]),
                    "display_name": r["display_name"],
                    "username": r["username"],
                    "email": r["email"],
                    "tier": r["tier"],
                    "referral_code": r.get("referral_code"),
                    "referred_by": str(r["referred_by"]) if r["referred_by"] else None,
                    "stripe_customer_id": r["stripe_customer_id"],
                    "active": r["active"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "last_access": r["last_access"].isoformat() if r["last_access"] else None,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                })
            return {"accounts": accounts, "count": len(accounts)}
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")


@router.patch("/api/admin/accounts/{account_id}/tier")
async def admin_update_tier(account_id: str, request: Request):
    """Change an account's tier directly (bypasses Stripe)."""
    body = await request.json()
    secret = (body.get("secret") or "").strip()
    new_tier = (body.get("tier") or "").strip().lower()

    if not UPGRADE_SECRET or not hmac.compare_digest(secret, UPGRADE_SECRET):
        raise HTTPException(403, "Invalid secret")
    if new_tier not in VALID_TIERS:
        raise HTTPException(400, f"Invalid tier: {new_tier}")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT id, email, tier FROM accounts WHERE id = %s",
                (account_id,)
            )
            row = await result.fetchone()
            if not row:
                raise HTTPException(404, "Account not found")

            old_tier = row["tier"]
            await conn.execute(
                "UPDATE accounts SET tier = %s, updated_at = NOW() WHERE id = %s",
                (new_tier, account_id)
            )
            # Capture row data before closing connection
            row_email = row["email"]
            row_id = str(row["id"])

        # Auto-provision Nextcloud OUTSIDE the DB context to avoid row-lock deadlock
        nc_info = None
        if new_tier in ("operator", "presence", "cove") and old_tier in ("free", "pro"):
            try:
                from src.dashboard.routes.nextcloud import provision_nc_user
                display = row_email.split("@")[0] if row_email else "user"
                nc_info = await provision_nc_user(row_id, display, tier=new_tier)
            except Exception as nc_err:
                nc_info = {"ok": False, "error": str(nc_err)}

        return {
            "ok": True,
            "id": account_id,
            "email": row_email,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "nextcloud": nc_info,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")


@router.get("/api/admin/stats")
async def admin_stats(request: Request, secret: str = ""):
    """System stats for Haven MC dashboard — account counts, session counts."""
    if not UPGRADE_SECRET or not hmac.compare_digest(secret, UPGRADE_SECRET):
        raise HTTPException(403, "Invalid secret")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Account counts by tier
            result = await conn.execute(
                "SELECT tier, COUNT(*) as count FROM accounts WHERE active = TRUE GROUP BY tier ORDER BY tier"
            )
            tier_counts = {r["tier"]: r["count"] for r in await result.fetchall()}

            # Total accounts
            result = await conn.execute("SELECT COUNT(*) as total FROM accounts WHERE active = TRUE")
            total = (await result.fetchone())["total"]

            # Session count
            session_count = 0
            try:
                result = await conn.execute("SELECT COUNT(*) as total FROM tuning_sessions")
                session_count = (await result.fetchone())["total"]
            except Exception:
                pass

            # Contact messages (unread)
            msg_count = 0
            try:
                result = await conn.execute(
                    "SELECT COUNT(*) as total FROM contact_messages WHERE archived = FALSE"
                )
                msg_count = (await result.fetchone())["total"]
            except Exception:
                pass

            return {
                "total_accounts": total,
                "tier_counts": tier_counts,
                "tuning_sessions": session_count,
                "unread_messages": msg_count,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")


@router.get("/api/admin/activity")
async def admin_activity(request: Request, secret: str = "", limit: int = 50, user: str = ""):
    """Recent activity feed for Haven MC dashboard.

    Returns recent events (logins, listens, mirror clicks, etc.) with user info,
    plus aggregate stats for today/week. Optional `user` param filters by username.
    """
    if not UPGRADE_SECRET or not hmac.compare_digest(secret, UPGRADE_SECRET):
        raise HTTPException(403, "Invalid secret")

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Recent events with account info (last N events)
            if user:
                result = await conn.execute(
                    """SELECT te.event_type, te.echo_name, te.echo_album,
                              te.frequency, te.play_source, te.play_duration,
                              te.principle, te.signal_type, te.event_data,
                              te.position_in_playlist, te.context,
                              te.timestamp, te.date, te.time,
                              a.display_name, a.username, a.tier
                       FROM tuning_events te
                       LEFT JOIN accounts a ON te.presence_id = a.id
                       WHERE a.username = %s
                       ORDER BY te.timestamp DESC
                       LIMIT %s""",
                    (user, limit)
                )
            else:
                result = await conn.execute(
                    """SELECT te.event_type, te.echo_name, te.echo_album,
                              te.frequency, te.play_source, te.play_duration,
                              te.principle, te.signal_type, te.event_data,
                              te.position_in_playlist, te.context,
                              te.timestamp, te.date, te.time,
                              a.display_name, a.username, a.tier
                       FROM tuning_events te
                       LEFT JOIN accounts a ON te.presence_id = a.id
                       ORDER BY te.timestamp DESC
                       LIMIT %s""",
                    (limit,)
                )
            recent = []
            for r in await result.fetchall():
                evt = {
                    "event_type": r["event_type"],
                    "echo_name": r["echo_name"] or "",
                    "echo_album": r["echo_album"] or "",
                    "frequency": r["frequency"] or "",
                    "play_source": r["play_source"] or "",
                    "play_duration": r["play_duration"],
                    "principle": r["principle"] or "",
                    "timestamp": r["timestamp"].isoformat() if r["timestamp"] else "",
                    "display_name": r["display_name"] or r["username"] or "Unknown",
                    "username": r["username"] or "",
                    "tier": r["tier"] or "free",
                }
                # Include raw event_data for detail views
                if r["event_data"] and r["event_data"] != {}:
                    evt["event_data"] = r["event_data"]
                if r["position_in_playlist"] is not None:
                    evt["position"] = r["position_in_playlist"]
                recent.append(evt)

            # Today's aggregate stats
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            result = await conn.execute(
                """SELECT event_type, COUNT(*) as count
                   FROM tuning_events WHERE date = %s
                   GROUP BY event_type ORDER BY count DESC""",
                (today,)
            )
            today_stats = {r["event_type"]: r["count"] for r in await result.fetchall()}

            # Total play duration today
            result = await conn.execute(
                """SELECT COALESCE(SUM(play_duration), 0) as total_duration
                   FROM tuning_events
                   WHERE date = %s AND play_duration IS NOT NULL""",
                (today,)
            )
            total_duration = (await result.fetchone())["total_duration"]

            # Recently active users (last 7 days)
            result = await conn.execute(
                """SELECT a.display_name, a.username, a.tier, a.last_access,
                          COUNT(te.id) as event_count,
                          MAX(te.timestamp) as last_event
                   FROM accounts a
                   LEFT JOIN tuning_events te ON te.presence_id = a.id
                       AND te.timestamp > NOW() - INTERVAL '7 days'
                   WHERE a.active = TRUE
                   GROUP BY a.id, a.display_name, a.username, a.tier, a.last_access
                   ORDER BY last_event DESC NULLS LAST
                   LIMIT 20""",
            )
            active_users = []
            for r in await result.fetchall():
                active_users.append({
                    "display_name": r["display_name"] or r["username"] or "Unknown",
                    "username": r["username"] or "",
                    "tier": r["tier"] or "free",
                    "last_access": r["last_access"].isoformat() if r["last_access"] else "",
                    "event_count": r["event_count"],
                    "last_event": r["last_event"].isoformat() if r["last_event"] else "",
                })

            return {
                "recent": recent,
                "today_stats": today_stats,
                "today_listen_minutes": round(total_duration / 60, 1) if total_duration else 0,
                "active_users": active_users,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Something went wrong. Please try again.")


# =============================================================================
# Self-host operator token (#200) — the token a self-hoster needs so their own
# box joins the registry as their OWN @handle (X-Operator-Token / LP_OPERATOR_TOKEN).
# =============================================================================

@router.post("/api/account/self-host-token")
async def mint_self_host_token(request: Request):
    """Mint (rotate) the operator's self-host token and return it ONCE, with a
    ready-to-paste config bundle.

    The registry matches X-Operator-Token against accounts.auth_token (sha256). The
    raw token is never stored, so we mint a fresh one, store its hash, and show it a
    single time. SESSION-SAFE: live logins live in auth_sessions (the cookie), not
    auth_token, so rotating auth_token does NOT log the operator out (it only
    invalidates an unused magic link, which is fine). Self-scoped — acts on the
    logged-in account only.
    """
    if COVE_MODE != "multi":
        raise HTTPException(400, "Self-host tokens are issued from your app account.")
    from src.dashboard.routes.presence import get_current_presence
    presence = await get_current_presence(request)
    if not presence or not presence.get("id"):
        raise HTTPException(401, "Sign in first.")
    raw_token = secrets.token_urlsafe(32)
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
                (_hash_token(raw_token), str(presence["id"])),
            )
    except Exception:
        raise HTTPException(500, "Could not issue a token. Please try again.")
    handle = (presence.get("username") or "").lstrip("@")
    cove_name = (presence.get("last_name") or presence.get("display_name") or "").strip()
    registry_url = env("LP_REGISTRY_URL") or "https://app.lucidcove.org"
    return {
        "ok": True,
        "token": raw_token,            # shown ONCE — not retrievable again
        "handle": handle,
        "cove_name": cove_name,
        "registry_url": registry_url,
        # Two forms: .env for the running container, or operator.token for the cove.config
        # the self-host CLI consumes. Either makes the box register as @handle.
        "env_snippet": f"LP_OPERATOR_TOKEN={raw_token}\nLP_REGISTRY_URL={registry_url}",
        "config_snippet": f"operator:\n  handle: {handle}\n  token: {raw_token}",
        "note": (f"Save this now — it is shown only once. In the Cove setup wizard, paste it "
                 f"where it asks for your connect key and your box joins the network as "
                 f"@{handle}. (Hand setup: same key as LP_OPERATOR_TOKEN in .env or "
                 f"operator.token in cove.config.)"),
    }
