"""
Memory Service — core CRUD, search, and loading for family agents.

This is the core memory layer. It sits between raw conversation (LangGraph
checkpointer) and the agent's system prompt, providing:

  1. STORE    — Save discrete memories with categories, importance, tags, source tracking
  2. RECALL   — Load memories by category, recency, importance, or semantic similarity
  3. SEARCH   — Full-text and tag-based search across the memory corpus
  4. UPDATE   — Modify existing memories, supersede stale knowledge
  5. LOAD     — Budget-aware memory loading for system prompt injection

Thread management, extraction, and summarization live in threads.py.
Stats, expiry, review queue, and backfill live in maintenance.py.

Architecture:
  - All memories live in PostgreSQL (agent_memory table)
  - Embeddings via pgvector enable semantic search (when available)
  - Superseding chains keep knowledge current without losing history
  - Importance scoring + access tracking enable smart context budgeting
  - Category system organizes memories for targeted retrieval

Categories:
  decision   — Choices the operator made, preferences expressed, directions set
  fact       — Concrete information: names, dates, specs, addresses
  preference — How the operator likes things done, communication style, etc.
  person     — Info about people in the operator's life and work
  project    — Project-specific context, goals, status, architecture decisions
  technical  — System configs, deployment details, infrastructure knowledge
  observation — Patterns the agent notices, things that worked/didn't work
  instruction — Standing orders, rules, recurring procedures
  general    — Anything that doesn't fit neatly elsewhere
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from src.memory.database import get_db
from src.config import get_primary_agent_id

logger = logging.getLogger("family.memory")


def _default_agent_id(agent_id: str | None) -> str:
    """Resolve agent_id, falling back to config if not provided."""
    return agent_id or get_primary_agent_id()


def _memory_agent_id(channel: str, agent_id: str) -> str:
    """Resolve the agent_id to use for memory storage on a channel.

    Steward channels use the steward's agent_id so all Presences share
    one memory pool. Regular channels use the host agent_id.
    """
    from src.config import _is_steward_channel, get_steward_channel_config
    if _is_steward_channel(channel):
        sc = get_steward_channel_config()
        return sc.get("agent_id", "stuart") if sc else "stuart"
    return agent_id

VALID_CATEGORIES = {
    "decision", "fact", "preference", "person", "project",
    "technical", "observation", "instruction", "general",
    "architecture", "deployment", "bug_fix", "feature", "process",
    "context",  # thread summaries and continuity context
    "tuning",   # daily tuning state — frequency, principle, tuning key, coaching
    "synthesis", # higher-level patterns extracted from memory clusters
    "ceremony",  # Memory Ceremony records — participatory hygiene reflections
}

# Token budget defaults (rough estimate: 1 token ≈ 4 chars)
# Personal agents get more budget — they need to deeply know their operator.
# Admin/steward agents serve multiple people, keep it tighter.
DEFAULT_MEMORY_BUDGET = 5000   # ~1250 tokens — enough for ~30-40 memories
PERSONAL_MEMORY_BUDGET = 8000  # ~2000 tokens — personal agents get more context
MAX_MEMORY_BUDGET = 12000      # ~3000 tokens — hard cap


def _get_memory_budget() -> int:
    """Return memory budget based on instance type. Personal agents get more."""
    try:
        from src.config import get_instance
        instance_type = get_instance().get("type", "personal")
        if instance_type == "personal":
            return PERSONAL_MEMORY_BUDGET
        return DEFAULT_MEMORY_BUDGET
    except Exception:
        return DEFAULT_MEMORY_BUDGET


# =============================================================================
# Temporal Decay — continuous importance weighting over time
# =============================================================================

def compute_temporal_weight(
    importance: float,
    created_at: datetime,
    access_count: int = 0,
    last_accessed: datetime | None = None,
    now: datetime | None = None,
) -> float:
    """Compute effective importance with continuous temporal decay.

    This is the core function that makes memory retrieval time-aware.
    Instead of treating all memories equally regardless of age, this
    applies exponential decay where:

      - Half-life scales with importance: high-importance memories
        decay very slowly, low-importance memories fade fast.
      - Access patterns resist decay: frequently retrieved memories
        stay relevant longer.
      - Recent access gives a temporary boost.

    The formula:
      half_life = 7 + (importance ^ 1.5) * 358  days
        → importance 0.0: half-life ~7 days (gone in 2 weeks)
        → importance 0.3: half-life ~66 days (~2 months)
        → importance 0.5: half-life ~134 days (~4.5 months)
        → importance 0.8: half-life ~263 days (~9 months)
        → importance 1.0: half-life ~365 days (barely decays in a year)

      decay_factor = 0.5 ^ (days_old / half_life)
      access_boost = 1.0 + min(0.5, ln(1 + access_count) * 0.15)
        → 0 accesses: 1.0x (no boost)
        → 3 accesses: ~1.21x
        → 10 accesses: ~1.36x
        → 20+ accesses: caps at 1.5x

      recency_boost: +0.3 if accessed in last 3 days, +0.15 if last 7 days

      effective_importance = importance * decay_factor * access_boost

    Returns a float in [0.0, 1.0].
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Ensure timezone-aware comparison
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    days_old = max(0.0, (now - created_at).total_seconds() / 86400.0)

    # Half-life scales with importance: higher importance = much slower decay
    half_life_days = 7.0 + (importance ** 1.5) * 358.0

    # Exponential decay
    if days_old == 0:
        decay_factor = 1.0
    else:
        decay_factor = math.pow(0.5, days_old / half_life_days)

    # Access boost: frequently retrieved memories resist decay
    # Diminishing returns via log — first few accesses matter most
    access_boost = 1.0 + min(0.5, math.log1p(access_count) * 0.15)

    # Recency of last access: recently used memories get a bump
    if last_accessed is not None:
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        days_since_access = max(0.0, (now - last_accessed).total_seconds() / 86400.0)
        if days_since_access < 3:
            access_boost += 0.3
        elif days_since_access < 7:
            access_boost += 0.15

    effective = importance * decay_factor * access_boost

    # Clamp to [0, 1]
    return max(0.0, min(1.0, effective))


