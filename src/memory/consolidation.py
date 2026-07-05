"""
Memory Consolidation — Ezra's curation pass over agent memories.

COVE-CORE: This runs for any agent. Keeps memory clean, deduplicated,
and organized so the agent's context stays sharp over time.

What it does:
  1. Scans all active memories for duplicates (similar content, same category)
  2. Merges related memories into single stronger entries
  3. Deactivates superseded/stale memories
  4. Prunes low-importance memories that haven't been accessed in 30+ days
  5. Logs what it did for operator review

  SYNTHESIS (weekly):
  6. Clusters related memories by pgvector similarity
  7. LLM extracts higher-level patterns from each cluster
  8. Stores synthesis as high-importance memories (category='synthesis')

Triggered:
  - Daily at 01:00 (after auto-commit at 00:05) — dedup + prune
  - Weekly Sundays at 02:00 — synthesis pass (after dedup runs first)
  - Can also be triggered manually via API

This is the difference between an agent that accumulates noise and one
that builds clean, reliable knowledge over time. Without consolidation,
memory extraction creates duplicates on every thread rotation — the same
facts restated slightly differently, piling up until the budget is full
of redundancy and the agent can't remember what matters.

Without synthesis, the agent accumulates raw observations but never
extracts patterns. It gets better at recalling individual facts but
never develops higher-level understanding of recurring themes.
"""

import json
import logging
from datetime import datetime, timezone

from src.memory.database import get_db
from src.memory.memory import store_memory, update_memory, _default_agent_id
from src.config import get_primary_agent_id

logger = logging.getLogger("family.consolidation")


def _ts():
    return datetime.now(timezone.utc).strftime("[%Y-%m-%d %H:%M:%S UTC]")


async def run_memory_consolidation(agent_id: str | None = None) -> dict:
    """Main entry point — consolidate agent memories.

    Steps:
      1. Load all active memories grouped by category
      2. Use LLM to identify duplicates and merge opportunities
      3. Merge duplicates (keep highest importance, deactivate rest)
      4. Prune stale low-importance memories (>30 days, never accessed, low importance)
      5. Return summary of actions taken

    Returns dict with consolidation results.
    """
    agent_id = _default_agent_id(agent_id)
    print(f"{_ts()} [consolidation] Starting memory consolidation for {agent_id}...")

    results = {
        "agent_id": agent_id,
        "duplicates_merged": 0,
        "stale_pruned": 0,
        "total_before": 0,
        "total_after": 0,
        "categories_processed": [],
        "errors": [],
    }

    try:
        # Count before
        async with get_db() as conn:
            count_result = await conn.execute(
                "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s AND is_active = TRUE",
                (agent_id,),
            )
            row = await count_result.fetchone()
            results["total_before"] = row["c"]

        # Step 1: Merge duplicates within each category
        merged = await _merge_duplicate_memories(agent_id)
        results["duplicates_merged"] = merged["merged_count"]
        results["categories_processed"] = merged["categories"]
        if merged.get("errors"):
            results["errors"].extend(merged["errors"])

        # Step 2: Prune stale memories
        pruned = await _prune_stale_memories(agent_id)
        results["stale_pruned"] = pruned

        # Count after
        async with get_db() as conn:
            count_result = await conn.execute(
                "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s AND is_active = TRUE",
                (agent_id,),
            )
            row = await count_result.fetchone()
            results["total_after"] = row["c"]

        print(f"{_ts()} [consolidation] Complete for {agent_id}: "
              f"{results['total_before']} → {results['total_after']} memories "
              f"({results['duplicates_merged']} merged, {results['stale_pruned']} pruned)")

    except Exception as e:
        results["errors"].append(str(e))
        print(f"{_ts()} [consolidation] ERROR: {e}")

    return results


