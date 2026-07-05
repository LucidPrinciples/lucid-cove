"""
Memory Access Control — role-based memory visibility per agent.

Part of the accountability architecture. Controls which memory categories
an agent can read when accessing other agents' memories (cross-agent review,
Vera's nightly audit, etc.).

Access profiles do NOT affect an agent reading their OWN memories in normal
conversation — those flows remain unrestricted. This only applies to
cross-agent memory reads used by the review pipeline.

Profiles are defined here and referenced by agent_id. Each agent defaults
to "full" unless explicitly assigned a restricted profile.

Design:
  - Profiles are dicts with allowed/blocked category lists
  - A function filters memory rows based on the reviewer's profile
  - New profiles are added here — no config file needed until agent count grows
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Profile Definitions
# =============================================================================

PROFILES = {
    "full": {
        "description": "Unrestricted — all memory categories visible",
        "blocked_categories": [],
    },
    "operational": {
        "description": "Operational access — sees technical/project/factual, blocked from operator preferences and decisions",
        "blocked_categories": ["preference", "decision", "instruction"],
    },
    "technical": {
        "description": "Technical only — facts and technical memories",
        "blocked_categories": ["preference", "decision", "instruction", "person", "observation"],
    },
}

# Agent → profile mapping. Agents not listed default to "full".
# This is the canonical place to assign access profiles.
AGENT_PROFILES = {
    "vera": "operational",
    # All other agents default to "full"
}


# =============================================================================
# Profile Lookup
# =============================================================================

def get_agent_profile(agent_id: str) -> dict:
    """Get the memory access profile for an agent.

    Returns the profile dict with 'blocked_categories' list.
    Unknown agents get "full" (no restrictions).
    """
    profile_name = AGENT_PROFILES.get(agent_id.lower(), "full")
    profile = PROFILES.get(profile_name)
    if not profile:
        logger.warning("Unknown profile '%s' for agent '%s', using 'full'", profile_name, agent_id)
        return PROFILES["full"]
    return profile


def get_blocked_categories(agent_id: str) -> list[str]:
    """Get the list of memory categories this agent cannot see in cross-agent reads."""
    return get_agent_profile(agent_id).get("blocked_categories", [])


def is_category_visible(agent_id: str, category: str) -> bool:
    """Check if a specific memory category is visible to this agent."""
    blocked = get_blocked_categories(agent_id)
    return category.lower() not in [c.lower() for c in blocked]


# =============================================================================
# Memory Filtering
# =============================================================================

def filter_memories_by_access(
    memories: list[dict],
    reviewer_agent_id: str,
    category_key: str = "category",
) -> list[dict]:
    """Filter a list of memory dicts based on the reviewer's access profile.

    Args:
        memories: list of memory dicts (each must have a category field)
        reviewer_agent_id: the agent doing the reading (determines access)
        category_key: key name for the category field in each dict

    Returns:
        Filtered list with blocked categories removed.
    """
    blocked = get_blocked_categories(reviewer_agent_id)
    if not blocked:
        return memories  # Full access, no filtering needed

    blocked_lower = {c.lower() for c in blocked}
    filtered = [
        m for m in memories
        if m.get(category_key, "general").lower() not in blocked_lower
    ]

    removed_count = len(memories) - len(filtered)
    if removed_count > 0:
        logger.info(
            "Access control: filtered %d memories from %s's view (blocked: %s)",
            removed_count, reviewer_agent_id, blocked,
        )

    return filtered


def build_category_filter_sql(
    reviewer_agent_id: str,
    category_column: str = "category",
) -> tuple[str, list[str]]:
    """Build a SQL WHERE clause fragment to enforce access control.

    Returns:
        (sql_fragment, params) — e.g. ("AND category NOT IN (%s, %s, %s)", ["preference", "decision", "instruction"])
        If no restrictions, returns ("", []).

    Usage:
        filter_sql, filter_params = build_category_filter_sql("vera")
        query = f"SELECT * FROM agent_memory WHERE agent_id = %s {filter_sql}"
        params = [target_agent_id] + filter_params
    """
    blocked = get_blocked_categories(reviewer_agent_id)
    if not blocked:
        return ("", [])

    placeholders = ", ".join(["%s"] * len(blocked))
    sql = f"AND {category_column} NOT IN ({placeholders})"
    return (sql, blocked)


# =============================================================================
# Cross-Agent Memory Reads (access-controlled)
# =============================================================================

async def load_memories_for_review(
    target_agent_id: str,
    reviewer_agent_id: str,
    limit: int = 30,
    min_importance: float = 0.3,
    days_back: int = 1,
) -> list[dict]:
    """Load another agent's recent memories for review, respecting access control.

    This is the primary function for the nightly review pipeline.
    Returns memories from the target agent that the reviewer is allowed to see.

    Args:
        target_agent_id: whose memories to read
        reviewer_agent_id: who is reading (determines category filtering)
        limit: max memories to return
        min_importance: minimum importance threshold
        days_back: how many days back to look (default 1 = today only)
    """
    from src.memory.database import get_db

    filter_sql, filter_params = build_category_filter_sql(reviewer_agent_id)

    async with get_db() as conn:
        rows = await conn.execute(
            f"""SELECT id, content, category, importance, tags,
                       created_at, access_count, last_accessed
                FROM agent_memory
                WHERE agent_id = %s
                  AND is_active = TRUE
                  AND importance >= %s
                  AND created_at > NOW() - INTERVAL '%s days'
                  {filter_sql}
                ORDER BY importance DESC, created_at DESC
                LIMIT %s""",
            [target_agent_id, min_importance, days_back] + filter_params + [limit],
        )
        results = await rows.fetchall()

    return [dict(r) for r in results]


async def load_activity_for_review(
    target_agent_id: str,
    days_back: int = 1,
) -> list[dict]:
    """Load an agent's recent activity log for review.

    Activity is tool calls and results — what the agent DID, not what it
    remembered. This is always visible regardless of access profile because
    it's operational data, not preference data.

    Returns recent message_activity rows.
    """
    from src.memory.database import get_db

    async with get_db() as conn:
        rows = await conn.execute(
            """SELECT channel, thread_id, steps, step_count, recorded_at
               FROM message_activity
               WHERE recorded_at > NOW() - INTERVAL '%s days'
               ORDER BY recorded_at DESC
               LIMIT 50""",
            (days_back,),
        )
        results = await rows.fetchall()

    return [dict(r) for r in results]