def _compute_weight_from_row(row: dict, now: datetime | None = None) -> float:
    """Convenience: compute temporal weight from a DB row dict."""
    return compute_temporal_weight(
        importance=float(row.get("importance", 0.5)),
        created_at=row["created_at"],
        access_count=int(row.get("access_count", 0)),
        last_accessed=row.get("last_accessed"),
        now=now,
    )


# =============================================================================
# Store
# =============================================================================

async def store_memory(
    content: str,
    category: str = "general",
    importance: float = 0.5,
    tags: list[str] | None = None,
    agent_id: str | None = None,
    source_thread: str | None = None,
    source_channel: str | None = None,
    source_summary: str | None = None,
    expires_at: str | None = None,
    supersedes_id: int | None = None,
) -> dict:
    """Store a new memory entry.

    Returns the created memory dict with id.
    If supersedes_id is provided, marks the old memory as superseded.
    """
    agent_id = _default_agent_id(agent_id)
    if category not in VALID_CATEGORIES:
        category = "general"
    importance = max(0.0, min(1.0, importance))
    tags = tags or []

    async with get_db() as conn:
        # If superseding, mark the old memory
        if supersedes_id:
            await conn.execute(
                """UPDATE agent_memory
                   SET is_active = FALSE, superseded_by = NULL, updated_at = NOW()
                   WHERE id = %s AND agent_id = %s""",
                (supersedes_id, agent_id),
            )

        result = await conn.execute(
            """INSERT INTO agent_memory
               (agent_id, content, category, importance, tags,
                source_thread, source_channel, source_summary,
                supersedes, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (agent_id, content, category, importance, tags,
             source_thread, source_channel, source_summary,
             supersedes_id,
             expires_at),
        )
        row = await result.fetchone()

        # Update the superseded_by pointer on the old memory
        if supersedes_id and row:
            await conn.execute(
                """UPDATE agent_memory SET superseded_by = %s
                   WHERE id = %s""",
                (row["id"], supersedes_id),
            )

        memory_id = row["id"]
        logger.info(
            f"Stored memory #{memory_id}: [{category}] "
            f"importance={importance} tags={tags}"
        )

        # Generate embedding async — don't block memory storage on Ollama
        import asyncio
        asyncio.ensure_future(_embed_memory(memory_id, content, agent_id))

        return {
            "id": memory_id,
            "content": content,
            "category": category,
            "importance": importance,
            "tags": tags,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }


async def _embed_memory(memory_id: int, content: str, agent_id: str):
    """Generate and store embedding for a memory. Non-blocking background task.

    Uses the same nomic-embed-text model as the knowledge base (768 dimensions).
    If embedding fails (Ollama down, model not loaded, etc.), the memory
    still exists — it just won't appear in semantic search results.
    """
    try:
        from src.memory.knowledge import get_embedding
        embedding = await get_embedding(content)
        if embedding:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE agent_memory SET embedding = %s WHERE id = %s AND agent_id = %s",
                    (str(embedding), memory_id, agent_id),
                )
            logger.debug(f"Embedded memory #{memory_id}")
        else:
            logger.debug(f"No embedding returned for memory #{memory_id} (Ollama may be unavailable)")
    except Exception as e:
        # Never let embedding failure propagate — memory is already stored
        logger.warning(f"Embedding failed for memory #{memory_id}: {e}")


# =============================================================================
# Semantic search — find memories by meaning
# =============================================================================

async def search_memories_semantic(
    query: str,
    agent_id: str | None = None,
    limit: int = 8,
    min_similarity: float = 0.3,
    exclude_ids: set | None = None,
) -> list[dict]:
    """Semantic search over agent memories using pgvector cosine similarity.

    Embeds the query text, then finds the most similar active memories.
    Results are ranked by a blended score: 70% semantic similarity + 30%
    temporal weight. This ensures highly relevant old memories still surface
    but recent relevant memories get a natural boost.

    Returns list of dicts with: id, content, category, importance, tags, similarity, temporal_weight, score
    """
    agent_id = _default_agent_id(agent_id)
    exclude_ids = exclude_ids or set()

    try:
        from src.memory.knowledge import get_embedding
        query_embedding = await get_embedding(query)
        if not query_embedding:
            logger.debug("search_memories_semantic: no embedding returned for query")
            return []
    except Exception as e:
        logger.warning(f"search_memories_semantic: embedding failed: {e}")
        return []

    # Fetch more candidates than needed so we can re-rank after blending
    fetch_limit = max(limit * 3, 20) + len(exclude_ids)

    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      access_count, last_accessed, created_at,
                      1 - (embedding <=> %s::vector) as similarity
               FROM agent_memory
               WHERE agent_id = %s
                 AND is_active = TRUE
                 AND embedding IS NOT NULL
                 AND (expires_at IS NULL OR expires_at > NOW())
                 AND COALESCE(needs_review, FALSE) = FALSE
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (str(query_embedding), agent_id, str(query_embedding), fetch_limit),
        )
        rows = await result.fetchall()

    now = datetime.now(timezone.utc)
    candidates = []
    for r in rows:
        if r["id"] in exclude_ids:
            continue
        sim = float(r.get("similarity", 0))
        if sim < min_similarity:
            continue

        tw = _compute_weight_from_row(r, now)
        # Blended score: similarity drives relevance, temporal weight prevents stale results
        blended = (sim * 0.7) + (tw * 0.3)

        candidates.append({
            "id": r["id"],
            "content": r["content"],
            "category": r["category"],
            "importance": float(r["importance"]),
            "tags": r["tags"] or [],
            "similarity": round(sim, 4),
            "temporal_weight": round(tw, 4),
            "score": round(blended, 4),
        })

    # Sort by blended score and return top results
    candidates.sort(key=lambda x: x["score"], reverse=True)
    results = candidates[:limit]

    logger.debug(f"search_memories_semantic: query returned {len(results)} results (threshold={min_similarity})")
    return results


# =============================================================================
# Recall — targeted retrieval
# =============================================================================

async def recall_memories(
    agent_id: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    min_importance: float = 0.0,
    limit: int = 20,
    include_expired: bool = False,
    review_status: str | None = None,
) -> list[dict]:
    """Recall memories by category, tags, and/or importance threshold.

    review_status: 'pending' (unreviewed), 'committed' (reviewed), or None (all).
    Returns memories sorted by importance DESC, then recency.
    """
    agent_id = _default_agent_id(agent_id)
    conditions = ["agent_id = %s", "is_active = TRUE"]
    params: list = [agent_id]

    if not include_expired:
        conditions.append("(expires_at IS NULL OR expires_at > NOW())")

    if review_status == "pending":
        conditions.append("reviewed = FALSE")
    elif review_status == "committed":
        conditions.append("reviewed = TRUE")

    if category:
        conditions.append("category = %s")
        params.append(category)

    if tags:
        conditions.append("tags && %s")  # Array overlap operator
        params.append(tags)

    if min_importance > 0:
        conditions.append("importance >= %s")
        params.append(min_importance)

    where = " AND ".join(conditions)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT id, content, category, importance, tags,
                       source_channel, source_summary,
                       access_count, reviewed, reviewed_at,
                       created_at, updated_at
                FROM agent_memory
                WHERE {where}
                ORDER BY importance DESC, created_at DESC
                LIMIT %s""",
            (*params, limit),
        )
        rows = await result.fetchall()

        # Update access tracking
        if rows:
            ids = [r["id"] for r in rows]
            await conn.execute(
                """UPDATE agent_memory
                   SET access_count = access_count + 1,
                       last_accessed = NOW()
                   WHERE id = ANY(%s)""",
                (ids,),
            )

        return [_serialize_memory(r) for r in rows]


