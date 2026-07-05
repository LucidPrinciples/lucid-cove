"""
Memory Tools — agent's ability to remember things across conversations.

These tools let the agent actively manage persistent memory during conversation:
  - save_memory: Store something worth remembering
  - recall_memory: Pull up memories by category or topic
  - search_memory: Find specific memories by keyword
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
# Tool Registry
# =============================================================================

ALL_MEMORY_TOOLS = [
    save_memory,
    recall_memory,
    recall_recent,
    search_memory,
    update_existing_memory,
    correct_memory_tool,
]
TOOLS = ALL_MEMORY_TOOLS  # alias for cove-core channels.py loader
