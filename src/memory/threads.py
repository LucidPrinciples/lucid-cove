"""
Thread Management — conversation lifecycle for family agents.

Handles thread creation, archiving, listing, rotation, and the extraction/
summarization pipelines that convert ephemeral conversation into persistent
knowledge during thread transitions.

Split from memory.py for maintainability. Core memory CRUD lives in memory.py;
stats/maintenance in maintenance.py.
"""

import logging
from datetime import datetime, timezone

from src.memory.database import get_db
from src.config import get_primary_agent_id
from src.memory.memory import (
    _default_agent_id,
    _memory_agent_id,
    _serialize_thread,
    store_memory,
    find_contradicting_memories,
    flag_memory,
)

logger = logging.getLogger("family.memory")


# =============================================================================
# Memory Extraction — pull knowledge from conversation threads
# =============================================================================

async def extract_memories_from_thread(
    messages: list,
    thread_id: str,
    channel: str,
    agent_id: str | None = None,
) -> list[dict]:
    """Extract discrete memories from a conversation thread using LLM.

    This is the key operation that turns ephemeral conversation into
    persistent knowledge. Called when:
      - A thread is archived
      - Manually triggered from the dashboard
      - On a schedule for long-running threads

    The LLM reads the conversation and identifies:
      - Decisions made
      - Facts learned
      - Preferences expressed
      - Instructions given
      - Project updates
      - People mentioned with context
      - Technical details worth remembering

    Returns list of stored memory dicts.
    """
    agent_id = _default_agent_id(agent_id)
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    if not messages:
        return []

    # Resolve agent and operator names from config
    from src.config import get_instance, get_primary_agent_id
    instance = get_instance()
    agent_name = instance.get("name", get_primary_agent_id().capitalize())
    operator = instance.get("operator", "Operator")

    # Build conversation text from messages
    conv_lines = []
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", str(msg))
        if role in ("human", "ai") and content.strip():
            speaker = operator if role == "human" else agent_name
            conv_lines.append(f"{speaker}: {content.strip()}")

    if not conv_lines:
        return []

    # Cap conversation length for extraction (last ~50 exchanges)
    conv_text = "\n".join(conv_lines[-100:])

    # Channel-aware extraction — Day and Deep have different priorities
    if channel == "deep":
        channel_guidance = """
DEEP CHANNEL PRIORITIES (this is the long-term vision and planning channel):
- Vision changes, long-term direction shifts, and strategic decisions are HIGH importance (0.9+)
- Relationship patterns, emotional context, and personal growth observations are HIGH importance (0.8+)
- Changes to how the operator thinks about their Cove, family, or life direction are HIGH importance
- Reflections on what's working or not working at a systemic level are important (0.7+)
- Anything the operator said about who they are, what matters to them, or what they want their life to look like
- Tag vision-related memories with "deep", "vision", or "direction" so Day channel can surface them"""
    elif channel == "day":
        channel_guidance = """
DAY CHANNEL PRIORITIES (this is the operational, task-oriented channel):
- Standing instructions and process preferences are HIGH importance (0.9+)
- Decisions about how to build or configure things are HIGH importance (0.8+)
- Task outcomes, what was completed, and what's pending are important (0.7)
- Corrections to how the agent approached work are HIGH importance (0.9) — the operator should never have to say the same thing twice
- Technical details (configs, paths, architecture choices) worth keeping (0.6+)
- Skip routine task acknowledgments — only capture decisions and outcomes
- Tag operational memories with "day" or "ops" so Deep channel can surface relevant ones"""
    else:
        channel_guidance = ""

    extraction_prompt = f"""You are a memory extraction system. Read this conversation between {operator} (the operator) and {agent_name} (the assistant) and extract discrete, useful memories.

For each memory, output a JSON line with these fields:
- "content": The memory itself, written as a clear standalone statement (not referencing "the conversation")
- "category": One of: decision, fact, preference, person, project, technical, observation, instruction
- "importance": Float 0.0-1.0 (0.8+ for decisions/instructions, 0.5 for general facts, 0.3 for minor observations)
- "tags": Array of 1-3 short tags
{channel_guidance}

Rules:
- Each memory must stand alone — someone reading it with no context should understand it
- Prefer specific facts over vague summaries
- Decisions and instructions from {operator} are always high importance
- Skip pure pleasantries, acknowledgments, and meta-conversation
- If {operator} corrected {agent_name} or expressed a preference about how to do things, that's high importance (0.9+) — this prevents the operator from having to repeat themselves
- Technical details about infrastructure, configs, and architecture are worth keeping
- Capture WHO people are and their relationship to {operator} when mentioned
- When the operator expresses frustration or satisfaction about how something was done, extract WHY — the underlying preference matters more than the incident

Output ONLY valid JSON lines, one per memory. No other text.

CONVERSATION:
{conv_text}"""

    try:
        response = await invoke_with_fallback(
            [
                SystemMessage(content="You extract structured memories from conversations. Output only JSON lines."),
                HumanMessage(content=extraction_prompt),
            ],
            temperature=0.3,
            label=f"{agent_id}/memory-extraction",
            agent_id=agent_id,
            operation_type="memory",
        )

        # Parse the JSON lines
        import json
        memories = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                mem = json.loads(line)
                content = mem.get("content", "")
                if not content:
                    continue

                # Check for contradictions with existing memories
                contradicts = await find_contradicting_memories(
                    content, agent_id=agent_id, limit=3
                )

                stored = await store_memory(
                    content=content,
                    category=mem.get("category", "general"),
                    importance=float(mem.get("importance", 0.5)),
                    tags=mem.get("tags", []),
                    agent_id=agent_id,
                    source_thread=thread_id,
                    source_channel=channel,
                    source_summary=f"Extracted from thread {thread_id}",
                )

                # Flag if potential contradiction found
                if contradicts:
                    old_ids = ", ".join(f"#{c['id']}" for c in contradicts)
                    await flag_memory(
                        stored["id"],
                        reason=f"May contradict existing memories ({old_ids}). Review to confirm or dismiss.",
                        agent_id=agent_id,
                    )
                    logger.info(
                        f"Memory #{stored['id']} flagged — potential contradiction with {old_ids}"
                    )

                memories.append(stored)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.warning(f"Failed to parse memory line: {e}")
                continue

        logger.info(
            f"Extracted {len(memories)} memories from thread {thread_id}"
        )
        return memories

    except Exception as e:
        logger.error(f"Memory extraction failed for thread {thread_id}: {e}")
        return []