# =============================================================================
# Temporal recall — memories by time range
# =============================================================================

async def recall_memories_by_time(
    agent_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    category: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """Recall memories created within a time range.

    Args:
        agent_id: Agent to query (defaults to primary)
        since: Start of range, e.g. '2026-05-01', '7 days ago', 'today'
        until: End of range (defaults to now)
        category: Optional category filter
        limit: Max results

    Supports PostgreSQL interval syntax: '7 days', '1 month', 'today', etc.
    """
    agent_id = _default_agent_id(agent_id)
    conditions = ["agent_id = %s", "is_active = TRUE"]
    params: list = [agent_id]

    if since:
        # Try as a date first, then as an interval
        if since.lower() == "today":
            conditions.append("created_at >= CURRENT_DATE")
        elif since.lower() == "yesterday":
            conditions.append("created_at >= CURRENT_DATE - INTERVAL '1 day'")
        elif "ago" in since.lower():
            # "7 days ago" → interval '7 days'
            interval = since.lower().replace(" ago", "").strip()
            conditions.append(f"created_at >= NOW() - INTERVAL '{interval}'")
        else:
            # Assume date string like '2026-05-01'
            conditions.append("created_at >= %s::timestamp")
            params.append(since)

    if until:
        if until.lower() == "today":
            conditions.append("created_at < CURRENT_DATE + INTERVAL '1 day'")
        else:
            conditions.append("created_at <= %s::timestamp")
            params.append(until)

    if category:
        conditions.append("category = %s")
        params.append(category)

    where = " AND ".join(conditions)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT id, content, category, importance, tags,
                       source_channel, source_summary,
                       access_count, reviewed, reviewed_at,
                       created_at, updated_at
                FROM agent_memory
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s""",
            (*params, limit),
        )
        rows = await result.fetchall()
        return [_serialize_memory(r) for r in rows]


# =============================================================================
# Search — full-text across all memories
# =============================================================================

async def search_memories(
    query: str,
    agent_id: str | None = None,
    category: str | None = None,
    review_status: str | None = None,
    limit: int = 15,
) -> list[dict]:
    """Search memories by text content match.

    Uses PostgreSQL ILIKE for text search. Results are re-ranked by
    temporal weight (continuous decay) so older low-importance matches
    naturally sort below recent relevant ones.
    """
    agent_id = _default_agent_id(agent_id)
    conditions = ["agent_id = %s", "is_active = TRUE",
                   "(expires_at IS NULL OR expires_at > NOW())"]
    params: list = [agent_id]

    if review_status == "pending":
        conditions.append("reviewed = FALSE")
    elif review_status == "committed":
        conditions.append("reviewed = TRUE")

    # Split query into words, search for all of them
    words = query.strip().split()
    for word in words[:5]:  # Cap at 5 search terms
        conditions.append("content ILIKE %s")
        params.append(f"%{word}%")

    if category:
        conditions.append("category = %s")
        params.append(category)

    where = " AND ".join(conditions)

    # Fetch more candidates than needed, re-rank with temporal decay
    fetch_limit = max(limit * 2, 30)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT id, content, category, importance, tags,
                       source_channel, source_summary,
                       access_count, last_accessed, reviewed, reviewed_at,
                       created_at, updated_at
                FROM agent_memory
                WHERE {where}
                ORDER BY importance DESC, created_at DESC
                LIMIT %s""",
            (*params, fetch_limit),
        )
        rows = await result.fetchall()

        if not rows:
            return []

        # Re-rank by temporal weight
        now = datetime.now(timezone.utc)
        scored = []
        for r in rows:
            tw = _compute_weight_from_row(r, now)
            scored.append((tw, r))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take the top results
        top_rows = [r for _, r in scored[:limit]]

        # Update access tracking
        ids = [r["id"] for r in top_rows]
        await conn.execute(
            """UPDATE agent_memory
               SET access_count = access_count + 1,
                   last_accessed = NOW()
               WHERE id = ANY(%s)""",
            (ids,),
        )

        return [_serialize_memory(r) for r in top_rows]


