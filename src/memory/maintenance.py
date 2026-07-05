"""
Memory Maintenance — stats, expiry, review queue, and backfill operations.

Handles memory system health: statistics gathering, expired memory cleanup,
auto-commit of reviewed memories, review queue management, and embedding
backfill for memories that were stored before vector search was enabled.

Split from memory.py for maintainability. Core memory CRUD lives in memory.py;
thread lifecycle in threads.py.
"""

import logging

from src.memory.database import get_db
from src.memory.memory import _default_agent_id

logger = logging.getLogger("family.memory")


# =============================================================================
# Memory Stats
# =============================================================================

async def get_memory_stats(agent_id: str = None) -> dict:
    """Get memory system statistics."""
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        total = await conn.execute(
            "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s",
            (agent_id,),
        )
        total_row = await total.fetchone()

        active = await conn.execute(
            "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s AND is_active = TRUE",
            (agent_id,),
        )
        active_row = await active.fetchone()

        flagged = await conn.execute(
            "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s AND is_active = TRUE AND COALESCE(needs_review, FALSE) = TRUE",
            (agent_id,),
        )
        flagged_row = await flagged.fetchone()

        superseded = await conn.execute(
            "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s AND is_active = FALSE AND superseded_by IS NOT NULL",
            (agent_id,),
        )
        superseded_row = await superseded.fetchone()

        by_cat = await conn.execute(
            """SELECT category, COUNT(*) as c
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
               GROUP BY category
               ORDER BY c DESC""",
            (agent_id,),
        )
        cat_rows = await by_cat.fetchall()

        threads = await conn.execute(
            """SELECT status, COUNT(*) as c
               FROM chat_threads
               WHERE agent_id = %s
               GROUP BY status""",
            (agent_id,),
        )
        thread_rows = await threads.fetchall()

        # Recent corrections (last 30 days)
        corrections = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND supersedes IS NOT NULL
                 AND created_at > NOW() - INTERVAL '30 days'""",
            (agent_id,),
        )
        corrections_row = await corrections.fetchone()

    return {
        "total_memories": total_row["c"],
        "active_memories": active_row["c"],
        "flagged_count": flagged_row["c"],
        "superseded_count": superseded_row["c"],
        "recent_corrections": corrections_row["c"],
        "by_category": {r["category"]: r["c"] for r in cat_rows},
        "threads": {r["status"]: r["c"] for r in thread_rows},
    }


# =============================================================================
# Expiry maintenance
# =============================================================================

async def expire_old_memories(agent_id: str = None) -> int:
    """Deactivate memories past their expiry date. Returns count expired."""
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        result = await conn.execute(
            """UPDATE agent_memory
               SET is_active = FALSE, updated_at = NOW()
               WHERE agent_id = %s AND is_active = TRUE
                 AND expires_at IS NOT NULL AND expires_at <= NOW()
               RETURNING id""",
            (agent_id,),
        )
        rows = await result.fetchall()

    count = len(rows)
    if count:
        logger.info(f"Expired {count} memories for {agent_id}")
    return count


# =============================================================================
# Review window — auto-commit
# =============================================================================

async def auto_commit_reviewed(agent_id: str = None, days: int = 7) -> int:
    """Auto-commit memories older than `days` that haven't been reviewed.

    Marks them as reviewed (committed to long-term memory).
    Returns count committed.
    """
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        result = await conn.execute(
            """UPDATE agent_memory
               SET reviewed = TRUE, reviewed_at = NOW(), updated_at = NOW()
               WHERE agent_id = %s AND is_active = TRUE
                 AND reviewed = FALSE
                 AND created_at < NOW() - make_interval(days => %s)
               RETURNING id""",
            (agent_id, days),
        )
        rows = await result.fetchall()

    count = len(rows)
    if count:
        logger.info(f"Auto-committed {count} memories for {agent_id} (>{days} days old)")
    return count


async def get_review_queue_stats(agent_id: str = None) -> dict:
    """Get counts for review queue vs committed memories."""
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT
                 COUNT(*) FILTER (WHERE reviewed = FALSE) AS pending,
                 COUNT(*) FILTER (WHERE reviewed = TRUE) AS committed,
                 COUNT(*) AS total
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE""",
            (agent_id,),
        )
        row = await result.fetchone()
        return {
            "pending": row["pending"] or 0,
            "committed": row["committed"] or 0,
            "total": row["total"] or 0,
        }


# =============================================================================
# Embedding backfill — populate vectors for existing memories
# =============================================================================

async def backfill_embeddings(agent_id: str | None = None, batch_size: int = 20) -> dict:
    """Generate embeddings for active memories that don't have them yet.

    Processes in batches to avoid overwhelming Ollama. Call via manual API
    endpoint or run as a one-time migration task.

    Returns dict with counts: total_missing, embedded, failed.
    """
    agent_id = _default_agent_id(agent_id)

    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE AND embedding IS NULL
               ORDER BY importance DESC, created_at DESC
               LIMIT %s""",
            (agent_id, batch_size),
        )
        rows = await result.fetchall()

    if not rows:
        return {"total_missing": 0, "embedded": 0, "failed": 0}

    # Count total missing (not just this batch)
    async with get_db() as conn:
        count_result = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE AND embedding IS NULL""",
            (agent_id,),
        )
        total_missing = (await count_result.fetchone())["c"]

    embedded = 0
    failed = 0

    try:
        from src.memory.knowledge import get_embedding
    except ImportError:
        return {"total_missing": total_missing, "embedded": 0, "failed": len(rows),
                "error": "knowledge module not available"}

    for row in rows:
        try:
            emb = await get_embedding(row["content"])
            if emb:
                async with get_db() as conn:
                    await conn.execute(
                        "UPDATE agent_memory SET embedding = %s WHERE id = %s",
                        (str(emb), row["id"]),
                    )
                embedded += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"Backfill embedding failed for #{row['id']}: {e}")
            failed += 1

    remaining = total_missing - embedded
    logger.info(f"Embedding backfill: {embedded} embedded, {failed} failed, {remaining} remaining")

    return {
        "total_missing": total_missing,
        "embedded": embedded,
        "failed": failed,
        "remaining": remaining,
    }


async def backfill_all_embeddings(batch_size: int = 20, max_batches: int = 25) -> dict:
    """Bounded nightly sweep (audit C3-9): backfill missing embeddings for EVERY
    agent. Memories written while Ollama was still pulling models (exactly the
    fresh-box window when the wizard generates the first memories) land with
    embedding NULL and semantic recall silently skips them — the cure existed
    (backfill_embeddings) but was only reachable via a manual endpoint. Called
    from the 01:00 memory-consolidation job. Never raises past the DB layer."""
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT DISTINCT agent_id FROM agent_memory
               WHERE is_active = TRUE AND embedding IS NULL""")
        agents = [row["agent_id"] for row in await result.fetchall()]

    totals = {"agents": len(agents), "embedded": 0, "failed": 0, "remaining": 0}
    for aid in agents:
        remaining = 0
        for _ in range(max_batches):
            res = await backfill_embeddings(aid, batch_size=batch_size)
            totals["embedded"] += res.get("embedded", 0)
            totals["failed"] += res.get("failed", 0)
            remaining = res.get("remaining", 0)
            if res.get("embedded", 0) == 0 or remaining <= 0:
                break   # done, or the embed backend is down — stop burning the batch budget
        totals["remaining"] += max(remaining, 0)
    return totals
