"""
YouTube OAuth2 authentication — token storage, refresh, and auth flow routes.

Handles:
  1. /api/youtube/auth           → Redirect operator to Google consent screen
  2. /api/youtube/oauth-callback → Receive auth code, exchange for tokens
  3. /api/youtube/status         → Check if we have valid tokens
  4. /api/youtube/revoke         → Revoke tokens (disconnect YouTube)

Tokens stored in DB (oauth_tokens table). Refresh handled automatically.

Environment variables (in .env):
  YOUTUBE_CLIENT_ID       — from Google Cloud Console
  YOUTUBE_CLIENT_SECRET   — from Google Cloud Console
  YOUTUBE_REDIRECT_URI    — must match Console exactly
"""

import os
from src.env import env
import time
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()

# Google OAuth2 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Scopes needed for YouTube upload + scheduling
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def _get_oauth_config() -> dict:
    """Read the YouTube OAuth *app* config (Cove-wide).

    The Google app (client id/secret) and redirect_uri are domain-level — one
    OAuth app per Cove — so they live in Cove feature flags (set via Posting
    Accounts), falling back to env. Per-presence-ness is the connected CHANNEL
    (its refresh token), keyed separately. Raises if the app isn't configured.
    """
    try:
        from src.config import get_feature_flags
        flags = get_feature_flags()
    except Exception:
        flags = {}
    client_id = flags.get("youtube_client_id") or env("YOUTUBE_CLIENT_ID")
    client_secret = flags.get("youtube_client_secret") or env("YOUTUBE_CLIENT_SECRET")
    redirect_uri = flags.get("youtube_redirect_uri") or env("YOUTUBE_REDIRECT_URI")

    if not all([client_id, client_secret, redirect_uri]):
        missing = []
        if not client_id:
            missing.append("YOUTUBE_CLIENT_ID")
        if not client_secret:
            missing.append("YOUTUBE_CLIENT_SECRET")
        if not redirect_uri:
            missing.append("YOUTUBE_REDIRECT_URI")
        raise ValueError(f"Missing YouTube OAuth env vars: {', '.join(missing)}")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


# =========================================================================
# Token storage (DB-backed)
# =========================================================================

async def _store_tokens(service: str, tokens: dict):
    """Store or update OAuth tokens in the database."""
    from src.memory.database import get_db

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO oauth_tokens (service, access_token, refresh_token, expires_at, scope, token_type)
            VALUES (%(service)s, %(access_token)s, %(refresh_token)s,
                    to_timestamp(%(expires_at)s), %(scope)s, %(token_type)s)
            ON CONFLICT (service) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = COALESCE(EXCLUDED.refresh_token, oauth_tokens.refresh_token),
                expires_at = EXCLUDED.expires_at,
                scope = EXCLUDED.scope,
                token_type = EXCLUDED.token_type,
                updated_at = NOW()
            """,
            {
                "service": service,
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "expires_at": tokens.get("expires_at", 0),
                "scope": tokens.get("scope", ""),
                "token_type": tokens.get("token_type", "Bearer"),
            },
        )


async def _get_tokens(service: str) -> dict | None:
    """Retrieve stored tokens for a service."""
    from src.memory.database import get_db

    async with get_db() as conn:
        result = await conn.execute(
            "SELECT * FROM oauth_tokens WHERE service = %s",
            (service,),
        )
        row = await result.fetchone()
        if row:
            return dict(row)
    return None


async def _delete_tokens(service: str):
    """Remove tokens for a service."""
    from src.memory.database import get_db

    async with get_db() as conn:
        await conn.execute("DELETE FROM oauth_tokens WHERE service = %s", (service,))


# =========================================================================
# Token refresh
# =========================================================================

async def get_valid_access_token(service: str = "youtube") -> str:
    """Get a valid access token, refreshing if expired.

    Returns the access token string.
    Raises ValueError if no tokens stored or refresh fails.
    """
    tokens = await _get_tokens(service)
    if not tokens:
        raise ValueError(f"No {service} tokens stored. Operator must authorize first.")

    # Check if expired (with 5-minute buffer)
    expires_at = tokens.get("expires_at")
    if expires_at:
        # expires_at is a datetime from DB
        if hasattr(expires_at, "timestamp"):
            expires_epoch = expires_at.timestamp()
        else:
            expires_epoch = float(expires_at)

        if time.time() < (expires_epoch - 300):
            # Still valid
            return tokens["access_token"]

    # Need to refresh
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise ValueError(f"Access token expired and no refresh token available. Re-authorize.")

    config = _get_oauth_config()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code != 200:
        raise ValueError(f"Token refresh failed ({resp.status_code}): {resp.text}")

    new_tokens = resp.json()
    new_tokens["expires_at"] = time.time() + new_tokens.get("expires_in", 3600)
    # Refresh tokens are NOT returned on refresh calls — keep the existing one
    new_tokens["refresh_token"] = refresh_token

    await _store_tokens(service, new_tokens)
    return new_tokens["access_token"]


# =========================================================================
# Routes
# =========================================================================

@router.get("/api/youtube/auth")
async def youtube_auth(request: Request):
    """Start OAuth2 flow — redirect the current presence to Google consent.

    The presence's account id rides through `state` so the callback stores the
    refresh token against THIS presence (per-presence channel), not globally.
    """
    try:
        config = _get_oauth_config()
    except ValueError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    from src.dashboard.routes.posting_identity import owner_id_from_request
    owner_id = await owner_id_from_request(request)

    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",  # Gets us a refresh token
        "prompt": "consent",       # Force consent to ensure refresh token
        "state": owner_id or "",   # carry the presence through the round-trip
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=auth_url)


@router.get("/api/youtube/oauth-callback")
async def youtube_oauth_callback(request: Request):
    """Receive OAuth2 callback from Google.

    Exchanges the auth code for access + refresh tokens,
    stores them in the DB, shows a success page.
    """
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    owner_id = request.query_params.get("state") or None  # the presence from /auth

    if error:
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><body style="font-family: system-ui; padding: 2rem; background: #1a1a2e; color: #eee;">
            <h2>YouTube Authorization Failed</h2>
            <p>Google returned an error: <strong>{error}</strong></p>
            <p>You can close this tab and try again from the Action Page.</p>
            </body></html>""",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content="""<!DOCTYPE html>
            <html><body style="font-family: system-ui; padding: 2rem; background: #1a1a2e; color: #eee;">
            <h2>Missing Authorization Code</h2>
            <p>No code received from Google. Try again from the Action Page.</p>
            </body></html>""",
            status_code=400,
        )

    # Exchange code for tokens
    try:
        config = _get_oauth_config()
    except ValueError as e:
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><body style="font-family: system-ui; padding: 2rem; background: #1a1a2e; color: #eee;">
            <h2>Configuration Error</h2>
            <p>{e}</p>
            </body></html>""",
            status_code=500,
        )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": config["redirect_uri"],
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><body style="font-family: system-ui; padding: 2rem; background: #1a1a2e; color: #eee;">
            <h2>Token Exchange Failed</h2>
            <p>Google returned status {resp.status_code}.</p>
            <p>You can close this tab and try again.</p>
            </body></html>""",
            status_code=400,
        )

    token_data = resp.json()
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)

    # Store tokens under the presence's namespaced service key (per-presence channel).
    from src.dashboard.routes.posting_identity import yt_service_key
    await _store_tokens(yt_service_key(owner_id), token_data)

    now = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    print(f"[{now}] [youtube] OAuth2 tokens stored successfully.")

    return HTMLResponse(
        content=f"""<!DOCTYPE html>
        <html><body style="font-family: system-ui; padding: 2rem; background: #1a1a2e; color: #eee; text-align: center;">
        <h2 style="color: #4CAF50;">YouTube Connected</h2>
        <p>Authorization successful. Tokens stored.</p>
        <p style="color: #888;">You can close this tab and return to the Action Page.</p>
        <p style="color: #666; font-size: 0.85rem;">Authorized at {now}</p>
        </body></html>"""
    )