# =============================================================================
# Update
# =============================================================================

async def update_memory(
    memory_id: int,
    agent_id: str | None = None,
    content: str | None = None,
    category: str | None = None,
    importance: float | None = None,
    tags: list[str] | None = None,
    is_active: bool | None = None,
    reviewed: bool | None = None,
) -> dict | None:
    """Update fields on an existing memory."""
    agent_id = _default_agent_id(agent_id)
    updates = []
    params: list = []

    if content is not None:
        updates.append("content = %s")
        params.append(content)
    if category is not None and category in VALID_CATEGORIES:
        updates.append("category = %s")
        params.append(category)
    if importance is not None:
        updates.append("importance = %s")
        params.append(max(0.0, min(1.0, importance)))
    if tags is not None:
        updates.append("tags = %s")
        params.append(tags)
    if is_active is not None:
        updates.append("is_active = %s")
        params.append(is_active)
    if reviewed is not None:
        updates.append("reviewed = %s")
        params.append(reviewed)
        if reviewed:
            updates.append("reviewed_at = NOW()")

    if not updates:
        return None

    updates.append("updated_at = NOW()")
    set_clause = ", ".join(updates)
    params.extend([memory_id, agent_id])

    async with get_db() as conn:
        result = await conn.execute(
            f"""UPDATE agent_memory SET {set_clause}
                WHERE id = %s AND agent_id = %s
                RETURNING id, content, category, importance, tags,
                          is_active, reviewed, reviewed_at, updated_at""",
            params,
        )
        row = await result.fetchone()
        return _serialize_memory(row) if row else None