async def _merge_duplicate_memories(agent_id: str) -> dict:
    """Find and merge duplicate memories using LLM analysis.

    Groups memories by category, then asks the LLM to identify clusters
    of memories that say essentially the same thing. The highest-importance
    memory in each cluster is kept (or a merged version is created), and
    the rest are deactivated.

    Only processes categories with 5+ memories to avoid unnecessary LLM calls.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    result = {"merged_count": 0, "categories": [], "errors": []}

    # Get categories with enough memories to be worth consolidating
    async with get_db() as conn:
        cat_counts = await conn.execute(
            """SELECT category, COUNT(*) as c
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND (expires_at IS NULL OR expires_at > NOW())
               GROUP BY category
               HAVING COUNT(*) >= 5
               ORDER BY c DESC""",
            (agent_id,),
        )
        categories = await cat_counts.fetchall()

    if not categories:
        print(f"{_ts()} [consolidation] No categories with 5+ memories — skipping merge")
        return result

    for cat_row in categories:
        category = cat_row["category"]
        count = cat_row["c"]

        try:
            merged = await _consolidate_category(agent_id, category)
            result["merged_count"] += merged
            result["categories"].append({"category": category, "count": count, "merged": merged})
            if merged:
                print(f"{_ts()} [consolidation]   {category}: {count} memories → merged {merged} duplicates")
        except Exception as e:
            err = f"{category}: {e}"
            result["errors"].append(err)
            print(f"{_ts()} [consolidation]   ERROR {err}")

    return result


async def _consolidate_category(agent_id: str, category: str) -> int:
    """Consolidate memories within a single category.

    Returns count of memories deactivated (merged into others).
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    # Load all active memories for this category
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, content, importance, tags, access_count, created_at
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE AND category = %s
                 AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY importance DESC, created_at DESC""",
            (agent_id, category),
        )
        memories = await result.fetchall()

    if len(memories) < 5:
        return 0

    # Build memory list for LLM analysis (cap at 60 to stay in context)
    mem_lines = []
    for m in memories[:60]:
        mem_lines.append(f"[{m['id']}] (imp={m['importance']}, accessed={m['access_count']}x) {m['content']}")

    mem_text = "\n".join(mem_lines)

    prompt = f"""You are a memory consolidation system. Below are {len(mem_lines)} memories in the "{category}" category for an AI agent.

Your job: identify DUPLICATE or REDUNDANT memories — ones that say essentially the same thing in different words. Group them into clusters.

For each cluster of duplicates:
- Pick the BEST version (clearest, most complete, highest importance)
- List the IDs of memories that should be DEACTIVATED (the redundant copies)

Rules:
- Only group memories that truly overlap in meaning. Similar topic ≠ duplicate.
- A memory that adds NEW information to a similar topic is NOT a duplicate — keep both.
- When in doubt, keep both. False merges lose knowledge. False keeps just use a bit more budget.
- Never deactivate the last memory on a topic. There must always be at least one survivor.

Output ONLY a JSON array of objects, each with:
- "keep_id": the ID of the best version to keep
- "deactivate_ids": array of IDs to deactivate (the redundant copies)
- "reason": brief explanation of why these are duplicates

If there are NO duplicates, return an empty array: []

MEMORIES:
{mem_text}

