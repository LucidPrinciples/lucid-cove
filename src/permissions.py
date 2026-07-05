"""
Tier-based permission system for Lucid Cove.

Product ladder: Free → Operator → Presence → Cove
Each tier includes everything below it plus its own additions.

This module defines what each tier can access (features, tabs, API endpoints)
and provides decorators/helpers for gating.

Reads tier from:
  - cove.yaml → cove.tier (container-level: "operator", "presence", "cove")
  - Per-user override via presence config (future: multi-tenant shared container)
"""

from enum import IntEnum
from typing import Optional
from functools import wraps

from src.config import load_cove_config, get_feature_flags


# ── Tier Enum ───────────────────────────────────────────────────────────────
# IntEnum so tiers are comparable: Operator < Presence < Cove
# Higher tier = more access. tier >= PRESENCE means "Presence or above."

class Tier(IntEnum):
    FREE = 0        # Lucid Tuner basic — daily tuning + single echo
    PRO = 5         # Lucid Tuner Pro — full tuning streams, history, journal
    OPERATOR = 10   # + personal management, Creation Flows, marketplace
    PRESENCE = 20   # + personal agent, voice, memory, permanent name
    COVE = 30       # + full team (Stuart + 9), dedicated container


# ── Feature Permission Matrix ───────────────────────────────────────────────
# Maps feature flag names (from cove.yaml features:{}) to minimum tier.
# If a feature isn't listed here, it's available at all tiers.

FEATURE_TIER_REQUIREMENTS = {
    # Operator and above (available to everyone with an account)
    "tuning": Tier.OPERATOR,
    "calendar": Tier.OPERATOR,
    "files": Tier.OPERATOR,
    "creation_flows": Tier.OPERATOR,
    "marketplace": Tier.OPERATOR,
    "action_board": Tier.OPERATOR,

    # Presence and above (requires an agent)
    "voice": Tier.PRESENCE,
    "messaging": Tier.PRESENCE,

    # Cove and above (requires full team)
    "team_tab": Tier.COVE,
    "premium_workflows": Tier.COVE,
}


# ── Tab Permission Matrix ──────────────────────────────────────────────────
# Maps tab IDs (from agent.yaml tabs:[]) to minimum tier.
# Tabs not listed here are visible at all tiers.

TAB_TIER_REQUIREMENTS = {
    # Free tier (Tuner) — the tuning-first experience
    "home": Tier.FREE,
    "tune": Tier.FREE,
    "playlists": Tier.FREE,
    "go-deeper": Tier.FREE,
    "affiliates": Tier.FREE,
    "settings": Tier.FREE,

    # Operator tier — personal management tools (no agent)
    "projects": Tier.OPERATOR,
    "calendar": Tier.OPERATOR,
    "files": Tier.OPERATOR,
    "reports": Tier.OPERATOR,
    "actions": Tier.OPERATOR,
    "flows": Tier.OPERATOR,
    "links": Tier.OPERATOR,

    # Operator tier — Chat is the Connect/Market surface (no agent needed; the
    # agent channels only appear for Presence+ who have agents). #128/#137.
    "chat": Tier.OPERATOR,

    # Presence tier — requires an agent
    "memory": Tier.PRESENCE,    # Memory requires agent for persistence

    # Cove tier — full team
    "team": Tier.COVE,
    "system": Tier.COVE,
}


# ── Tab Tier Ceiling ──────────────────────────────────────────────────────
# Tabs that are ONLY visible up to a certain tier. When you upgrade past
# this tier, the tab disappears — its functionality is accessed differently.
# Example: Tuner has a dedicated Tune tab; Operator accesses tuning via
# the home card and reports.

TAB_TIER_MAX = {
    # Tabs that are ONLY visible up to a certain tier. When you upgrade past
    # this tier, the tab disappears from navigation — its functionality is
    # accessed via the Latest Tuning card links instead.
    # NOTE: Panels and scripts still load for ALL tabs — TAB_TIER_MAX only
    # controls nav visibility. Empty = no ceiling on any tab.
}


# ── API Route Permission Matrix ────────────────────────────────────────────
# Maps route prefixes to minimum tier.
# Checked by require_tier() decorator on route handlers.

ROUTE_TIER_REQUIREMENTS = {
    # Free routes (needed for all tiers including Tuner)
    "/api/config": Tier.FREE,
    "/api/tuning": Tier.FREE,       # Tuners need tuning access
    "/api/quick-list": Tier.FREE,   # Quick Lists available at all tiers

    # Operator routes (dashboard, management tools)
    "/api/settings": Tier.OPERATOR,
    "/api/action-board": Tier.OPERATOR,
    "/api/flow": Tier.OPERATOR,
    "/api/calendar": Tier.OPERATOR,
    "/api/files": Tier.OPERATOR,
    "/api/marketplace": Tier.OPERATOR,

    # Presence routes (agent interaction)
    "/api/chat": Tier.PRESENCE,
    "/api/voice": Tier.PRESENCE,
    "/api/memory": Tier.PRESENCE,

    # Cove routes (team management, system ops)
    "/api/team": Tier.COVE,
    "/api/system": Tier.COVE,
    "/api/family": Tier.COVE,
}