# =============================================================================
# Correct — supersession-based memory correction
# =============================================================================

async def correct_memory(
    memory_id: int,
    new_content: str,
    reason: str = "",
    agent_id: str | None = None,
    source_thread: str | None = None,
    source_channel: str | None = None,
) -> dict:
    """Correct a memory by creating a new version that supersedes the old one.

    This preserves the history chain — the old memory is deactivated and
    linked to the new one via supersedes/superseded_by. The correction
    reason is recorded for audit.

    Returns the new memory dict.
    """
    agent_id = _default_agent_id(agent_id)

    async with get_db() as conn:
        # Get the old memory to preserve category/importance/tags
        old_result = await conn.execute(
            """SELECT id, content, category, importance, tags
               FROM agent_memory
               WHERE id = %s AND agent_id = %s AND is_active = TRUE""",
            (memory_id, agent_id),
        )
        old = await old_result.fetchone()
        if not old:
            return {}

    # Store new memory that supersedes the old one
    new_mem = await store_memory(
        content=new_content,
        category=old["category"],
        importance=old["importance"],
        tags=old["tags"] or [],
        agent_id=agent_id,
        source_thread=source_thread,
        source_channel=source_channel,
        source_summary=f"Corrected from #{memory_id}: {reason}" if reason else f"Corrected from #{memory_id}",
        supersedes_id=memory_id,
    )

    logger.info(
        f"Memory #{memory_id} corrected → #{new_mem['id']}: {reason or 'no reason given'}"
    )
    return new_mem