# =============================================================================
# Thread Summary Generation
# =============================================================================

async def generate_thread_summary(
    messages: list,
    thread_id: str,
    channel: str,
    agent_id: str | None = None,
) -> str:
    """Generate a concise summary of a conversation thread.

    Used during thread rotation to seed the new thread with continuity.
    Returns a plain text summary paragraph (200-400 words).
    Returns empty string on failure.
    """
    agent_id = _default_agent_id(agent_id)
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.models.provider import invoke_with_fallback

    if not messages:
        return ""

    # Resolve names from config (not hardcoded)
    from src.config import get_instance, get_primary_agent_id
    instance = get_instance()
    agent_name = instance.get("name", get_primary_agent_id().capitalize())
    operator = instance.get("operator", "Operator")

    # Build conversation text from messages
    conv_lines = []
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", str(msg))
        if role in ("human", "ai") and content.strip():
            speaker = operator if role == "human" else agent_name
            text = content.strip()
            if len(text) > 2000:
                text = text[:2000] + "... [truncated]"
            conv_lines.append(f"{speaker}: {text}")

    if not conv_lines:
        return ""

    # Use the tail of the conversation (most recent is most important)
    conv_text = "\n\n".join(conv_lines[-80:])

    summary_prompt = f"""You are summarizing a conversation thread between {operator} (the operator) and {agent_name} (the AI assistant) in the {channel} channel.

Write a concise summary that captures:
1. What was being worked on or discussed
2. Key decisions that were made
3. Any open questions, pending items, or next steps
4. The state of things when the conversation ended

Write this as a natural paragraph (not bullet points), 200-400 words. Write it as a briefing for {agent_name}'s next thread — the goal is continuity so the conversation can pick up where it left off without {operator} having to re-explain context.

Do NOT include any JSON, markdown formatting, or headers. Just plain prose.

CONVERSATION:
{conv_text}"""

    try:
        summary = await invoke_with_fallback(
            [
                SystemMessage(content="You summarize conversations into clear, concise continuity briefings."),
                HumanMessage(content=summary_prompt),
            ],
            temperature=0.3,
            label=f"{agent_id}/thread-summary",
            agent_id=agent_id,
            operation_type="memory",
        )
        logger.info(f"Generated summary for thread {thread_id} ({len(summary)} chars)")
        return summary.strip()

    except Exception as e:
        logger.error(f"Thread summary generation failed for {thread_id}: {e}")
        return ""


