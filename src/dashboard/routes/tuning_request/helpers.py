"""Shared helpers for tuning_request endpoints.

Auth (presence ID resolution), reference data loading, caching,
and context-signal mapping used across core, meta, and favorites.
"""

import json
import os
from src.env import env
from pathlib import Path
from typing import Optional

from fastapi import Request


COVE_MODE = env("COVE_MODE", "single")


# ── Auth ────────────────────────────────────────────────────────────────────

_single_mode_account_id: Optional[str] = None

async def _get_presence_id(request: Request) -> Optional[str]:
    """Get the user's account UUID. Works in both single and multi mode.
    Single mode: finds or creates a default operator account.
    Multi mode: reads account from auth cookie."""
    global _single_mode_account_id

    if COVE_MODE != "multi":
        # Single-agent mode — one operator, one account
        if _single_mode_account_id:
            return _single_mode_account_id

        from src.config import get_primary_agent_id
        from src.memory.database import get_db
        agent_id = get_primary_agent_id()

        try:
            async with get_db() as conn:
                # Look up existing default account by username = agent_id
                result = await conn.execute(
                    "SELECT id FROM accounts WHERE username = %s AND active = TRUE",
                    (agent_id,),
                )
                row = await result.fetchone()
                if row:
                    _single_mode_account_id = str(row["id"])
                    return _single_mode_account_id

                # Create default operator account
                import hashlib, secrets
                token = secrets.token_hex(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                result = await conn.execute(
                    """INSERT INTO accounts (display_name, username, tier, cove_role, auth_token)
                       VALUES (%s, %s, 'cove', 'admin', %s)
                       RETURNING id""",
                    (agent_id.capitalize(), agent_id, token_hash),
                )
                row = await result.fetchone()
                _single_mode_account_id = str(row["id"])
                return _single_mode_account_id
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[tuning] Default account lookup failed: {e}")
            return None

    # Multi mode — get from auth cookie
    try:
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        return str(presence["id"]) if presence else None
    except Exception:
        return None


# ── Reference Data ──────────────────────────────────────────────────────────

_lt_ref_cache = None
_lt_ref_mtime = 0

# Day-level tuning cache: one tuning per user per day
# Key: "YYYY-MM-DD" (or "YYYY-MM-DD:{user_id}" for per-user)
# Value: tuning result dict
_daily_tuning_cache = {}


def _load_lt_reference() -> dict:
    """Load lt_reference.json with file-mtime caching."""
    global _lt_ref_cache, _lt_ref_mtime

    # Check multiple possible locations
    candidates = [
        env("LT_REFERENCE_PATH"),
        "/cove-core/data/lt_reference.json",
        "/app/data/lt_reference.json",
        "/app/data/seed/lt_reference.json",
        str(Path(__file__).resolve().parents[4] / "data" / "lt_reference.json"),
    ]

    for path in candidates:
        if path and os.path.exists(path):
            mtime = os.path.getmtime(path)
            if _lt_ref_cache and mtime == _lt_ref_mtime:
                return _lt_ref_cache
            with open(path) as f:
                _lt_ref_cache = json.load(f)
                _lt_ref_mtime = mtime
            return _lt_ref_cache

    raise FileNotFoundError("lt_reference.json not found in any expected location")


# ── Context → Signal Type Mapping ──────────────────────────────────────────
# Which signal types are appropriate for each listening context.
# Absorbed from the Lucid Tuner app's CONTEXT_SIGNAL_MAP.

CONTEXT_SIGNAL_MAP = {
    "Driving": ["Drive", "Clear", "Bright"],
    "Working / Focus": ["Clear", "Ground"],
    "Home / Domestic": ["Open", "Ground", "Bright"],
    "Moving / Workout": ["Rise", "Drive", "Bright"],
    "Starting the Day": ["Rise", "Bright", "Clear"],
    "Winding Down": ["Ground", "Open"],
    "Stillness / Meditation": ["Ground", "Open"],
    "Walking / Outside": ["Clear", "Open", "Drive"],
}