async def get_memory(memory_id: int, agent_id: str | None = None) -> dict | None:
    """Fetch a single active memory by id (#D54 memory_get)."""
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      source_channel, source_summary, source_thread,
                      access_count, last_accessed, reviewed, reviewed_at,
                      created_at, updated_at, is_active,
                      supersedes_id, superseded_by
               FROM agent_memory
               WHERE id = %s AND agent_id = %s""",
            (memory_id, agent_id),
        )
        row = await result.fetchone()
        if not row:
            return None
        await conn.execute(
            """UPDATE agent_memory
               SET access_count = access_count + 1,
                   last_accessed = NOW()
               WHERE id = %s""",
            (memory_id,),
        )
        return _serialize_memory(row)


async def get_memory_history(memory_id: int, agent_id: str | None = None) -> list[dict]:
    """Get the supersession history chain for a memory.

    Walks backward (supersedes) and forward (superseded_by) to show
    how a memory evolved over time.
    """
    agent_id = _default_agent_id(agent_id)

    async with get_db() as conn:
        # Walk backward to find the root
        chain = []
        current_id = memory_id
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            result = await conn.execute(
                """SELECT id, content, category, importance, tags,
                          is_active, supersedes, superseded_by,
                          source_summary, created_at
                   FROM agent_memory
                   WHERE id = %s AND agent_id = %s""",
                (current_id, agent_id),
            )
            row = await result.fetchone()
            if not row:
                break
            chain.append(_serialize_memory(row))
            current_id = row["supersedes"]

        chain.reverse()  # Oldest first

        # Walk forward from the requested memory
        current_id = memory_id
        visited_fwd = {memory_id}
        result = await conn.execute(
            "SELECT superseded_by FROM agent_memory WHERE id = %s",
            (current_id,),
        )
        row = await result.fetchone()
        current_id = row["superseded_by"] if row else None

        while current_id and current_id not in visited_fwd:
            visited_fwd.add(current_id)
            result = await conn.execute(
                """SELECT id, content, category, importance, tags,
                          is_active, supersedes, superseded_by,
                          source_summary, created_at
                   FROM agent_memory
                   WHERE id = %s AND agent_id = %s""",
                (current_id, agent_id),
            )
            row = await result.fetchone()
            if not row:
                break
            chain.append(_serialize_memory(row))
            current_id = row["superseded_by"]

    return chain


async def find_contradicting_memories(
    new_content: str,
    agent_id: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Find existing memories that might contradict new content.

    Uses keyword overlap to find potential conflicts. Returns memories
    that share significant word overlap with the new content.
    """
    agent_id = _default_agent_id(agent_id)

    # Extract significant words (skip short/common words)
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "has",
        "have", "had", "do", "does", "did", "will", "would", "could", "should",
        "may", "might", "can", "to", "of", "in", "for", "on", "with", "at",
        "by", "from", "as", "into", "that", "this", "it", "its", "and", "or",
        "but", "not", "no", "so", "if", "then", "than", "when", "while",
    }
    words = [
        w.lower().strip(".,!?;:'\"()[]")
        for w in new_content.split()
        if len(w) > 3
    ]
    keywords = [w for w in words if w not in stop_words][:8]

    if not keywords:
        return []

    # Search for memories containing multiple shared keywords.
    # At least 2 keyword matches to be a potential contradiction.
    # Use the first 3 keywords as SQL filters — params MUST match only
    # the placeholders we actually append (bug: previously pushed every
    # keyword into params while only binding [:3], which made psycopg
    # raise "query has 5 placeholders but N parameters" and aborted the
    # entire thread-rotation extraction with extraction_count=0).
    if len(keywords) < 2:
        return []

    sql_keywords = keywords[:3]
    conditions = ["agent_id = %s", "is_active = TRUE"]
    params: list = [agent_id]
    for kw in sql_keywords:
        conditions.append("content ILIKE %s")
        params.append(f"%{kw}%")

    where = " AND ".join(conditions)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT id, content, category, importance, tags,
                       created_at, updated_at
                FROM agent_memory
                WHERE {where}
                ORDER BY importance DESC, created_at DESC
                LIMIT %s""",
            (*params, limit),
        )
        rows = await result.fetchall()

    return [_serialize_memory(r) for r in rows]


async def flag_memory(
    memory_id: int,
    reason: str,
    agent_id: str | None = None,
) -> bool:
    """Flag a memory for operator review.

    Sets needs_review = TRUE with a reason. Used by contradiction detection
    during extraction. Operator can approve or dismiss from the dashboard.
    """
    agent_id = _default_agent_id(agent_id)
    try:
        async with get_db() as conn:
            await conn.execute(
                """UPDATE agent_memory
                   SET needs_review = TRUE, review_reason = %s, updated_at = NOW()
                   WHERE id = %s AND agent_id = %s""",
                (reason, memory_id, agent_id),
            )
        return True
    except Exception as e:
        logger.warning(f"Failed to flag memory #{memory_id}: {e}")
        return False


async def resolve_flag(
    memory_id: int,
    action: str = "approve",
    agent_id: str | None = None,
) -> bool:
    """Resolve a flagged memory — approve keeps it, dismiss deactivates it."""
    agent_id = _default_agent_id(agent_id)
    try:
        async with get_db() as conn:
            if action == "dismiss":
                await conn.execute(
                    """UPDATE agent_memory
                       SET needs_review = FALSE, review_reason = NULL,
                           is_active = FALSE, updated_at = NOW()
                       WHERE id = %s AND agent_id = %s""",
                    (memory_id, agent_id),
                )
            else:  # approve
                await conn.execute(
                    """UPDATE agent_memory
                       SET needs_review = FALSE, review_reason = NULL, updated_at = NOW()
                       WHERE id = %s AND agent_id = %s""",
                    (memory_id, agent_id),
                )
        return True
    except Exception as e:
        logger.warning(f"Failed to resolve flag on memory #{memory_id}: {e}")
        return False


async def get_flagged_memories(agent_id: str | None = None) -> list[dict]:
    """Get all memories flagged for review."""
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      review_reason, source_summary, created_at, updated_at
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE AND needs_review = TRUE
               ORDER BY created_at DESC""",
            (agent_id,),
        )
        rows = await result.fetchall()
    return [_serialize_memory(r) for r in rows]