@router.get("/api/youtube/status")
async def youtube_status(request: Request):
    """Check the current presence's YouTube authorization status.

    Returns whether this presence has tokens, expiry, and basic channel info.
    """
    from src.dashboard.routes.posting_identity import owner_id_from_request, yt_service_key
    owner_id = await owner_id_from_request(request)
    service = yt_service_key(owner_id)
    tokens = await _get_tokens(service)

    if not tokens:
        return {
            "authorized": False,
            "message": "No YouTube tokens stored for this presence. Connect to authorize.",
        }

    # Check expiration
    expires_at = tokens.get("expires_at")
    expired = True
    if expires_at:
        if hasattr(expires_at, "timestamp"):
            expires_epoch = expires_at.timestamp()
        else:
            expires_epoch = float(expires_at)
        expired = time.time() > expires_epoch

    has_refresh = bool(tokens.get("refresh_token"))

    result = {
        "authorized": True,
        "expired": expired,
        "has_refresh_token": has_refresh,
        "can_refresh": has_refresh,  # If we have a refresh token, we can auto-refresh
        "updated_at": tokens.get("updated_at").isoformat() if tokens.get("updated_at") else None,
    }

    # If not expired (or we can refresh), try to get channel info
    if not expired or has_refresh:
        try:
            access_token = await get_valid_access_token(service)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "snippet", "mine": "true"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    snippet = items[0].get("snippet", {})
                    result["channel"] = {
                        "title": snippet.get("title"),
                        "id": items[0].get("id"),
                    }
        except Exception as e:
            result["channel_error"] = str(e)

    return result


@router.post("/api/youtube/revoke")
async def youtube_revoke(request: Request):
    """Revoke the current presence's YouTube tokens and remove from database."""
    from src.dashboard.routes.posting_identity import owner_id_from_request, yt_service_key
    owner_id = await owner_id_from_request(request)
    service = yt_service_key(owner_id)
    tokens = await _get_tokens(service)

    if not tokens:
        return {"status": "ok", "message": "No tokens to revoke."}

    # Try to revoke with Google
    access_token = tokens.get("access_token")
    if access_token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    GOOGLE_REVOKE_URL,
                    params={"token": access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception:
            pass  # Best effort — delete from DB regardless

    await _delete_tokens(service)
    print(f"[youtube] Tokens revoked and removed for {service}.")

    return {"status": "ok", "message": "YouTube tokens revoked."}