# =============================================================================
# Automatic Thread Rotation — full lifecycle orchestration
# =============================================================================

async def auto_rotate_thread(
    channel: str,
    agent_id: str | None = None,
) -> dict:
    """Automatically rotate a thread when context is critical.

    Full lifecycle:
      1. Get current active thread and its messages
      2. Generate a summary of the conversation
      3. Extract discrete memories from the conversation
      4. Archive the old thread (stores summary + extraction count)
      5. Create a fresh thread
      6. Seed the new thread with a continuation context message

    Returns dict with rotation details (new thread, summary, memories extracted).
    Raises on failure — caller should handle gracefully.
    """
    agent_id = _default_agent_id(agent_id)
    from src.memory.checkpointer import get_checkpointer
    from src.graphs.channels import get_channel_graph
    from langchain_core.messages import HumanMessage, AIMessage

    # 1. Get active thread
    active = await get_active_thread(channel, agent_id)
    if not active:
        raise RuntimeError(f"No active thread for channel {channel}")

    old_thread_id = active["thread_id"]
    logger.info(f"Starting auto-rotation for {channel} thread {old_thread_id}")

    # 2. Get messages from checkpointer
    async with get_checkpointer() as checkpointer:
        graph = await get_channel_graph(channel, checkpointer)
        config = {"configurable": {"thread_id": old_thread_id}}
        snapshot = await graph.aget_state(config)

    messages = []
    if snapshot and snapshot.values:
        messages = snapshot.values.get("messages", [])

    msg_count = len(messages)

    # 3. Generate summary (for continuity)
    summary = await generate_thread_summary(
        messages, old_thread_id, channel, agent_id
    )

    # 4. Extract memories (for long-term knowledge)
    # Use steward agent_id for steward channels so all Presences share one memory pool
    mem_agent = _memory_agent_id(channel, agent_id)
    extraction_result = await extract_memories_from_thread(
        messages, old_thread_id, channel, mem_agent
    )

    # 5. Archive the old thread
    async with get_db() as conn:
        await conn.execute(
            """UPDATE chat_threads
               SET status = 'archived',
                   archived_at = NOW(),
                   summary = %s,
                   memories_extracted = TRUE,
                   extraction_count = %s
               WHERE thread_id = %s AND agent_id = %s""",
            (summary, len(extraction_result), old_thread_id, agent_id),
        )

    # 6. Create new thread
    new_thread = await create_thread(channel=channel, agent_id=agent_id)
    new_thread_id = new_thread["thread_id"]

    # 7. Seed the new thread with continuation context
    if summary:
        seed_content = (
            f"[Thread continuation from {old_thread_id}]\n\n"
            f"Summary of previous conversation ({msg_count} messages):\n"
            f"{summary}\n\n"
            f"Memories extracted: {len(extraction_result)}. "
            f"Continue naturally from where we left off."
        )

        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(channel, checkpointer)
            config = {"configurable": {"thread_id": new_thread_id}}
            await graph.aupdate_state(
                config,
                {
                    "messages": [
                        HumanMessage(content=seed_content),
                        AIMessage(content=(
                            f"Understood. I've picked up the thread from the previous conversation. "
                            f"I have the summary and {len(extraction_result)} extracted memories to work from. "
                            f"Ready to continue."
                        )),
                    ],
                    "agent_id": agent_id,
                    "channel": channel,
                },
            )

    # 8. Store thread summary as a long-term memory
    #    Summaries survive only one rotation in the seed message. After the
    #    *next* rotation that seed is gone. Storing as a high-importance memory
    #    ensures the summary is available to all future threads via recall.
    if summary and len(summary) > 50:
        try:
            await store_memory(
                content=f"[Thread summary — {old_thread_id}] {summary}",
                category="context",
                importance=0.85,
                tags=["thread-summary", channel],
                agent_id=agent_id,
                source_thread=old_thread_id,
                source_channel=channel,
                source_summary=f"Auto-extracted summary from thread rotation ({msg_count} messages)",
            )
            logger.info(f"Stored thread summary as long-term memory for {old_thread_id}")
        except Exception as e:
            logger.warning(f"Failed to store thread summary as memory: {e}")

    logger.info(
        f"Auto-rotated {channel}: {old_thread_id} → {new_thread_id} "
        f"({msg_count} msgs, {len(extraction_result)} memories, "
        f"{len(summary)} char summary)"
    )

    return {
        "rotated": True,
        "old_thread_id": old_thread_id,
        "new_thread_id": new_thread_id,
        "new_thread": new_thread,
        "summary": summary,
        "memories_extracted": len(extraction_result),
        "old_message_count": msg_count,
    }