# =============================================================================
# Smart Loading — budget-aware memory injection for system prompts
# =============================================================================

async def load_memories_for_prompt(
    agent_id: str | None = None,
    channel: str | None = None,
    budget_chars: int | None = None,
) -> str:
    """Load memories formatted for system prompt injection.

    Builds a memory block within a character budget, prioritizing:
      1. High-importance memories (instructions, critical decisions)
      2. Recently accessed memories (actively relevant)
      3. Channel-relevant memories
      4. Recent memories

    Uses temporal weighting: importance is blended with age decay so
    recent memories about active topics outrank old high-importance
    memories about resolved issues.

    Returns a formatted string ready to append to the system prompt,
    or empty string if no memories exist.
    """
    agent_id = _default_agent_id(agent_id)
    if budget_chars is None:
        budget_chars = _get_memory_budget()
    budget_chars = min(budget_chars, MAX_MEMORY_BUDGET)

    now = datetime.now(timezone.utc)

    async with get_db() as conn:
        # Load recent thread summaries first — these provide conversation continuity
        # across thread boundaries. Most recent 3 summaries get priority.
        continuity = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      access_count, last_accessed, created_at
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND category = 'context'
                 AND (expires_at IS NULL OR expires_at > NOW())
                 AND COALESCE(needs_review, FALSE) = FALSE
               ORDER BY created_at DESC
               LIMIT 3""",
            (agent_id,),
        )
        continuity_rows = await continuity.fetchall()

        # Load ALL non-context active memories as candidates.
        # We fetch a wide set and let the temporal decay function
        # do the ranking in Python — single source of truth for the
        # decay formula instead of duplicating step-functions in SQL.
        candidates = await conn.execute(
            """SELECT id, content, category, importance, tags,
                      access_count, last_accessed, created_at
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND (expires_at IS NULL OR expires_at > NOW())
                 AND COALESCE(needs_review, FALSE) = FALSE
                 AND category != 'context'
               ORDER BY importance DESC, created_at DESC
               LIMIT 80""",
            (agent_id,),
        )
        candidate_rows = await candidates.fetchall()

        # Channel-specific memories if channel provided
        channel_rows = []
        if channel:
            ch_result = await conn.execute(
                """SELECT id, content, category, importance, tags,
                          access_count, last_accessed, created_at
                   FROM agent_memory
                   WHERE agent_id = %s AND is_active = TRUE
                     AND source_channel = %s
                     AND (expires_at IS NULL OR expires_at > NOW())
                     AND COALESCE(needs_review, FALSE) = FALSE
                     AND importance >= 0.3
                   ORDER BY importance DESC, created_at DESC
                   LIMIT 15""",
                (agent_id, channel),
            )
            channel_rows = await ch_result.fetchall()

    # Score all candidates with continuous temporal decay
    scored_candidates = []
    for row in candidate_rows:
        tw = _compute_weight_from_row(row, now)
        scored_candidates.append((tw, row))

    # Sort by temporal weight (effective importance) descending
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # Also score channel-specific memories
    scored_channel = []
    for row in channel_rows:
        tw = _compute_weight_from_row(row, now)
        scored_channel.append((tw, row))
    scored_channel.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate and assemble within budget
    # Priority order: continuity (thread summaries) → top candidates → channel-specific
    seen_ids = set()
    selected = []

    # Continuity rows always first (thread summaries for conversation pickup)
    for row in continuity_rows:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            selected.append(row)

    # Top temporally-weighted candidates
    for tw, row in scored_candidates:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            selected.append(row)

    # Channel-specific memories fill remaining budget
    for tw, row in scored_channel:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            selected.append(row)

    if not selected:
        return ""

    # Build the memory block within budget
    lines = []
    char_count = 0
    header = "## Active Memory\nThese are things you remember from past conversations:\n"
    char_count += len(header)

    for mem in selected:
        cat_label = mem["category"].upper()
        tag_str = f" [{', '.join(mem['tags'])}]" if mem.get("tags") else ""
        line = f"- ({cat_label}{tag_str}) {mem['content']}"

        if char_count + len(line) + 1 > budget_chars:
            break

        lines.append(line)
        char_count += len(line) + 1

    if not lines:
        return ""

    # Update access counts for loaded memories
    loaded_ids = [s["id"] for s in selected[:len(lines)]]
    if loaded_ids:
        try:
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE agent_memory
                       SET access_count = access_count + 1,
                           last_accessed = NOW()
                       WHERE id = ANY(%s)""",
                    (loaded_ids,),
                )
        except Exception:
            pass  # Non-critical

    return header + "\n".join(lines)