# ── Tier Resolution ────────────────────────────────────────────────────────

def get_container_tier() -> Tier:
    """Get the tier this container is configured for.

    Reads from cove.yaml → cove.tier field.
    Defaults to COVE (backward compatible with existing installs).
    """
    cove = load_cove_config()
    tier_str = cove.get("tier", "cove").lower()
    return _parse_tier(tier_str)


def get_user_tier(presence_id: Optional[str] = None) -> Tier:
    """Get the effective tier for a specific user.

    In a dedicated container (Cove), everyone gets Cove tier.
    In a shared container (Operator/Presence), tier is per-user.

    Future: look up per-user tier from presence config or DB.
    For now: returns the container tier.
    """
    # TODO: In shared container mode, look up per-user tier from
    # presence config (operator vs presence account).
    # For now, container tier applies to all users.
    return get_container_tier()


def _parse_tier(tier_str: str) -> Tier:
    """Parse a tier string to Tier enum."""
    mapping = {
        "free": Tier.FREE,
        "pro": Tier.PRO,
        "operator": Tier.OPERATOR,
        "presence": Tier.PRESENCE,
        "cove": Tier.COVE,
    }
    return mapping.get(tier_str, Tier.COVE)


# ── Permission Checks ──────────────────────────────────────────────────────

def can_access_feature(feature: str, tier: Optional[Tier] = None) -> bool:
    """Check if a feature is accessible at the given tier.

    Checks both:
    1. The tier meets the minimum requirement for this feature
    2. The feature is enabled in cove.yaml feature flags

    Args:
        feature: Feature flag name (e.g., "voice", "team_tab")
        tier: Tier to check against. Defaults to container tier.
    """
    if tier is None:
        tier = get_container_tier()

    # Check tier requirement
    required_tier = FEATURE_TIER_REQUIREMENTS.get(feature, Tier.OPERATOR)
    if tier < required_tier:
        return False

    # Check feature flag (admin can still disable features at any tier)
    flags = get_feature_flags()
    return flags.get(feature, True)


def can_access_tab(tab_id: str, tier: Optional[Tier] = None) -> bool:
    """Check if a tab should be visible at the given tier.

    Checks both floor (minimum tier) and ceiling (maximum tier).
    Tuner tabs like tune/playlists have a ceiling — they disappear
    at Operator+ because their functionality is accessed differently.
    """
    if tier is None:
        tier = get_container_tier()
    required_tier = TAB_TIER_REQUIREMENTS.get(tab_id, Tier.OPERATOR)
    if tier < required_tier:
        return False
    max_tier = TAB_TIER_MAX.get(tab_id)
    if max_tier is not None and tier > max_tier:
        return False
    return True


def can_access_route(route_path: str, tier: Optional[Tier] = None) -> bool:
    """Check if an API route is accessible at the given tier.

    Matches route prefixes — /api/chat/send matches /api/chat requirement.
    """
    if tier is None:
        tier = get_container_tier()

    for prefix, required_tier in ROUTE_TIER_REQUIREMENTS.items():
        if route_path.startswith(prefix):
            return tier >= required_tier

    # Routes not in the matrix are accessible at Operator and above
    return tier >= Tier.OPERATOR


def filter_tabs_for_tier(tabs: list[dict], tier: Optional[Tier] = None) -> list[dict]:
    """Filter a tab list to only those accessible at the given tier.

    Used by get_frontend_config() to send only visible tabs to the UI.
    """
    if tier is None:
        tier = get_container_tier()
    return [tab for tab in tabs if can_access_tab(tab.get("id", ""), tier)]


def filter_features_for_tier(features: dict, tier: Optional[Tier] = None) -> dict:
    """Filter feature flags to only those accessible at the given tier.

    Features above the tier are forced to False regardless of config.
    """
    if tier is None:
        tier = get_container_tier()
    filtered = {}
    for feature, enabled in features.items():
        if can_access_feature(feature, tier):
            filtered[feature] = enabled
        else:
            filtered[feature] = False
    return filtered


def get_tier_info(tier: Optional[Tier] = None) -> dict:
    """Get tier metadata for the frontend.

    Returned in /api/config so the UI knows what tier it's running at.
    """
    if tier is None:
        tier = get_container_tier()
    return {
        "current": tier.name.lower(),
        "level": int(tier),
        "has_agent": tier >= Tier.PRESENCE,
        "has_team": tier >= Tier.COVE,
        "upgrade_available": tier < Tier.COVE,
    }


# ── Route Decorator ────────────────────────────────────────────────────────

def require_tier(minimum_tier: Tier):
    """Decorator for FastAPI route handlers that gates by tier.

    Usage:
        @router.get("/api/team/roster")
        @require_tier(Tier.COVE)
        async def get_roster():
            ...

    Returns 403 with tier info if the user doesn't meet the requirement.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user_tier = get_user_tier()  # TODO: pass presence_id from request
            if user_tier < minimum_tier:
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "tier_required",
                        "required": minimum_tier.name.lower(),
                        "current": user_tier.name.lower(),
                        "message": f"This feature requires {minimum_tier.name.lower()} tier or above.",
                    },
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator
