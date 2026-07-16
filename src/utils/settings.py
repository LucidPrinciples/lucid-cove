"""
System Settings — deployment-specific configuration loaded from the database.

This module provides the single source of truth for values that change per
family deployment: family name, operator name, admin agent ID, etc.

Settings are cached in memory on first access and refreshed on demand.
Runtime code should call get_setting() or get_family_config() instead
of hardcoding deployment-specific values.

Usage:
    from src.utils.settings import get_setting, get_family_config

    operator = await get_setting("operator_display_name")  # e.g. "Operator"
    config = await get_family_config()  # dict with all settings
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# Cache — settings are loaded once and cached until refresh
# =============================================================================

_cache: dict[str, str] = {}
_cache_loaded: bool = False
_cache_time: float = 0
_CACHE_TTL: int = 300  # 5 minutes — settings rarely change

# Defaults — used if DB is unavailable (first startup, migration not run yet)
# Neutral fallbacks ONLY — used if the DB is unavailable (first startup,
# migration not run yet). Real per-Cove values live in system_settings (DB wins)
# and are seeded from the instance cove.yaml at provision time. No founder values here.
# NO family_name here: a fallback surname gets APPENDED to every agent's display
# name by get_full_name ("Stuart Cove" on any box whose DB isn't up yet). No value
# = bare first names until the wizard finalize writes the real one. (A Cove really
# named "Cove" still works — that value lives in the DB, not here.)
_DEFAULTS = {
    "operator_id": "operator",
    "operator_display_name": "Operator",
    "admin_agent_id": "stuart",
    "admin_agent_display_name": "Stuart",
    "location": "",
    "tuning_package_folder": "default",
    # #D58 Cove Charter — cove-level mission + operating principles, injected
    # into every agent's system prompt (identity.py). DB rows (seeded by
    # migration 038, wizard-set mission, admin-edited) override these.
    "charter.mission": "",
    "charter.principles": (
        "- Truth over comfort, warmth over coldness. Both, not either.\n"
        "- New breakage outranks the plan. When something breaks or a more urgent issue "
        "appears mid-task, stop, name it, and re-prioritize with the operator before "
        "continuing. Never push the original task through a fire. Finishing the wrong "
        "thing is not progress.\n"
        "- Say what you don't know. Never invent file paths, names, numbers, or artifacts.\n"
        "- The operator decides priorities. Surface, propose, then follow their call."
    ),
}


# =============================================================================
# Core functions
# =============================================================================

async def _load_settings() -> dict[str, str]:
    """Load all settings from the database."""
    global _cache, _cache_loaded, _cache_time

    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute("SELECT key, value FROM system_settings")
            rows = await result.fetchall()
            _cache = {row["key"]: row["value"] for row in rows}
            _cache_loaded = True
            _cache_time = time.time()
            logger.info(f"System settings loaded: {len(_cache)} entries")
            return _cache
    except Exception as e:
        logger.warning(f"Failed to load system settings from DB (using defaults): {e}")
        _cache = dict(_DEFAULTS)
        _cache_loaded = True
        _cache_time = time.time()
        return _cache


async def get_setting(key: str, default: Optional[str] = None) -> str:
    """Get a single setting by key. Returns default if not found.

    Settings are cached for performance. First call loads from DB.
    """
    global _cache, _cache_loaded, _cache_time

    # Check if cache needs refresh
    if not _cache_loaded or (time.time() - _cache_time > _CACHE_TTL):
        await _load_settings()

    # Try cache, then hardcoded defaults, then provided default
    if key in _cache:
        return _cache[key]
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    if default is not None:
        return default
    raise KeyError(f"Unknown setting: {key}")


async def get_family_config() -> dict[str, str]:
    """Get all family settings as a dict. Convenience for bulk access."""
    if not _cache_loaded or (time.time() - _cache_time > _CACHE_TTL):
        await _load_settings()
    # Merge defaults with DB values (DB wins)
    merged = dict(_DEFAULTS)
    merged.update(_cache)
    return merged


async def update_setting(key: str, value: str) -> bool:
    """Update a setting in the database and refresh cache."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO system_settings (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                (key, value),
            )
        # Refresh cache immediately
        _cache[key] = value
        logger.info(f"Setting updated: {key} = {value}")
        return True
    except Exception as e:
        logger.error(f"Failed to update setting {key}: {e}")
        return False


def refresh_cache():
    """Force cache invalidation. Next get_setting() call will reload from DB."""
    global _cache_loaded
    _cache_loaded = False


def get_setting_sync(key: str, default: Optional[str] = None) -> str:
    """Synchronous access to cached settings. Returns default if cache not loaded.

    Use this ONLY in contexts where async is not available (e.g., module-level
    constants, synchronous functions). The cache must have been loaded by a prior
    async call — this will NOT trigger a DB load.
    """
    if key in _cache:
        return _cache[key]
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    if default is not None:
        return default
    return _DEFAULTS.get(key, "")