JSON output:"""

    try:
        response = await invoke_with_fallback(
            [
                SystemMessage(content="You identify duplicate memories. Output only JSON. Be conservative — only flag clear duplicates."),
                HumanMessage(content=prompt),
            ],
            temperature=0.2,
            label=f"{agent_id}/memory-consolidation/{category}",
            agent_id=agent_id,
            operation_type="memory",
        )

        # Parse response
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        clusters = json.loads(text)
        if not isinstance(clusters, list):
            return 0

        # Process each cluster
        deactivated = 0
        valid_ids = {m["id"] for m in memories}

        for cluster in clusters:
            keep_id = cluster.get("keep_id")
            deactivate_ids = cluster.get("deactivate_ids", [])
            reason = cluster.get("reason", "duplicate")

            if not keep_id or not deactivate_ids:
                continue

            # Validate all IDs exist in our memory set
            if keep_id not in valid_ids:
                continue
            deactivate_ids = [d for d in deactivate_ids if d in valid_ids and d != keep_id]
            if not deactivate_ids:
                continue

            # Deactivate the duplicates
            async with get_db() as conn:
                for dup_id in deactivate_ids:
                    await conn.execute(
                        """UPDATE agent_memory
                           SET is_active = FALSE,
                               source_summary = COALESCE(source_summary, '') || %s,
                               updated_at = NOW()
                           WHERE id = %s AND agent_id = %s AND is_active = TRUE""",
                        (f" [consolidated: duplicate of #{keep_id} — {reason}]",
                         dup_id, agent_id),
                    )
                    deactivated += 1

            logger.info(f"Consolidated: keep #{keep_id}, deactivated {deactivate_ids} ({reason})")

        return deactivated

    except json.JSONDecodeError as e:
        logger.warning(f"Consolidation JSON parse failed for {category}: {e}")
        return 0
    except Exception as e:
        logger.warning(f"Consolidation failed for {category}: {e}")
        raise


async def _prune_stale_memories(agent_id: str) -> int:
    """Prune memories that are old, low-importance, and never accessed.

    Criteria for pruning:
    - Created more than 30 days ago
    - Importance <= 0.3
    - Never accessed (access_count = 0)
    - Not flagged for review

    These are minor observations that were extracted but never useful.
    Deactivates them (soft delete — recoverable if needed).
    """
    async with get_db() as conn:
        result = await conn.execute(
            """UPDATE agent_memory
               SET is_active = FALSE,
                   source_summary = COALESCE(source_summary, '') || ' [pruned: stale low-importance, never accessed]',
                   updated_at = NOW()
               WHERE agent_id = %s
                 AND is_active = TRUE
                 AND importance <= 0.3
                 AND access_count = 0
                 AND created_at < NOW() - INTERVAL '30 days'
                 AND COALESCE(needs_review, FALSE) = FALSE
               RETURNING id""",
            (agent_id,),
        )
        rows = await result.fetchall()

    count = len(rows)
    if count:
        logger.info(f"Pruned {count} stale memories for {agent_id}")
    return count


async def get_consolidation_stats(agent_id: str | None = None) -> dict:
    """Get current memory health stats — useful for monitoring."""
    agent_id = _default_agent_id(agent_id)

    async with get_db() as conn:
        # Total active
        r = await conn.execute(
            "SELECT COUNT(*) as c FROM agent_memory WHERE agent_id = %s AND is_active = TRUE",
            (agent_id,),
        )
        total = (await r.fetchone())["c"]

        # By category
        r = await conn.execute(
            """SELECT category, COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
               GROUP BY category ORDER BY c DESC""",
            (agent_id,),
        )
        by_cat = {row["category"]: row["c"] for row in await r.fetchall()}

        # Stale candidates
        r = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND importance <= 0.3 AND access_count = 0
                 AND created_at < NOW() - INTERVAL '30 days'""",
            (agent_id,),
        )
        stale = (await r.fetchone())["c"]

        # Recent extractions (last 7 days)
        r = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND created_at > NOW() - INTERVAL '7 days'""",
            (agent_id,),
        )
        recent = (await r.fetchone())["c"]

        # Consolidated (deactivated by consolidation)
        r = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND is_active = FALSE
                 AND source_summary LIKE '%%consolidated%%'""",
            (agent_id,),
        )
        consolidated = (await r.fetchone())["c"]

        # Synthesis count
        r = await conn.execute(
            """SELECT COUNT(*) as c FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND category = 'synthesis'""",
            (agent_id,),
        )
        synthesis_count = (await r.fetchone())["c"]

    return {
        "total_active": total,
        "by_category": by_cat,
        "stale_candidates": stale,
        "recent_7d": recent,
        "previously_consolidated": consolidated,
        "synthesis_memories": synthesis_count,
    }


# =============================================================================
# Synthesis Consolidation — pattern extraction from memory clusters
# =============================================================================

async def run_memory_synthesis(agent_id: str | None = None, min_cluster_size: int = 3) -> dict:
    """Extract higher-level patterns from clusters of related memories.

    This is Ezra's synthesis pass — the step that turns raw memory accumulation
    into genuine understanding. Instead of 50 individual memories about deploy
    issues, the agent gets one synthesis memory: "Deploy process is fragile
    around overlay merging — consider automated overlay audit pre-deploy."

    Process:
      1. Load all active non-synthesis memories with embeddings
      2. Cluster by pgvector cosine similarity (threshold 0.75)
      3. For clusters with min_cluster_size+ memories, ask LLM to extract patterns
      4. Store each pattern as a high-importance 'synthesis' memory
      5. Don't re-synthesize clusters that already have a matching synthesis

    Returns dict with synthesis results.
    """
    agent_id = _default_agent_id(agent_id)
    print(f"{_ts()} [synthesis] Starting memory synthesis for {agent_id}...")

    results = {
        "agent_id": agent_id,
        "clusters_found": 0,
        "clusters_synthesized": 0,
        "synthesis_memories_created": 0,
        "clusters_skipped_existing": 0,
        "errors": [],
    }

    try:
        # Step 1: Find clusters of related memories using pgvector
        clusters = await _find_memory_clusters(agent_id, min_cluster_size)
        results["clusters_found"] = len(clusters)

        if not clusters:
            print(f"{_ts()} [synthesis] No clusters found with {min_cluster_size}+ memories — done")
            return results

        # Step 2: For each cluster, check if synthesis already exists, then extract pattern
        for cluster in clusters:
            try:
                # Check if we already have a synthesis covering these memories
                if await _synthesis_exists_for_cluster(agent_id, cluster):
                    results["clusters_skipped_existing"] += 1
                    continue

                # Extract pattern from cluster
                synthesis = await _extract_cluster_pattern(agent_id, cluster)
                if synthesis:
                    # Store as high-importance synthesis memory
                    source_ids = [m["id"] for m in cluster["memories"]]
                    await store_memory(
                        content=synthesis["content"],
                        category="synthesis",
                        importance=0.85,
                        tags=synthesis.get("tags", []) + ["auto-synthesis"],
                        agent_id=agent_id,
                        source_summary=f"Synthesized from {len(source_ids)} memories: {source_ids[:10]}",
                    )
                    results["synthesis_memories_created"] += 1
                    results["clusters_synthesized"] += 1
                    print(f"{_ts()} [synthesis]   Created synthesis from {len(source_ids)} memories: "
                          f"{synthesis['content'][:80]}...")

            except Exception as e:
                err = f"Cluster synthesis failed: {e}"
                results["errors"].append(err)
                print(f"{_ts()} [synthesis]   ERROR: {err}")

        print(f"{_ts()} [synthesis] Complete for {agent_id}: "
              f"{results['clusters_found']} clusters found, "
              f"{results['clusters_synthesized']} synthesized, "
              f"{results['clusters_skipped_existing']} already had synthesis")

    except Exception as e:
        results["errors"].append(str(e))
        print(f"{_ts()} [synthesis] ERROR: {e}")

    return results