async def load_crossfeed_memories(
    agent_id: str,
    current_channel: str,
    all_channels: list[str] | None = None,
    budget_chars: int = 2500,
) -> str:
    """Load recent memories from OTHER channels for cross-feed injection.

    Day and Deep are complementary:
      - Day is operations, tasks, what's happening now
      - Deep is vision, direction, long-term planning

    Each channel needs awareness of the other so decisions stay coherent.
    Day surfaces operational outcomes and decisions to Deep.
    Deep surfaces vision changes and direction shifts to Day.

    Returns a formatted string to append to the system prompt, or empty string.
    """
    if not all_channels:
        all_channels = ["day", "deep"]

    other_channels = [ch for ch in all_channels if ch != current_channel]
    if not other_channels:
        return ""

    memories_by_channel = {}

    async with get_db() as conn:
        for ch in other_channels:
            # Load more from the other channel, prioritize by importance
            result = await conn.execute(
                """SELECT content, category, importance, tags, created_at
                   FROM agent_memory
                   WHERE agent_id = %s AND is_active = TRUE
                     AND source_channel = %s
                     AND (expires_at IS NULL OR expires_at > NOW())
                     AND importance >= 0.4
                   ORDER BY importance DESC, created_at DESC
                   LIMIT 12""",
                (agent_id, ch),
            )
            rows = await result.fetchall()
            if rows:
                memories_by_channel[ch] = rows

    if not memories_by_channel:
        return ""

    # Channel relationship descriptions
    channel_desc = {
        "day": "operational decisions, task outcomes, and standing instructions",
        "deep": "vision, long-term direction, and how the operator thinks about their goals",
    }

    # Build cross-feed block within budget
    lines = []
    char_count = 0

    for ch, rows in memories_by_channel.items():
        desc = channel_desc.get(ch, "recent context")
        ch_header = f"\n### From {ch.capitalize()} channel ({desc}):\n"
        if char_count + len(ch_header) > budget_chars:
            break
        lines.append(ch_header)
        char_count += len(ch_header)

        for mem in rows:
            tag_str = f" [{', '.join(mem['tags'])}]" if mem.get("tags") else ""
            line = f"- ({mem['category'].upper()}{tag_str}) {mem['content']}"
            if char_count + len(line) + 1 > budget_chars:
                break
            lines.append(line)
            char_count += len(line) + 1

    if len(lines) <= 1:  # Only a header, no actual memories
        return ""

    header = "\n## Cross-Channel Context\nContext from your other channel — Day and Deep inform each other:\n"
    return header + "\n".join(lines)


# =============================================================================
# Serialization helpers
# =============================================================================

def _serialize_memory(row: dict) -> dict:
    """Convert a DB row to a clean dict for API responses."""
    if not row:
        return {}
    result = dict(row)
    for key in ("created_at", "updated_at", "last_accessed", "expires_at", "reviewed_at"):
        if key in result and result[key] is not None:
            result[key] = result[key].isoformat() if hasattr(result[key], "isoformat") else str(result[key])
    # Remove embedding from API responses (binary blob)
    result.pop("embedding", None)
    return result


def _serialize_thread(row: dict) -> dict:
    """Convert a thread DB row to a clean dict."""
    if not row:
        return {}
    result = dict(row)
    for key in ("created_at", "archived_at", "first_message_at", "last_message_at"):
        if key in result and result[key] is not None:
            result[key] = result[key].isoformat() if hasattr(result[key], "isoformat") else str(result[key])
    return result
