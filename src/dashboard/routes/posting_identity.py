"""
Per-presence posting identity + credential resolution.

Posting accounts (X, YouTube) belong to a Presence, not the Cove. This module
is the single place that resolves "whose account" and "which credentials" for
both contexts:

  - Request context  (manual post / status / save): the current presence from
    the auth cookie.
  - Scheduler context (auto-post): the owning presence recorded on the
    social_queue row (agent_id = accounts.id).

Storage:
  - X keys (4)                 → accounts.preferences.posting.x.*  (per-presence)
  - YouTube refresh token      → oauth_tokens, service='youtube:{owner_id}'
                                 (per-presence, namespaced — no schema change)
  - YouTube client creds       → Cove-wide feature flags (redirect_uri is
                                 domain-level: one Google app per Cove) + env.

Everything falls back to env vars when nothing per-presence is set, so a
single-presence or freshly-migrated Cove keeps working unchanged.
"""

import logging

from src.env import env

logger = logging.getLogger(__name__)

X_ENV_KEYS = {
    "api_key": "X_API_KEY",
    "api_secret": "X_API_SECRET",
    "access_token": "X_ACCESS_TOKEN",
    "access_token_secret": "X_ACCESS_TOKEN_SECRET",
}


async def owner_id_from_request(request) -> str | None:
    """The current presence's account id, or None (single mode / not signed in)."""
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        return p["id"] if p else None
    except Exception:
        return None


async def _account_prefs(owner_id: str) -> dict:
    """Read an account's preferences JSON by account id."""
    if not owner_id:
        return {}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT preferences FROM accounts WHERE id = %s", (owner_id,)
            )
            row = await r.fetchone()
        if not row:
            return {}
        prefs = row["preferences"] if isinstance(row, dict) else row[0]
        return prefs or {}
    except Exception as e:
        logger.warning(f"posting: could not read prefs for {owner_id}: {e}")
        return {}


async def save_posting_section(owner_id: str, section: str, data: dict) -> bool:
    """Merge data into accounts.preferences.posting[section] for one presence."""
    if not owner_id:
        return False
    try:
        import json
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT preferences FROM accounts WHERE id = %s", (owner_id,)
            )
            row = await r.fetchone()
            if not row:
                return False
            prefs = (row["preferences"] if isinstance(row, dict) else row[0]) or {}
            posting = prefs.get("posting") or {}
            current = posting.get(section) or {}
            current.update(data)
            posting[section] = current
            prefs["posting"] = posting
            await conn.execute(
                "UPDATE accounts SET preferences = %s, updated_at = NOW() WHERE id = %s",
                (json.dumps(prefs), owner_id),
            )
        return True
    except Exception as e:
        logger.warning(f"posting: could not save {section} for {owner_id}: {e}")
        return False


def _x_creds_from_prefs(prefs: dict) -> dict | None:
    """Pull a complete set of 4 X keys from a preferences dict, or None."""
    x = ((prefs or {}).get("posting") or {}).get("x") or {}
    creds = {k: (x.get(k) or "").strip() for k in X_ENV_KEYS}
    return creds if all(creds.values()) else None


def _x_creds_from_env() -> dict | None:
    creds = {k: (env(envvar) or "").strip() for k, envvar in X_ENV_KEYS.items()}
    return creds if all(creds.values()) else None


async def resolve_x_creds(request=None, owner_id: str | None = None) -> tuple[dict | None, str | None]:
    """Resolve X OAuth 1.0a creds for a presence.

    Order: the presence's own saved keys → env fallback. Returns (creds, error).
    owner_id wins when given (scheduler); otherwise resolve from the request.
    """
    oid = owner_id or (await owner_id_from_request(request) if request is not None else None)
    if oid:
        creds = _x_creds_from_prefs(await _account_prefs(oid))
        if creds:
            return creds, None
    creds = _x_creds_from_env()
    if creds:
        return creds, None
    return None, ("X API credentials not set for this presence. "
                  "Add them under Posting Accounts on the Video Pipeline page.")


async def x_configured_for(request=None, owner_id: str | None = None) -> bool:
    creds, _ = await resolve_x_creds(request=request, owner_id=owner_id)
    return creds is not None


def yt_service_key(owner_id: str | None) -> str:
    """Namespaced oauth_tokens.service for a presence's YouTube token.

    Per-presence without a schema change: 'youtube:{owner_id}'. Falls back to the
    legacy global 'youtube' row when there's no owner (single mode)."""
    return f"youtube:{owner_id}" if owner_id else "youtube"
