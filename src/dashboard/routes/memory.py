"""
Memory & Thread Management routes — REST API for the memory system.

Thread management:
  GET  /api/threads                    — List all threads (filter by channel/status)
  POST /api/threads/new                — Start a new thread for a channel
  POST /api/threads/{thread_id}/archive — Archive a thread (with optional memory extraction)
  GET  /api/threads/{thread_id}/history — Get message history for a specific thread

Memory browser:
  GET  /api/memories                   — List/search memories
  GET  /api/memories/stats             — Memory system statistics
  POST /api/memories                   — Manually create a memory
  PATCH /api/memories/{id}             — Update a memory
  DELETE /api/memories/{id}            — Deactivate a memory
  POST /api/memories/extract/{thread_id} — Trigger memory extraction for a thread
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.memory.memory import (
    store_memory,
    recall_memories,
    search_memories,
    update_memory,
    get_memory_history,
    get_flagged_memories,
    resolve_flag,
    correct_memory,
    VALID_CATEGORIES,
)
from src.memory.threads import (
    get_threads,
    create_thread,
    archive_thread,
    get_active_thread,
    extract_memories_from_thread,
)
from src.memory.maintenance import (
    get_memory_stats,
    auto_commit_reviewed,
    get_review_queue_stats,
)

router = APIRouter()


async def _mem_agent_id(request: Request) -> str:
    """Whose memories this request operates on — presence-aware, the SAME resolver
    chat uses (_personal_agent_id). In multi-mode this scopes the Memory tab to the
    logged-in Presence instead of the container's primary agent; in single-mode it
    falls back to the primary agent (unchanged). Imported lazily to avoid a circular
    import with the chat route."""
    from src.dashboard.routes.chat import _personal_agent_id
    return await _personal_agent_id(request)


# =============================================================================
# Thread Management
# =============================================================================

@router.get("/api/threads")
async def list_threads(request: Request, channel: str = "", status: str = "", limit: int = 50):
    """List conversation threads with optional filters.

    Presence-scoped: resolves the logged-in Presence's agent_id the same way the
    active-chat path does (personal -> _personal_agent_id, steward/merchant ->
    _manager_thread_scope), instead of the container primary agent. Without this,
    multi-mode Coves list threads under the generic config agent id ("agent") which
    owns no threads, so the past-conversations list comes back empty for every agent.
    Single mode / config presences fall back to the primary agent (unchanged — no
    regression for Cove Cove).
    """
    try:
        from src.dashboard.routes.chat import resolve_list_agent_id
        agent_id = await resolve_list_agent_id(channel, request)
        threads = await get_threads(
            agent_id=agent_id,
            channel=channel or None,
            status=status or None,
            limit=limit,
        )
        return {"threads": threads}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/threads/new")
async def new_thread(request: Request):
    """Start a new thread for a channel, archiving the current one.

    Body: {"channel": "atlas", "title": "Optional custom title"}

    Uses auto_rotate_thread for the full lifecycle: summary generation,
    memory extraction, archive, seed new thread with continuation context.
    Falls back to simple creation if there's no active thread to rotate.
    """
    body = await request.json()
    channel = body.get("channel", "").strip()
    title = body.get("title", "").strip() or None

    if not channel:
        return JSONResponse(
            {"error": "Channel is required."},
            status_code=400,
        )

    try:
        from src.memory.threads import get_active_thread, auto_rotate_thread
        from src.dashboard.routes.chat import resolve_list_agent_id

        # Scope to the logged-in Presence's agent (same as the active-chat path), so a
        # manually started/rotated conversation lands under the right agent_id rather
        # than the generic config primary ("agent").
        agent_id = await resolve_list_agent_id(channel, request)

        # Check if there's an active thread to rotate
        active = await get_active_thread(channel, agent_id)
        if active:
            # Full lifecycle: summarize → extract → archive → seed new thread
            rotation = await auto_rotate_thread(channel, agent_id)
            new_thread_obj = rotation["new_thread"]
            # Apply custom title if provided
            if title:
                from src.memory.database import get_db
                async with get_db() as conn:
                    await conn.execute(
                        "UPDATE chat_threads SET title = %s WHERE thread_id = %s",
                        (title, new_thread_obj["thread_id"]),
                    )
                new_thread_obj["title"] = title
            return {
                "thread": new_thread_obj,
                "rotated": True,
                "memories_extracted": rotation["memories_extracted"],
                "summary_length": len(rotation.get("summary", "") or ""),
            }
        else:
            # No active thread — just create fresh (scoped to this Presence)
            thread = await create_thread(channel=channel, agent_id=agent_id, title=title)
            return {"thread": thread}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/threads/{thread_id}/archive")
async def archive_thread_route(thread_id: str, request: Request):
    """Archive a thread, optionally extracting memories first.

    Body: {"extract_memories": true}  (default: true)
    """
    body = await request.json() if request.headers.get("content-type") else {}
    extract = body.get("extract_memories", True)

    try:
        result = await archive_thread(
            thread_id=thread_id,
            extract_memories=extract,
        )
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/threads/{thread_id}/history")
async def thread_history(thread_id: str, limit: int = 200):
    """Get message history for a specific thread (active or archived).

    Filters out tool messages and intermediate ReAct steps, extracts
    thinking blocks, and includes timestamps and model info.
    """
    import re as _re

    try:
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph
        from src.memory.database import get_db

        # Figure out what channel this thread belongs to
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT channel, title, status, created_at, archived_at "
                "FROM chat_threads WHERE thread_id = %s",
                (thread_id,),
            )
            row = await result.fetchone()

        if not row:
            return JSONResponse({"error": "Thread not found"}, status_code=404)

        channel = row["channel"]
        thread_meta = {
            "title": row["title"],
            "status": row["status"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "archived_at": str(row["archived_at"]) if row["archived_at"] else None,
        }

        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(channel, checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await graph.aget_state(config)

        if not snapshot or not snapshot.values:
            return {"thread_id": thread_id, "channel": channel,
                    "thread": thread_meta, "messages": []}

        all_messages = snapshot.values.get("messages", [])

        # ── Artifact patterns to strip ──
        artifacts = [
            r"<\|user\|>", r"<\|assistant\|>", r"<\|system\|>", r"<\|end\|>",
            r"<\|endoftext\|>", r"\[INST\]", r"\[/INST\]", r"<<SYS>>", r"<</SYS>>",
            r"<think>.*?</think>",
        ]

        def _clean(text):
            if isinstance(text, list):
                parts = []
                for p in text:
                    if isinstance(p, dict):
                        parts.append(p.get("text", ""))
                    elif isinstance(p, str):
                        parts.append(p)
                text = " ".join(parts)
            if not isinstance(text, str):
                text = str(text) if text else ""
            thinking = None
            think_match = _re.search(r"<think>(.*?)</think>", text, flags=_re.DOTALL)
            if think_match:
                thinking = think_match.group(1).strip()
            for pattern in artifacts:
                text = _re.sub(pattern, "", text, flags=_re.DOTALL)
            return text.strip(), thinking

        # ── Filter to displayable messages ──
        displayable = []
        for msg in all_messages:
            msg_type = getattr(msg, "type", None)
            if msg_type == "human":
                displayable.append(msg)
            elif msg_type == "ai":
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    content = " ".join(text_parts)
                if not (isinstance(content, str) and content.strip()):
                    continue
                # Skip intermediate ReAct messages (AI with tool_calls only)
                if getattr(msg, "tool_calls", None):
                    continue
                displayable.append(msg)

        # ── Format with timestamps, model, thinking ──
        formatted = []
        last_ts = None
        for msg in displayable[-limit:]:
            meta = getattr(msg, "response_metadata", {}) or {}
            extra = getattr(msg, "additional_kwargs", {}) or {}
            model_name = meta.get("model_name") or meta.get("model") or ""
            ts = (
                extra.get("created_at")
                or meta.get("created_at")
                or meta.get("created")
                or None
            )
            if isinstance(ts, (int, float)):
                from datetime import datetime, timezone
                ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts:
                last_ts = ts
            elif last_ts:
                ts = last_ts

            msg_content = msg.content if hasattr(msg, "content") else str(msg)
            cleaned, msg_thinking = _clean(msg_content)

            # Check OpenRouter reasoning fields
            if not msg_thinking and getattr(msg, "type", None) == "ai":
                msg_thinking = (
                    extra.get("reasoning_content")
                    or extra.get("reasoning")
                    or meta.get("reasoning_content")
                    or meta.get("reasoning")
                    or None
                )

            entry = {
                "role": msg.type if hasattr(msg, "type") else "unknown",
                "content": cleaned,
                "timestamp": ts,
                "model": model_name,
            }
            if msg_thinking:
                entry["thinking"] = msg_thinking
            formatted.append(entry)

        # Fetch persisted activity steps for this thread
        activity_records = []
        try:
            import json
            async with get_db() as conn:
                result = await conn.execute(
                    """SELECT steps, step_count, recorded_at
                       FROM message_activity
                       WHERE channel = %s AND thread_id = %s
                       ORDER BY recorded_at ASC""",
                    (channel, thread_id),
                )
                rows = await result.fetchall()
            for row in rows:
                steps_val = row["steps"]
                activity_records.append({
                    "steps": json.loads(steps_val) if isinstance(steps_val, str) else steps_val,
                    "step_count": row["step_count"],
                    "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
                })
        except Exception:
            pass  # Activity is non-critical

        return {
            "thread_id": thread_id,
            "channel": channel,
            "thread": thread_meta,
            "messages": formatted,
            "activity": activity_records,
        }

    except Exception as e:
        return {"thread_id": thread_id, "messages": [], "error": str(e)}


# =============================================================================
# Supervisory Chat — Presence thread views for steward MC
# =============================================================================

@router.get("/api/threads/by-presence")
async def threads_by_presence(limit: int = 50):
    """List manager channel threads grouped by Presence.

    Used by manager MCs (steward or merchant) to show a supervisory view —
    one tab per Presence, each containing that Presence's conversation threads.

    Detects the current manager from instance config and queries the right
    prefixed channels (e.g. stuart-day, mercer-day).

    Groups threads by:
      1. metadata.operator_name (if tagged — new threads)
      2. agent_id (fallback — thread creator's container agent_id)
      3. Cove config presences list (for display names)

    Returns: {"presences": [{"name": "Alex", "agent_id": "agent", "threads": [...]}]}
    """
    from src.memory.database import get_db
    from src.config import (get_instance, get_steward_channel_config,
                            get_primary_agent_id, load_config)

    instance = get_instance()
    instance_type = instance.get("type", "personal")
    manager_agent_id = get_primary_agent_id()

    # Build channel list for this manager MC.
    # Steward MC: uses steward_channel config from cove.yaml.
    # Merchant MC: uses channels from agent.yaml + agent name prefix.
    # The admin supervisory view shows BOTH managers' per-presence threads. In the
    # Centralized model "admin" is per-request (host_context), not a container
    # instance type — so always include both steward (Stuart) and merchant (Mercer)
    # channels rather than gating on instance_type (which broke the whole view).
    manager_channels = []
    sc = get_steward_channel_config()
    if sc:
        sname = sc.get("name", "stuart").lower()
        for ch_key in sc.get("channels", {}):
            manager_channels.append(f"{sname}-{ch_key}")
    try:
        from src.config import get_merchant_channel_config
        mc = get_merchant_channel_config()
        if mc:
            mname = mc.get("name", "mercer").lower()
            for ch_key in mc.get("channels", {}):
                manager_channels.append(f"{mname}-{ch_key}")
    except Exception:
        pass

    if not manager_channels:
        # Fallback — derive from agent.yaml channels + agent name
        config = load_config()
        agents = config.get("agents", [])
        agent_name = (agents[0].get("name", manager_agent_id) if agents else manager_agent_id).lower()
        channels_config = config.get("channels", {})
        if channels_config:
            manager_channels = [f"{agent_name}-{ch_key}" for ch_key in channels_config]
        else:
            manager_channels = [f"{agent_name}-day", f"{agent_name}-deep"]

    try:
        async with get_db() as conn:
            placeholders = ", ".join(["%s"] * len(manager_channels))
            result = await conn.execute(
                f"""SELECT id, thread_id, agent_id, channel, title, summary,
                           status, message_count, first_message_at, last_message_at,
                           metadata, created_at, archived_at
                    FROM chat_threads
                    WHERE channel IN ({placeholders})
                    ORDER BY
                      CASE status WHEN 'active' THEN 0 ELSE 1 END,
                      created_at DESC
                    LIMIT %s""",
                (*manager_channels, limit),
            )
            rows = await result.fetchall()

        # Group by Presence
        import json
        presence_map = {}  # key = operator_name or agent_id
        for row in rows:
            meta = row["metadata"] or {}
            if isinstance(meta, str):
                meta = json.loads(meta)

            operator_name = meta.get("operator_name", "")
            agent_id = row["agent_id"]
            presence_key = operator_name or agent_id

            if presence_key not in presence_map:
                presence_map[presence_key] = {
                    "name": operator_name or presence_key.replace("-", " ").title(),
                    "agent_id": agent_id if agent_id != manager_agent_id else None,
                    "presence_agent_name": meta.get("presence_agent_name", ""),
                    "threads": [],
                }

            thread_data = {
                "id": row["id"],
                "thread_id": row["thread_id"],
                "channel": row["channel"],
                "title": row["title"],
                "summary": row["summary"],
                "status": row["status"],
                "message_count": row["message_count"],
                "first_message_at": str(row["first_message_at"]) if row["first_message_at"] else None,
                "last_message_at": str(row["last_message_at"]) if row["last_message_at"] else None,
                "created_at": str(row["created_at"]) if row["created_at"] else None,
                "archived_at": str(row["archived_at"]) if row["archived_at"] else None,
            }
            presence_map[presence_key]["threads"].append(thread_data)

        # Sort presences: most recent activity first
        presences = sorted(
            presence_map.values(),
            key=lambda p: p["threads"][0]["last_message_at"] or p["threads"][0]["created_at"] if p["threads"] else "",
            reverse=True,
        )

        return {"presences": presences}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Memory Browser
# =============================================================================

@router.get("/api/memories")
async def list_memories(
    request: Request,
    category: str = "",
    query: str = "",
    tags: str = "",
    min_importance: float = 0.0,
    review_status: str = "",
    limit: int = 50,
):
    """List or search memories.

    Query params:
      category — filter by category
      query — full-text search
      tags — comma-separated tag filter
      min_importance — minimum importance threshold
      review_status — 'pending' (unreviewed), 'committed' (reviewed), or '' (all)
      limit — max results
    """
    try:
        rs = review_status if review_status in ("pending", "committed") else None
        aid = await _mem_agent_id(request)
        if query:
            memories = await search_memories(
                query=query,
                agent_id=aid,
                category=category or None,
                review_status=rs,
                limit=limit,
            )
        else:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            memories = await recall_memories(
                agent_id=aid,
                category=category or None,
                tags=tag_list,
                min_importance=min_importance,
                review_status=rs,
                limit=limit,
            )

        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/memories/stats")
async def memory_stats(request: Request):
    """Get memory system statistics."""
    try:
        stats = await get_memory_stats(agent_id=await _mem_agent_id(request))
        stats["categories"] = sorted(VALID_CATEGORIES)
        return stats
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories")
async def create_memory(request: Request):
    """Manually create a memory entry.

    Body: {
        "content": "Memory text",
        "category": "decision",
        "importance": 0.8,
        "tags": ["tag1", "tag2"],
        "source_summary": "Manually added via dashboard"
    }
    """
    body = await request.json()
    content = body.get("content", "").strip()

    if not content:
        return JSONResponse({"error": "Content is required"}, status_code=400)

    try:
        memory = await store_memory(
            content=content,
            category=body.get("category", "general"),
            importance=float(body.get("importance", 0.5)),
            tags=body.get("tags", []),
            source_summary=body.get("source_summary", "Added via Mission Control"),
        )
        return {"memory": memory}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/memories/bulk")
async def bulk_update_memories(request: Request):
    """Bulk update multiple memories at once.

    Body: {
        "ids": [1, 2, 3],
        "category": "decision",      (optional)
        "importance": 0.7,           (optional)
        "is_active": false           (optional)
    }
    """
    body = await request.json()
    ids = body.get("ids", [])

    if not ids or not isinstance(ids, list):
        return JSONResponse({"error": "ids array is required"}, status_code=400)

    updates = {}
    if "category" in body:
        updates["category"] = body["category"]
    if "importance" in body:
        updates["importance"] = body["importance"]
    if "is_active" in body:
        updates["is_active"] = body["is_active"]
    if "reviewed" in body:
        updates["reviewed"] = body["reviewed"]

    if not updates:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    try:
        updated = 0
        for memory_id in ids:
            result = await update_memory(memory_id=int(memory_id), **updates)
            if result:
                updated += 1
        return {"updated": updated, "total": len(ids)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/memories/review-stats")
async def review_stats():
    """Get review queue vs committed counts."""
    try:
        stats = await get_review_queue_stats()
        return stats
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories/auto-commit")
async def trigger_auto_commit(request: Request):
    """Auto-commit memories older than N days (default 7).

    Body: {"days": 7}  (optional)
    """
    body = {}
    if request.headers.get("content-type"):
        body = await request.json()
    days = int(body.get("days", 7))

    try:
        count = await auto_commit_reviewed(days=days)
        return {"committed": count, "days": days}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories/commit")
async def commit_memories(request: Request):
    """Manually commit specific memories (mark as reviewed).

    Body: {"ids": [1, 2, 3]}
    """
    body = await request.json()
    ids = body.get("ids", [])

    if not ids:
        return JSONResponse({"error": "ids array is required"}, status_code=400)

    try:
        committed = 0
        for memory_id in ids:
            result = await update_memory(memory_id=int(memory_id), reviewed=True)
            if result:
                committed += 1
        return {"committed": committed, "total": len(ids)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/memories/{memory_id}")
async def update_memory_route(memory_id: int, request: Request):
    """Update a memory entry.

    Body: {"content": "...", "category": "...", "importance": 0.8, "tags": [...]}
    """
    body = await request.json()

    try:
        result = await update_memory(
            memory_id=memory_id,
            content=body.get("content"),
            category=body.get("category"),
            importance=body.get("importance"),
            tags=body.get("tags"),
            is_active=body.get("is_active"),
            reviewed=body.get("reviewed"),
        )
        if not result:
            return JSONResponse({"error": "Memory not found"}, status_code=404)
        return {"memory": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/memories/{memory_id}")
async def deactivate_memory(memory_id: int):
    """Deactivate (soft-delete) a memory."""
    try:
        result = await update_memory(
            memory_id=memory_id,
            is_active=False,
        )
        if not result:
            return JSONResponse({"error": "Memory not found"}, status_code=404)
        return {"deactivated": True, "memory_id": memory_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories/extract/{thread_id}")
async def trigger_extraction(thread_id: str):
    """Manually trigger memory extraction for a thread.

    Reads the full conversation from the checkpointer and runs
    LLM-based memory extraction. Works on active or archived threads.
    """
    try:
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph
        from src.memory.database import get_db

        # Get channel for this thread
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT channel FROM chat_threads WHERE thread_id = %s",
                (thread_id,),
            )
            row = await result.fetchone()

        if not row:
            return JSONResponse({"error": "Thread not found"}, status_code=404)

        channel = row["channel"]

        # Get messages from checkpointer
        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(channel, checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await graph.aget_state(config)

        if not snapshot or not snapshot.values:
            return {"thread_id": thread_id, "memories": [], "message": "No messages in thread"}

        messages = snapshot.values.get("messages", [])
        memories = await extract_memories_from_thread(
            messages, thread_id, channel
        )

        # Update thread extraction status
        async with get_db() as conn:
            await conn.execute(
                """UPDATE chat_threads
                   SET memories_extracted = TRUE,
                       extraction_count = extraction_count + %s
                   WHERE thread_id = %s""",
                (len(memories), thread_id),
            )

        return {
            "thread_id": thread_id,
            "memories_extracted": len(memories),
            "memories": memories,
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Memory Review & Correction
# =============================================================================

@router.get("/api/memories/flagged")
async def list_flagged_memories():
    """Get all memories flagged for operator review (contradictions, etc.)."""
    try:
        flagged = await get_flagged_memories()
        return {"memories": flagged, "count": len(flagged)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/memories/{memory_id}/history")
async def memory_history(memory_id: int):
    """Get the correction/supersession history chain for a memory."""
    try:
        history = await get_memory_history(memory_id)
        if not history:
            return JSONResponse({"error": "Memory not found"}, status_code=404)
        return {"history": history, "count": len(history)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories/{memory_id}/approve")
async def approve_flagged(memory_id: int):
    """Approve a flagged memory — keeps it active and clears the flag."""
    try:
        ok = await resolve_flag(memory_id, action="approve")
        if not ok:
            return JSONResponse({"error": "Failed to approve"}, status_code=500)
        return {"approved": True, "memory_id": memory_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories/{memory_id}/dismiss")
async def dismiss_flagged(memory_id: int):
    """Dismiss a flagged memory — deactivates it."""
    try:
        ok = await resolve_flag(memory_id, action="dismiss")
        if not ok:
            return JSONResponse({"error": "Failed to dismiss"}, status_code=500)
        return {"dismissed": True, "memory_id": memory_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/memories/{memory_id}/correct")
async def correct_memory_route(memory_id: int, request: Request):
    """Correct a memory from the dashboard (creates superseding entry).

    Body: {"content": "corrected text", "reason": "why it changed"}
    """
    body = await request.json()
    content = body.get("content", "").strip()
    reason = body.get("reason", "").strip()

    if not content:
        return JSONResponse({"error": "Content is required"}, status_code=400)

    try:
        result = await correct_memory(
            memory_id=memory_id,
            new_content=content,
            reason=reason or "Corrected via dashboard",
        )
        if not result:
            return JSONResponse({"error": "Memory not found or inactive"}, status_code=404)
        return {"corrected": True, "old_id": memory_id, "new_memory": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