async def rotate_if_context_critical(
    channel: str,
    agent_id: str | None = None,
) -> dict | None:
    """Best-effort: rotate the channel's active thread if it is at/over the
    critical context threshold. Returns rotation_info if it rotated, else None.
    NEVER raises — a rotation check must not break the turn it precedes.

    This is the background-turn counterpart to the interactive send path's
    pre-send check in dashboard/routes/chat.py. Delegation and wake turns append
    to threads WITHOUT ever hitting that check, so their channels grew unbounded
    (delegation work-logs reached 280-357 msgs and never rotated). Call this
    before writing a background turn so those channels rotate too.

    Token estimate uses the thread messages only (no system prompt / memory
    block). That slightly under-counts vs the interactive path, which is the safe
    direction — it can only delay a rotation by a hair, never trigger a spurious
    one — and by the time a runaway log approaches the threshold the messages
    dominate the token count anyway.
    """
    try:
        agent_id = _default_agent_id(agent_id)
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph
        from src.models.provider import (
            estimate_messages_tokens, get_context_limit,
            CONTEXT_CRITICAL_THRESHOLD, _OPENROUTER_PRIMARY_MODEL,
        )

        active = await get_active_thread(channel, agent_id)
        if not active:
            return None
        tid = active["thread_id"]

        async with get_checkpointer() as cp:
            graph = await get_channel_graph(channel, cp)
            snap = await graph.aget_state({"configurable": {"thread_id": tid}})
        msgs = snap.values.get("messages", []) if snap and snap.values else []
        if not msgs:
            return None

        limit = get_context_limit(_OPENROUTER_PRIMARY_MODEL)
        percent = (estimate_messages_tokens(msgs) / limit) if limit else 0
        if percent >= CONTEXT_CRITICAL_THRESHOLD:
            logger.info(
                f"[bg-rotate] {channel} at {percent*100:.1f}% of context "
                f"-- rotating (agent_id={agent_id})"
            )
            return await auto_rotate_thread(channel, agent_id=agent_id)
        return None
    except Exception as e:
        logger.warning(f"[bg-rotate] check failed for {channel} (non-fatal): {e}")
        return None


# =============================================================================
# Thread Management
# =============================================================================

