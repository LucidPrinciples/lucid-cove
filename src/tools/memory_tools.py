"""
Memory Tools — agent's ability to remember things across conversations.

These tools let the agent actively manage persistent memory during conversation:
  - save_memory: Store something worth remembering
  - recall_memory: Pull up memories by category or topic
  - search_memory: Find specific memories by keyword (ILIKE)
  - memory_search: Find by MEANING across agent_memory + vault Archive (#D54)
  - memory_get: Load one hit by ref from memory_search (#D54)
  - update_memory: Correct or update an existing memory
  - correct_memory: Handle operator corrections ("actually it's X not Y")

All memory reads are AUTO tier (no approval needed).
Memory writes are NOTIFY tier (logged to Mission Control but don't block).

CRITICAL BEHAVIOR — Correction Detection:
  When the operator says things like "actually...", "no, it's...", "that's wrong",
  "update that to...", or "it's not X, it's Y" — the agent should:
    1. Use search_memory to find the relevant existing memory
    2. Use correct_memory to supersede it with the corrected version
    3. Confirm the correction to the operator
  This keeps memory accurate without manual dashboard editing.
"""

from langchain_core.tools import tool

from src.tools.approval import auto, notify
from src.memory.memory import (
    store_memory,
    recall_memories,
    recall_memories_by_time,
    search_memories,
    get_memory as get_memory_service,
    update_memory as update_memory_service,
    correct_memory as correct_memory_service,
    VALID_CATEGORIES,
)


# =============================================================================
# Save — store a new memory
# =============================================================================

@notify
@tool
async def save_memory(
    content: str,
    category: str = "general",
    importance: float = 0.5,
    tags: str = "",
) -> str:
    """Save something to persistent memory so you remember it in future conversations.

    Use this whenever the operator shares important information, makes a decision,
    gives an instruction, or when you learn something worth keeping.

    Args:
        content: What to remember. Write as a clear standalone statement.
                 Bad: "He said yes" Good: "Operator approved moving DNS to Cloudflare"
        category: Type of memory. One of: decision, fact, preference, person,
                  project, technical, observation, instruction, general
        importance: How important (0.0-1.0). Use 0.8+ for decisions and instructions
                    from the operator. Use 0.5 for general facts. Use 0.3 for minor notes.
        tags: Comma-separated tags for easier retrieval (e.g. "dns,infrastructure,cloudflare")
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    result = await store_memory(
        content=content,
        category=category,
        importance=importance,
        tags=tag_list,
    )

    return (
        f"Memory saved (#{result['id']}): [{category}] {content[:100]}"
        f"{'...' if len(content) > 100 else ''}"
    )


# =============================================================================
# Recall — retrieve memories by category
# =============================================================================

@auto
@tool
async def recall_memory(
    category: str = "",
    tags: str = "",
    min_importance: float = 0.0,
    limit: int = 10,
) -> str:
    """Recall memories from your persistent knowledge.

    Use this when you need context about past conversations, decisions,
    or facts that were discussed before.

    Args:
        category: Filter by category (decision, fact, preference, person,
                  project, technical, observation, instruction, general).
                  Leave empty for all categories.
        tags: Comma-separated tags to filter by (e.g. "infrastructure,deploy")
        min_importance: Minimum importance threshold (0.0-1.0)
        limit: Maximum memories to return (default 10)
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    memories = await recall_memories(
        category=category or None,
        tags=tag_list,
        min_importance=min_importance,
        limit=limit,
    )

    if not memories:
        return "No memories found matching those criteria."

    lines = [f"Found {len(memories)} memories:\n"]
    for m in memories:
        cat = m.get("category", "general").upper()
        importance = m.get("importance", 0.5)
        tags_str = f" [{', '.join(m.get('tags', []))}]" if m.get("tags") else ""
        created = m.get("created_at", "")[:10] if m.get("created_at") else ""
        lines.append(
            f"  #{m['id']} ({cat}, {importance:.1f}{tags_str}) [{created}]: "
            f"{m['content']}"
        )

    return "\n".join(lines)


# =============================================================================
# Search — find memories by keyword
# =============================================================================

@auto
@tool
async def search_memory(query: str, category: str = "", limit: int = 10) -> str:
    """Search your memories for specific keywords or topics.

    Use this when you're looking for something specific you remember
    but don't know the exact category or when it was saved.

    Args:
        query: What to search for (keywords, names, topics)
        category: Optionally limit search to a category
        limit: Maximum results (default 10)
    """
    memories = await search_memories(
        query=query,
        category=category or None,
        limit=limit,
    )

    if not memories:
        return f"No memories found matching '{query}'."

    lines = [f"Found {len(memories)} memories matching '{query}':\n"]
    for m in memories:
        cat = m.get("category", "general").upper()
        importance = m.get("importance", 0.5)
        lines.append(
            f"  #{m['id']} ({cat}, {importance:.1f}): {m['content']}"
        )

    return "\n".join(lines)