async def _find_memory_clusters(agent_id: str, min_size: int = 3) -> list[dict]:
    """Find clusters of semantically related memories using pgvector.

    Strategy: for each memory with an embedding, find its nearest neighbors
    above the similarity threshold. Then merge overlapping neighbor sets
    into connected components (clusters).

    Returns list of cluster dicts, each with:
      - centroid_id: the memory with most connections
      - memories: list of memory dicts in the cluster
      - avg_similarity: average pairwise similarity
    """
    SIMILARITY_THRESHOLD = 0.75  # High bar — only truly related memories

    async with get_db() as conn:
        # Get all active non-synthesis memories that have embeddings
        result = await conn.execute(
            """SELECT id, content, category, importance, tags, created_at, embedding
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND category != 'synthesis'
                 AND category != 'context'
                 AND embedding IS NOT NULL
               ORDER BY created_at DESC
               LIMIT 500""",
            (agent_id,),
        )
        memories = await result.fetchall()

    if len(memories) < min_size:
        return []

    # Build adjacency: for each memory, find neighbors above threshold
    # We'll use a union-find approach to merge overlapping groups
    mem_by_id = {m["id"]: m for m in memories}
    mem_ids = list(mem_by_id.keys())

    # Find pairwise similarities using pgvector
    adjacency = {}  # id -> set of neighbor ids
    for mem in memories:
        adjacency[mem["id"]] = set()

    # Query nearest neighbors for each memory in batches
    async with get_db() as conn:
        for mem in memories:
            if mem["embedding"] is None:
                continue
            result = await conn.execute(
                """SELECT id, 1 - (embedding <=> %s::vector) as similarity
                   FROM agent_memory
                   WHERE agent_id = %s AND is_active = TRUE
                     AND category != 'synthesis'
                     AND category != 'context'
                     AND embedding IS NOT NULL
                     AND id != %s
                   ORDER BY embedding <=> %s::vector
                   LIMIT 10""",
                (str(mem["embedding"]), agent_id, mem["id"], str(mem["embedding"])),
            )
            neighbors = await result.fetchall()
            for n in neighbors:
                if n["similarity"] >= SIMILARITY_THRESHOLD:
                    adjacency[mem["id"]].add(n["id"])
                    if n["id"] in adjacency:
                        adjacency[n["id"]].add(mem["id"])

    # Union-find to merge connected components
    parent = {mid: mid for mid in mem_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for mid, neighbors in adjacency.items():
        for nid in neighbors:
            union(mid, nid)

    # Group into clusters
    from collections import defaultdict
    groups = defaultdict(list)
    for mid in mem_ids:
        root = find(mid)
        groups[root].append(mid)

    # Filter to clusters meeting minimum size, build result
    clusters = []
    for root, member_ids in groups.items():
        if len(member_ids) < min_size:
            continue

        cluster_mems = []
        for mid in member_ids:
            m = mem_by_id[mid]
            cluster_mems.append({
                "id": m["id"],
                "content": m["content"],
                "category": m["category"],
                "importance": m["importance"],
                "tags": m["tags"] or [],
                "created_at": m["created_at"].isoformat() if m["created_at"] else None,
            })

        # Find centroid (most connections)
        centroid_id = max(member_ids, key=lambda mid: len(adjacency.get(mid, set())))

        clusters.append({
            "centroid_id": centroid_id,
            "memories": cluster_mems,
            "size": len(cluster_mems),
        })

    # Sort by cluster size descending
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return clusters


async def _synthesis_exists_for_cluster(agent_id: str, cluster: dict) -> bool:
    """Check if a synthesis memory already covers this cluster's topic.

    Uses semantic similarity: if any existing synthesis memory is very
    similar (>0.8) to the cluster's centroid, skip this cluster.
    """
    centroid_id = cluster["centroid_id"]

    async with get_db() as conn:
        # Get centroid's embedding
        result = await conn.execute(
            "SELECT embedding FROM agent_memory WHERE id = %s",
            (centroid_id,),
        )
        centroid = await result.fetchone()
        if not centroid or centroid["embedding"] is None:
            return False

        # Check if any synthesis memory is very similar
        result = await conn.execute(
            """SELECT id, 1 - (embedding <=> %s::vector) as similarity
               FROM agent_memory
               WHERE agent_id = %s AND is_active = TRUE
                 AND category = 'synthesis'
                 AND embedding IS NOT NULL
               ORDER BY embedding <=> %s::vector
               LIMIT 1""",
            (str(centroid["embedding"]), agent_id, str(centroid["embedding"])),
        )
        match = await result.fetchone()
        if match and match["similarity"] >= 0.80:
            return True

    return False


async def _extract_cluster_pattern(agent_id: str, cluster: dict) -> dict | None:
    """Use LLM to extract a higher-level pattern from a memory cluster.

    Returns dict with 'content' and 'tags', or None if no pattern found.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    # Build memory text for LLM (cap at 20 per cluster to stay in context)
    mem_lines = []
    for m in cluster["memories"][:20]:
        mem_lines.append(f"- [{m['category']}] {m['content']}")
    mem_text = "\n".join(mem_lines)

    prompt = f"""Below are {len(cluster['memories'])} related memories from an AI agent's knowledge base. They were clustered because they share semantic similarity.

Your job: extract ONE higher-level insight or pattern that these memories collectively reveal. This should be something that isn't obvious from any single memory alone — a recurring theme, a systemic pattern, a lesson learned across multiple instances.

Rules:
- The synthesis should be 1-3 sentences. Dense and actionable.
- It must go BEYOND restating what the individual memories say. It should reveal something about the pattern.
- If the memories are about the same topic but don't reveal a deeper pattern, return "NO_PATTERN" — not everything needs synthesizing.
- Include 1-3 tags that capture the theme (single words or short phrases).

MEMORIES:
{mem_text}

Respond in this exact JSON format:
{{"content": "the synthesis insight", "tags": ["tag1", "tag2"]}}

Or if no meaningful pattern exists:
"NO_PATTERN"

JSON output:"""

    try:
        response = await invoke_with_fallback(
            [
                SystemMessage(content="You extract patterns from memory clusters. Output only JSON or NO_PATTERN. Be selective — only synthesize when there's a genuine higher-level insight."),
                HumanMessage(content=prompt),
            ],
            temperature=0.3,
            label=f"{agent_id}/memory-synthesis",
            agent_id=agent_id,
            operation_type="memory",
        )

        text = response.strip()
        if "NO_PATTERN" in text:
            return None

        # Parse JSON
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)
        if not isinstance(result, dict) or "content" not in result:
            return None

        return {
            "content": result["content"],
            "tags": result.get("tags", []),
        }

    except json.JSONDecodeError:
        logger.warning(f"Synthesis JSON parse failed for cluster (centroid={cluster['centroid_id']})")
        return None
    except Exception as e:
        logger.warning(f"Synthesis extraction failed: {e}")
        raise


async def run_full_consolidation(agent_id: str | None = None) -> dict:
    """Run the complete consolidation pipeline: dedup + prune + synthesis.

    This is the weekly pass. Daily runs should use run_memory_consolidation()
    (dedup + prune only). Weekly runs add the synthesis step.
    """
    agent_id = _default_agent_id(agent_id)
    print(f"{_ts()} [consolidation] Starting FULL consolidation (dedup + prune + synthesis) for {agent_id}...")

    # Step 1: Run standard dedup + prune
    consolidation_result = await run_memory_consolidation(agent_id)

    # Step 2: Run synthesis
    synthesis_result = await run_memory_synthesis(agent_id)

    return {
        "consolidation": consolidation_result,
        "synthesis": synthesis_result,
    }