async def create_thread(
    channel: str,
    agent_id: str | None = None,
    title: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a new thread for a channel. Returns thread info with thread_id.

    metadata: Optional dict stored in the thread's metadata JSONB column.
              For steward channels, this should include operator/Presence info
              so threads can be grouped by Presence in supervisory views.
    """
    agent_id = _default_agent_id(agent_id)
    import json
    import uuid
    thread_id = f"{agent_id}-{channel}-{uuid.uuid4().hex[:8]}"
    if not title:
        title = f"{channel.title()} — {datetime.now(timezone.utc).strftime('%b %d, %Y')}"

    meta_json = json.dumps(metadata) if metadata else '{}'

    async with get_db() as conn:
        result = await conn.execute(
            """INSERT INTO chat_threads
               (thread_id, agent_id, channel, title, status, first_message_at, metadata)
               VALUES (%s, %s, %s, %s, 'active', NOW(), %s::jsonb)
               RETURNING id, thread_id, title, created_at""",
            (thread_id, agent_id, channel, title, meta_json),
        )
        row = await result.fetchone()

    logger.info(f"Created thread {thread_id} for {channel}")
    return {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "title": row["title"],
        "channel": channel,
        "status": "active",
        "metadata": metadata or {},
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def archive_thread(
    thread_id: str,
    agent_id: str | None = None,
    extract_memories: bool = True,
) -> dict:
    """Archive a thread. Optionally extract memories first.

    Steps:
      1. If extract_memories, pull conversation from checkpointer and run extraction
      2. Mark thread as archived
      3. Return summary of what was extracted

    The thread's data stays in the LangGraph checkpointer — archiving just
    marks it inactive so new conversations start fresh.
    """
    agent_id = _default_agent_id(agent_id)
    extraction_result = []

    if extract_memories:
        try:
            from src.memory.checkpointer import get_checkpointer
            from src.graphs.channels import get_channel_graph

            # Get the channel from thread registry
            async with get_db() as conn:
                th = await conn.execute(
                    "SELECT channel FROM chat_threads WHERE thread_id = %s",
                    (thread_id,),
                )
                thread_row = await th.fetchone()

            if thread_row:
                channel = thread_row["channel"]
                async with get_checkpointer() as checkpointer:
                    graph = await get_channel_graph(channel, checkpointer)
                    config = {"configurable": {"thread_id": thread_id}}
                    snapshot = await graph.aget_state(config)

                if snapshot and snapshot.values:
                    messages = snapshot.values.get("messages", [])
                    # Use steward agent_id for memory extraction on steward channels
                    # so all Presences share one memory pool
                    mem_agent = _memory_agent_id(channel, agent_id)
                    extraction_result = await extract_memories_from_thread(
                        messages, thread_id, channel, mem_agent
                    )
        except Exception as e:
            logger.error(f"Memory extraction failed during archive: {e}")

    # Mark as archived
    async with get_db() as conn:
        await conn.execute(
            """UPDATE chat_threads
               SET status = 'archived',
                   archived_at = NOW(),
                   memories_extracted = %s,
                   extraction_count = %s
               WHERE thread_id = %s AND agent_id = %s""",
            (bool(extraction_result), len(extraction_result),
             thread_id, agent_id),
        )

    logger.info(
        f"Archived thread {thread_id} "
        f"({len(extraction_result)} memories extracted)"
    )

    return {
        "thread_id": thread_id,
        "archived": True,
        "memories_extracted": len(extraction_result),
        "memories": extraction_result,
    }


async def get_threads(
    agent_id: str | None = None,
    channel: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List threads with optional filters."""
    agent_id = _default_agent_id(agent_id)
    conditions = ["agent_id = %s"]
    params: list = [agent_id]

    if channel:
        conditions.append("channel = %s")
        params.append(channel)
    if status:
        conditions.append("status = %s")
        params.append(status)

    where = " AND ".join(conditions)

    async with get_db() as conn:
        result = await conn.execute(
            f"""SELECT id, thread_id, channel, title, summary, status,
                       message_count, first_message_at, last_message_at,
                       memories_extracted, extraction_count,
                       created_at, archived_at
                FROM chat_threads
                WHERE {where}
                ORDER BY
                  CASE status WHEN 'active' THEN 0 ELSE 1 END,
                  created_at DESC
                LIMIT %s""",
            (*params, limit),
        )
        rows = await result.fetchall()

    return [_serialize_thread(r) for r in rows]


async def get_active_thread(
    channel: str,
    agent_id: str | None = None,
) -> dict | None:
    """Get the currently active thread for a channel."""
    agent_id = _default_agent_id(agent_id)
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT id, thread_id, channel, title, status,
                      message_count, created_at
               FROM chat_threads
               WHERE agent_id = %s AND channel = %s AND status = 'active'
               ORDER BY created_at DESC
               LIMIT 1""",
            (agent_id, channel),
        )
        row = await result.fetchone()

    return _serialize_thread(row) if row else None


async def update_thread_stats(
    thread_id: str,
    message_count: int | None = None,
) -> None:
    """Update thread message count and last_message_at timestamp."""
    try:
        async with get_db() as conn:
            if message_count is not None:
                await conn.execute(
                    """UPDATE chat_threads
                       SET message_count = %s, last_message_at = NOW()
                       WHERE thread_id = %s""",
                    (message_count, thread_id),
                )
            else:
                await conn.execute(
                    """UPDATE chat_threads
                       SET last_message_at = NOW()
                       WHERE thread_id = %s""",
                    (thread_id,),
                )
    except Exception as e:
        logger.warning(f"Failed to update thread stats: {e}")