# =============================================================================
# Update — modify an existing memory
# =============================================================================

@notify
@tool
async def update_existing_memory(
    memory_id: int,
    content: str = "",
    importance: float = -1,
    category: str = "",
    tags: str = "",
) -> str:
    """Update an existing memory with corrected or expanded information.

    Use this when information has changed or when you need to correct
    a memory that was saved with wrong details.

    Args:
        memory_id: The ID of the memory to update (from recall or search results)
        content: New content (leave empty to keep existing)
        importance: New importance score (use -1 to keep existing)
        category: New category (leave empty to keep existing)
        tags: New comma-separated tags (leave empty to keep existing)
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    result = await update_memory_service(
        memory_id=memory_id,
        content=content or None,
        category=category or None,
        importance=importance if importance >= 0 else None,
        tags=tag_list,
    )

    if not result:
        return f"Memory #{memory_id} not found or no changes applied."

    return f"Memory #{memory_id} updated successfully."


# =============================================================================
# Correct — handle operator corrections ("actually it's X not Y")
# =============================================================================

@notify
@tool
async def correct_memory_tool(
    memory_id: int,
    corrected_content: str,
    reason: str = "",
) -> str:
    """Correct a memory when the operator provides updated information.

    Use this when the operator says things like:
      - "actually it's X not Y"
      - "no, that's wrong, it should be..."
      - "update that — the real answer is..."
      - "that changed — now it's..."

    This creates a new corrected memory and deactivates the old one,
    preserving the history chain. The operator doesn't need to manually
    edit anything — just tell you what's different.

    WORKFLOW:
      1. Operator says something that corrects existing knowledge
      2. You search_memory to find the relevant old memory
      3. You call correct_memory_tool with the old ID and new content
      4. Confirm the correction to the operator

    Args:
        memory_id: The ID of the memory to correct (from search/recall results)
        corrected_content: The new, corrected version of the memory
        reason: Brief explanation of what changed and why
    """
    result = await correct_memory_service(
        memory_id=memory_id,
        new_content=corrected_content,
        reason=reason,
    )

    if not result:
        return f"Memory #{memory_id} not found or already inactive."

    return (
        f"Memory corrected: #{memory_id} superseded by #{result['id']}. "
        f"New content: {corrected_content[:150]}"
        f"{'...' if len(corrected_content) > 150 else ''}"
    )


# =============================================================================
# Temporal recall — "what happened this week?"
# =============================================================================

@auto
@tool
async def recall_recent(
    timeframe: str = "7 days",
    category: str = "",
    limit: int = 20,
) -> str:
    """Recall memories from a specific time period.

    Use this when the operator asks things like:
      - "What happened this week?"
      - "What did we decide yesterday?"
      - "What have I saved in the last month?"
      - "Show me memories from May"

    Args:
        timeframe: How far back to look. Supports:
                   - "today", "yesterday"
                   - "3 days", "1 week", "2 weeks", "1 month"
                   - A specific date like "2026-05-01"
        category: Optional category filter (decision, fact, preference, etc.)
        limit: Maximum memories to return (default 20)
    """
    # Normalize common phrases
    tf = timeframe.lower().strip()
    since = None
    until = None

    if tf in ("today",):
        since = "today"
    elif tf in ("yesterday",):
        since = "yesterday"
        until = "today"
    elif tf.startswith("this week"):
        since = "7 days"
    elif tf.startswith("this month"):
        since = "30 days"
    elif tf.startswith("last week"):
        since = "14 days ago"
        until = None  # Will need special handling
        # Simplify: just go 14 days back, limit will keep it reasonable
        since = "14 days"
    elif tf.startswith("last month"):
        since = "60 days"
    else:
        # Could be "7 days", "2 weeks", "1 month", or a date
        if any(unit in tf for unit in ("day", "week", "month", "hour")):
            since = tf
        else:
            # Assume it's a date
            since = tf

    # Normalize "X days/weeks" to "X days/weeks ago" format for the service
    if since and since not in ("today", "yesterday") and "ago" not in since:
        if any(unit in since for unit in ("day", "week", "month", "hour")):
            since = f"{since} ago"

    memories = await recall_memories_by_time(
        since=since,
        until=until,
        category=category or None,
        limit=limit,
    )

    if not memories:
        return f"No memories found in the timeframe: {timeframe}"

    lines = [f"Found {len(memories)} memories from {timeframe}:\n"]
    for m in memories:
        cat = m.get("category", "general").upper()
        importance = m.get("importance", 0.5)
        tags_str = f" [{', '.join(m.get('tags', []))}]" if m.get("tags") else ""
        created = m.get("created_at", "")[:16] if m.get("created_at") else ""
        lines.append(
            f"  #{m['id']} ({cat}, {importance:.1f}{tags_str}) [{created}]: "
            f"{m['content']}"
        )

    return "\n".join(lines)


# =============================================================================
# Semantic search — meaning over keyword (#D54)
# =============================================================================

@auto
@tool
async def memory_search(
    query: str,
    source: str = "all",
    limit: int = 8,
) -> str:
    """Search memories and the vault session archive by MEANING (semantic).

    Prefer this over search_memory when you need continuity across compaction
    or past sessions and don't know exact keywords/tags. Uses the same
    embedding backend as the knowledge base (nomic-embed-text / cloud 768-dim).

    Args:
        query: Natural-language question or topic (e.g. "what did we decide
               about Haven nesting?" not just a tag).
        source: Where to look — 'all' (default), 'memories' (agent_memory only),
                or 'archive' (vault Archive/session-log + dated session files).
        limit: Max results (default 8).
    """
    from src.memory.archive_index import search_memory_unified

    results = await search_memory_unified(
        query=query,
        source=source,
        limit=max(1, min(int(limit or 8), 20)),
    )
    if not results:
        return (
            f"No semantic hits for '{query}' (source={source}). "
            "Index may still be building, embeddings may be off, or try broader wording."
        )

    lines = [f"Found {len(results)} semantic hits for '{query}' (source={source}):\n"]
    for r in results:
        sim = r.get("similarity") or r.get("score") or 0
        if r.get("source") == "memory":
            cat = (r.get("category") or "general").upper()
            tags = r.get("tags") or []
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(
                f"  {r['ref']} ({cat}, sim={sim:.3f}{tag_str}): {r.get('content', '')}"
            )
        else:
            title = r.get("title") or r.get("doc_name") or "archive"
            path = r.get("path") or ""
            sess = r.get("session_num")
            sess_bit = f" session={sess}" if sess else ""
            snippet = (r.get("content") or "")[:400]
            lines.append(
                f"  {r['ref']} (ARCHIVE sim={sim:.3f}{sess_bit} {path} · {title}):\n"
                f"    {snippet}{'…' if len(r.get('content') or '') > 400 else ''}"
            )
    lines.append(
        "\nUse memory_get with a ref (memory:123 or archive:_archive_s12#0) "
        "to load the full entry."
    )
    return "\n".join(lines)


@auto
@tool
async def memory_get(ref: str) -> str:
    """Load one memory or archive chunk by ref from memory_search.

    Args:
        ref: Either ``memory:123`` (agent_memory id) or
             ``archive:_archive_s12#0`` / ``archive:_archive_file:….md#0``.
             Bare integers are treated as memory ids.
    """
    raw = (ref or "").strip()
    if not raw:
        return "ref is required (memory:ID or archive:doc#chunk)."

    # Bare integer → memory
    if raw.isdigit():
        raw = f"memory:{raw}"

    if raw.startswith("memory:"):
        try:
            mid = int(raw.split(":", 1)[1])
        except ValueError:
            return f"Invalid memory ref: {ref}"
        mem = await get_memory_service(mid)
        if not mem:
            return f"Memory #{mid} not found."
        tags = mem.get("tags") or []
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        active = "active" if mem.get("is_active", True) else "inactive"
        return (
            f"memory:{mid} [{(mem.get('category') or 'general').upper()}, "
            f"{mem.get('importance', 0.5)}, {active}]{tag_str}\n"
            f"{mem.get('content', '')}\n"
            f"created={mem.get('created_at', '')} "
            f"source={mem.get('source_channel') or '—'} "
            f"{mem.get('source_summary') or ''}"
        ).strip()

    if raw.startswith("archive:") or raw.startswith("_archive"):
        from src.memory.archive_index import get_archive_chunk

        chunk = await get_archive_chunk(raw if raw.startswith("archive:") else f"archive:{raw}")
        if not chunk:
            return f"Archive chunk not found for ref: {ref}"
        meta = chunk.get("metadata") or {}
        header = (
            f"{chunk['ref']}\n"
            f"path={chunk.get('path') or meta.get('path') or '—'} "
            f"session={chunk.get('session_num') or meta.get('source_session') or '—'} "
            f"date={chunk.get('session_date') or meta.get('session_date') or '—'} "
            f"title={chunk.get('session_title') or meta.get('session_title') or '—'}\n"
        )
        return header + (chunk.get("text") or "")

    return (
        f"Unrecognized ref '{ref}'. Use memory:ID or archive:doc_name#chunk_index "
        "(from memory_search results)."
    )


# =============================================================================
# Tool Registry
# =============================================================================

ALL_MEMORY_TOOLS = [
    save_memory,
    recall_memory,
    recall_recent,
    search_memory,
    memory_search,
    memory_get,
    update_existing_memory,
    correct_memory_tool,
]
TOOLS = ALL_MEMORY_TOOLS  # alias for cove-core channels.py loader
