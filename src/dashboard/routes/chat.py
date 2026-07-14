"""
Chat routes — SSE streaming conversation with any agent, any channel.

COVE-CORE: Channel comes from request (default: first channel in config).
Replaces hardcoded single-channel design with multi-channel Day/Deep support.

Features: SSE streaming with live tool steps, thinking extraction,
heartbeat, cancellation, activity persistence, cross-device polling,
auto-rotation at context limits, token counting, formatted history.
"""

import os
from src.env import env
import asyncio
import json as _json
import re as _re
import time as _time

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.errors import GraphRecursionError

from src.config import get_default_channel, get_primary_agent_id, get_operator_name, get_steward_channel_config
from src.memory.database import channel_db_scope, enter_channel_db_scope, exit_channel_db_scope

router = APIRouter()

# #D26: the interactive chat turn ran on LangGraph's DEFAULT recursion_limit (25
# super-steps ≈ 12 tool rounds) — nothing set it here, so a heavy tool-using turn hit
# the ceiling and surfaced as a generic error (some "model errors" were really this).
# Matches delegation's ceiling; the turn TIMEOUT, not the step count, is the real
# runaway bound. Raised 100->200 (2026-07-12): heavy Clearfield dev turns (read
# several files, edit, test, git) legitimately exceed ~50 tool rounds and were
# getting chopped mid-task every run. Timeout still caps genuine runaways.
CHAT_RECURSION_LIMIT = 200

# Track running send tasks for cancellation — keyed by channel
_running_tasks: dict[str, asyncio.Event] = {}

# Channel processing status (visible to all clients) — keyed by channel
_channel_status: dict[str, dict] = {}


def _update_channel_status(channel: str, **kwargs):
    """Update the shared processing status for a channel."""
    if channel not in _channel_status:
        _channel_status[channel] = {"steps": []}
    _channel_status[channel].update(kwargs)
    _channel_status[channel]["updated_at"] = _time.time()


def _add_activity_step(channel: str, text: str):
    """Append a live activity step so all devices can see it."""
    if channel not in _channel_status:
        _channel_status[channel] = {"steps": []}
    if "steps" not in _channel_status[channel]:
        _channel_status[channel]["steps"] = []
    _channel_status[channel]["steps"].append(text)


async def _persist_activity_steps(channel: str, thread_id: str | None = None):
    """Save activity steps to the database.

    Callers that already resolved the send's thread MUST pass it in. Resolving
    here without the request loses the presence scope (multi mode) and silently
    creates/maintains a primary-scoped thread for a presence-scoped channel —
    the companion half of the wrong-scope rotation bug."""
    status = _channel_status.get(channel, {})
    steps = status.get("steps", [])
    if not steps:
        return
    try:
        if not thread_id:
            thread_id = await _get_active_thread_id(channel)
        from src.memory.database import get_db
        import json
        started_at = status.get("started_at")
        if started_at:
            recorded_ts = datetime.fromtimestamp(started_at, tz=timezone.utc)
        else:
            recorded_ts = datetime.now(timezone.utc)
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO message_activity (channel, thread_id, steps, step_count, recorded_at)
                   VALUES (%s, %s, %s::jsonb, %s, %s)""",
                (channel, thread_id, json.dumps(steps), len(steps), recorded_ts),
            )
    except Exception as e:
        print(f"[chat] Failed to persist activity steps: {e}")


def _clear_channel_status(channel: str):
    """Clear processing status when done."""
    _channel_status.pop(channel, None)


async def _personal_agent_id(request: Request = None) -> str:
    """The logged-in Presence's own agent id (Centralized model).

    A data-entry Presence (created via Agent Setup; its agent lives in
    accounts.agent_identity) chats with ITS OWN agent, scoped by presence id.
    Falls back to the container primary for single-mode and for config-defined
    Presences (e.g. Knight) whose agent_identity is empty — so Cove Cove and the
    existing agents are unchanged.
    """
    import os
    if request is not None and env("COVE_MODE", "single") == "multi":
        try:
            presence = getattr(request.state, "presence", None)
            if presence is None:
                from src.dashboard.routes.presence import get_current_presence
                presence = await get_current_presence(request)
            if presence and (presence.get("agent_identity") or {}):
                return str(presence["id"])
        except Exception:
            pass
    return get_primary_agent_id()


async def _manager_thread_scope(request: Request = None) -> str | None:
    """Per-presence scope key for MANAGER (Stuart/Mercer) threads in multi mode.

    Each presence gets their OWN thread with each manager — Cove Cove had this
    implicitly (separate containers); the Centralized model needs it explicit.
    The manager still responds (channel drives the agent) and writes to the ONE
    shared manager memory (`_memory_agent_id`); only the thread/display is scoped
    per presence. Returns the presence id, or None in single mode (one shared
    thread — unchanged, no regression for Cove Cove / config presences).
    """
    import os
    if request is not None and env("COVE_MODE", "single") == "multi":
        try:
            presence = getattr(request.state, "presence", None)
            if presence is None:
                from src.dashboard.routes.presence import get_current_presence
                presence = await get_current_presence(request)
            if presence and presence.get("id"):
                return str(presence["id"])
        except Exception:
            pass
    return None


async def resolve_list_agent_id(channel: str, request: Request = None) -> str:
    """Read/list counterpart to _get_active_thread_id's scoping: the agent_id that
    owns a channel's threads for the CURRENT Presence, WITHOUT creating a thread.

    Personal channels -> the Presence's own agent (_personal_agent_id); steward/
    merchant channels -> the per-presence manager scope (_manager_thread_scope);
    falls back to the container primary in single mode / when no Presence is on the
    request. Use this for any endpoint that LISTS or starts threads so they scope to
    the same agent the active-chat path uses (otherwise multi-mode lists query the
    generic config agent id, which owns no threads, and come back empty)."""
    from src.config import _is_steward_channel, _is_merchant_channel, get_primary_agent_id
    is_manager = _is_steward_channel(channel) or _is_merchant_channel(channel)
    agent_id = (await _manager_thread_scope(request)) if is_manager else (await _personal_agent_id(request))
    return agent_id or get_primary_agent_id()


async def _get_active_thread_id(channel: str, request: Request = None) -> str:
    """Get the active thread ID for a channel. Creates one if none exists.

    When creating a new thread (no active thread found), seeds it with
    the last archived thread's summary for continuity. This handles:
      - Container restarts (threads in DB but none marked active)
      - First-ever conversation (no threads at all)
      - After manual reset (previous thread was just archived)

    For steward channels, tags the thread with operator/Presence metadata
    so threads can be grouped by Presence in supervisory views.
    """
    from src.memory.threads import get_active_thread, create_thread
    from src.config import _is_steward_channel, _is_merchant_channel
    is_manager = _is_steward_channel(channel) or _is_merchant_channel(channel)
    # Personal channels scope to the logged-in Presence's own agent. Manager
    # channels scope per-presence too (each presence has their own Stuart/Mercer
    # thread); falls back to one shared thread in single mode.
    agent_id = (await _manager_thread_scope(request)) if is_manager else (await _personal_agent_id(request))
    try:
        thread = await get_active_thread(channel, agent_id)
        if thread:
            return thread["thread_id"]
    except Exception as e:
        print(f"[chat] WARNING: DB lookup for active thread failed: {e}")
        raise

    # Build metadata for manager channel threads — tag with Presence identity
    metadata = None
    if is_manager:
        metadata = _build_presence_metadata(request)

    print(f"[chat] No active thread for {channel} -- creating new thread with continuity seeding")
    new_thread = await create_thread(channel=channel, agent_id=agent_id, metadata=metadata)
    new_thread_id = new_thread["thread_id"]

    # Seed the new thread with the last archived thread's summary
    # so the agent picks up where the last conversation left off
    try:
        await _seed_thread_with_continuity(new_thread_id, channel, agent_id)
    except Exception as e:
        print(f"[chat] Thread seeding failed (non-fatal, thread still usable): {e}")

    return new_thread_id


def _build_presence_metadata(request: Request = None) -> dict:
    """Build Presence identity metadata for steward channel threads.

    Tries to get Presence info from the request (multi-Presence mode),
    falls back to container-level operator name (single-Presence mode).
    """
    import os
    metadata = {
        "presence_agent_id": get_primary_agent_id(),
        "operator_name": get_operator_name(),
    }

    # In multi-Presence mode, try to get the actual logged-in Presence info
    if request and env("COVE_MODE", "single") == "multi":
        try:
            # Can't await here (sync function), but we stored it on request state
            # during the middleware. Check if it's available.
            presence = getattr(request.state, "presence", None)
            if presence:
                metadata["presence_id"] = str(presence.get("id", ""))
                metadata["operator_name"] = presence.get("display_name", metadata["operator_name"])
                metadata["presence_agent_name"] = presence.get("agent_name", "")
                # Centralized data-entry Presence → tag with its own agent id, not the
                # container primary, so steward threads group by the real Presence.
                if (presence.get("agent_identity") or {}):
                    metadata["presence_agent_id"] = str(presence.get("id", ""))
        except Exception:
            pass

    return metadata


async def _seed_thread_with_continuity(thread_id: str, channel: str, agent_id: str = None):
    """Seed a fresh thread with context from the last archived thread.

    Loads the most recent archived thread's summary for this channel and
    injects it as the opening exchange so the agent has continuity.
    If no archived threads exist, this is a no-op (first-ever conversation).
    """
    from src.memory.database import get_db
    from src.config import get_primary_agent_id, get_instance

    if agent_id is None:
        agent_id = get_primary_agent_id()
    instance = get_instance()
    agent_name = instance.get("name", agent_id.capitalize())

    # Find the most recent archived thread with a summary for this channel
    async with get_db() as conn:
        result = await conn.execute(
            """SELECT thread_id, summary, message_count, extraction_count, archived_at
               FROM chat_threads
               WHERE agent_id = %s AND channel = %s AND status = 'archived'
                 AND summary IS NOT NULL AND summary != ''
               ORDER BY archived_at DESC
               LIMIT 1""",
            (agent_id, channel),
        )
        row = await result.fetchone()

    if not row:
        print(f"[chat] No archived threads with summaries for {channel} — starting fresh")
        return

    summary = row["summary"]
    old_thread_id = row["thread_id"]
    msg_count = row["message_count"] or 0
    extraction_count = row["extraction_count"] or 0

    # Inject the summary as a seed exchange into the checkpointer
    from src.memory.checkpointer import get_checkpointer
    from src.graphs.channels import get_channel_graph
    from langchain_core.messages import HumanMessage, AIMessage

    seed_content = (
        f"[Continuity from previous conversation — {old_thread_id}]\n\n"
        f"Summary of our last conversation ({msg_count} messages):\n"
        f"{summary}\n\n"
        f"Memories from that conversation were extracted ({extraction_count} memories). "
        f"Pick up naturally from where we left off."
    )

    async with get_checkpointer() as checkpointer:
        graph = await get_channel_graph(channel, checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content=seed_content),
                    AIMessage(content=(
                        f"I've picked up the thread from our previous conversation. "
                        f"I have the summary and my extracted memories to work from. "
                        f"Ready to continue."
                    )),
                ],
                "agent_id": agent_id,
                "channel": channel,
            },
        )

    print(f"[chat] Seeded {channel} thread {thread_id} with summary from {old_thread_id} "
          f"({len(summary)} chars)")


def _extract_thinking(text) -> tuple[str, str | None]:
    """Extract thinking blocks from model output.

    Returns (cleaned_text, thinking_text_or_None).
    Handles qwen3 <think> blocks, OpenRouter reasoning, and chat-template artifacts.
    """
    if isinstance(text, list):
        parts = []
        for part in text:
            if isinstance(part, dict):
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        text = " ".join(parts)
    if not isinstance(text, str):
        text = str(text) if text else ""

    thinking = None
    think_match = _re.search(r"<think>(.*?)</think>", text, flags=_re.DOTALL)
    if think_match:
        thinking = think_match.group(1).strip()

    artifacts = [
        r"<\|user\|>", r"<\|assistant\|>", r"<\|system\|>", r"<\|end\|>",
        r"<\|endoftext\|>", r"\[INST\]", r"\[/INST\]", r"<<SYS>>", r"<</SYS>>",
        r"<think>.*?</think>",
    ]
    for pattern in artifacts:
        text = _re.sub(pattern, "", text, flags=_re.DOTALL)
    return text.strip(), thinking


# =============================================================================
# Status Endpoint — Cross-device polling
# =============================================================================

@router.get("/api/chat/status")
async def get_chat_status(channel: str = ""):
    """Get current processing status for a channel."""
    ch = channel or get_default_channel()
    status = _channel_status.get(ch)
    if not status:
        return {"channel": ch, "processing": False}

    elapsed = _time.time() - status.get("started_at", _time.time())
    return {
        "channel": ch,
        "processing": status.get("processing", False),
        "step": status.get("step", ""),
        "step_count": status.get("step_count", 0),
        "elapsed_seconds": round(elapsed, 1),
        "updated_at": status.get("updated_at"),
        "steps": status.get("steps", []),
    }


# =============================================================================
# Chat History
# =============================================================================

@router.get("/api/chat/history")
async def get_history(request: Request, channel: str = "", limit: int = 50):
    """Return chat history for a channel from the checkpointer."""
    ch = channel or get_default_channel()
    agent_id = await _personal_agent_id(request)
    operator = get_operator_name()

    async with channel_db_scope(ch):
      try:
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph

        thread_id = await _get_active_thread_id(ch, request)

        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(ch, checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            state = await graph.aget_state(config)

        if not state or not state.values:
            return {"messages": [], "thread_id": thread_id, "channel": ch}

        all_messages = state.values.get("messages", [])

        # Filter to displayable messages
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
                if getattr(msg, "tool_calls", None):
                    continue
                displayable.append(msg)

        # Format with metadata
        formatted = []
        last_ts = None
        for msg in displayable[-limit:]:
            meta = getattr(msg, "response_metadata", {}) or {}
            extra = getattr(msg, "additional_kwargs", {}) or {}
            model_name = meta.get("model_name") or meta.get("model") or ""

            ts = (
                extra.get("created_at") or meta.get("created_at")
                or meta.get("created") or None
            )
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts:
                last_ts = ts
            elif last_ts:
                ts = last_ts

            msg_content = msg.content if hasattr(msg, "content") else str(msg)
            cleaned, msg_thinking = _extract_thinking(msg_content)

            if not msg_thinking and getattr(msg, "type", None) == "ai":
                msg_thinking = (
                    extra.get("reasoning_content") or extra.get("reasoning")
                    or meta.get("reasoning_content") or meta.get("reasoning")
                    or None
                )

            role = msg.type if hasattr(msg, "type") else "unknown"
            # Use operator name for human messages, agent name for AI
            # Steward channels (stuart-day, stuart-deep): AI name is the steward
            ai_name = agent_id
            sc = get_steward_channel_config()
            if sc:
                steward_name = sc.get("name", "stuart").lower()
                sc_channels = sc.get("channels", {})
                steward_channel_names = [f"{steward_name}-{k}" for k in sc_channels]
                # Also match bare steward name for backward compat
                steward_channel_names.append(steward_name)
                if ch in steward_channel_names:
                    ai_name = sc.get("agent_id", "stuart")
            name = operator if role == "human" else ai_name

            entry = {
                "role": role, "name": name, "content": cleaned,
                "timestamp": ts, "model": model_name,
            }
            if msg_thinking:
                entry["thinking"] = msg_thinking
            formatted.append(entry)

        # Fetch persisted activity steps
        activity_records = []
        try:
            from src.memory.database import get_db
            import json
            async with get_db() as conn:
                result = await conn.execute(
                    """SELECT steps, step_count, recorded_at
                       FROM message_activity
                       WHERE channel = %s AND thread_id = %s
                       ORDER BY recorded_at ASC""",
                    (ch, thread_id),
                )
                rows = await result.fetchall()
            for row in rows:
                steps_val = row["steps"]
                activity_records.append({
                    "steps": _json.loads(steps_val) if isinstance(steps_val, str) else steps_val,
                    "step_count": row["step_count"],
                    "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
                })
        except Exception:
            pass

        print(f"[chat] history ch={ch} thread={thread_id} agent_id={agent_id} returned={len(formatted)} msgs")
        return {"messages": formatted, "thread_id": thread_id, "channel": ch, "activity": activity_records}
      except Exception as e:
        print(f"[chat] history FAILED ch={ch}: {type(e).__name__}: {e}")
        return {"messages": [], "error": str(e)}


# =============================================================================
# Context Usage
# =============================================================================

@router.get("/api/chat/context")
async def get_context(request: Request, channel: str = ""):
    """Get current context window usage for a channel's thread."""
    ch = channel or get_default_channel()
    agent_id = await _personal_agent_id(request)
    _scope = enter_channel_db_scope(ch)

    try:
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph, get_channel_system_addition
        from src.models.provider import (
            estimate_messages_tokens, get_context_limit,
            CONTEXT_WARN_THRESHOLD, CONTEXT_CRITICAL_THRESHOLD,
            _OPENROUTER_PRIMARY_MODEL,
        )
        from src.agents.identity import build_system_prompt
        from src.memory.memory import load_memories_for_prompt
        from langchain_core.messages import SystemMessage

        thread_id = await _get_active_thread_id(ch, request)

        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(ch, checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await graph.aget_state(config)

        messages = []
        if snapshot and snapshot.values:
            messages = snapshot.values.get("messages", [])

        try:
            memory_block = await load_memories_for_prompt(agent_id=agent_id, channel=ch)
        except Exception:
            memory_block = ""
        system_prompt = build_system_prompt(agent_id)
        channel_addition = get_channel_system_addition(ch)
        full_system = system_prompt + channel_addition
        if memory_block:
            full_system += f"\n\n## Active Memories\n{memory_block}"

        all_messages = [SystemMessage(content=full_system)] + list(messages)
        tokens_used = estimate_messages_tokens(all_messages)
        token_limit = get_context_limit(_OPENROUTER_PRIMARY_MODEL)
        percent = round(tokens_used / token_limit * 100, 1) if token_limit else 0

        if percent >= CONTEXT_CRITICAL_THRESHOLD * 100:
            status = "critical"
        elif percent >= CONTEXT_WARN_THRESHOLD * 100:
            status = "warning"
        else:
            status = "ok"

        return {
            "thread_id": thread_id,
            "channel": ch,
            "context_usage": {
                "tokens_used": tokens_used,
                "token_limit": token_limit,
                "percent": percent,
                "status": status,
                "message_count": len(messages),
                "system_prompt_tokens": estimate_messages_tokens([SystemMessage(content=full_system)]),
            },
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        exit_channel_db_scope(_scope)


# =============================================================================
# Send Message — SSE Streaming
# =============================================================================

@router.post("/api/chat/send")
async def send_message(request: Request):
    """Send a message and stream the response via SSE.

    Body: {"message": "...", "channel": "day"}
    Channel defaults to the first channel in agent.yaml.
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    ch = body.get("channel", "") or get_default_channel()
    input_mode = body.get("input_mode", "type")  # type, dictate, or voice
    agent_id = await _personal_agent_id(request)

    # Centralized model (multi-mode): resolve THIS Presence's identity so the agent
    # answers with its own persona + personality dials, not the container's static
    # agent.yaml. None in single-mode → unchanged behavior. Managers stay manager.
    presence_identity = None
    _byok_provider = _byok_key = ""   # #121 — this operator's own model creds (if set)
    try:
        if env("COVE_MODE", "single") == "multi":
            from src.dashboard.routes.presence import get_current_presence
            _p = await get_current_presence(request)
            if _p and _p.get("agent_identity"):
                presence_identity = _p["agent_identity"]
            if _p:
                _ac = _p.get("agent_config") or {}
                if isinstance(_ac, str):
                    try:
                        # NOTE: use the module-level `_json` (line 15). A local
                        # `import json as _json` here would make `_json` a function
                        # local, and the nested `_sse()` closure would then hit an
                        # unbound-variable NameError whenever this branch didn't run.
                        _ac = _json.loads(_ac) or {}
                    except Exception:
                        _ac = {}
                if isinstance(_ac, dict):
                    _byok_provider = _ac.get("model_provider") or ""
                    _byok_key = _ac.get("model_api_key") or ""
    except Exception as _e:
        print(f"[chat] presence identity resolve failed (non-fatal): {_e}")

    # #121 fix: the operator's PERSONAL model creds power their OWN agent only. On a
    # manager channel (steward/merchant) the acting agent is a shared Cove agent
    # (Stuart/Mercer) with its OWN model assignment (e.g. Stuart = Grok), which must
    # win. Applying the operator BYOK here silently overrode that with the operator's
    # provider default (openrouter -> openrouter/auto), so Stuart ran on the OpenRouter
    # lottery instead of its assigned Grok. Skip the operator BYOK on manager channels.
    from src.config import _is_steward_channel, _is_merchant_channel
    if _byok_provider and (_is_steward_channel(ch) or _is_merchant_channel(ch)):
        _byok_provider = _byok_key = ""

    if not user_message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    # Diagnostic: what model context is this send resolving? (empty-chat / no-reply triage)
    print(f"[chat] send ch={ch} agent_id={agent_id} mode={env('COVE_MODE','single')} "
          f"byok_provider={_byok_provider or '(none)'} byok_key={'set' if _byok_key else '(none)'} "
          f"presence_identity={'yes' if presence_identity else 'no'}")

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph, get_channel_system_addition
        from src.models.provider import (
            estimate_messages_tokens, get_context_limit,
            CONTEXT_CRITICAL_THRESHOLD, _OPENROUTER_PRIMARY_MODEL,
        )
        from src.agents.identity import build_system_prompt
        from src.memory.memory import load_memories_for_prompt

        # Pre-send DB calls — route to steward DB if steward channel
        _pre_scope = enter_channel_db_scope(ch)
        try:
            thread_id = await _get_active_thread_id(ch, request=request)
            rotation_info = None
            pre_send_msg_count = 0

            # Pre-send context check — auto-rotate if critical
            try:
                async with get_checkpointer() as checkpointer:
                    graph = await get_channel_graph(ch, checkpointer)
                    config = {"configurable": {"thread_id": thread_id}}
                    snapshot = await graph.aget_state(config)

                if snapshot and snapshot.values:
                    existing_msgs = snapshot.values.get("messages", [])
                    pre_send_msg_count = len(existing_msgs)
                    if existing_msgs:
                        try:
                            memory_block = await load_memories_for_prompt(agent_id=agent_id, channel=ch)
                        except Exception:
                            memory_block = ""
                        sys_prompt = build_system_prompt(agent_id, agent_identity=presence_identity)
                        channel_addition = get_channel_system_addition(ch)
                        full_sys = sys_prompt + channel_addition
                        if memory_block:
                            full_sys += f"\n\n## Active Memories\n{memory_block}"
                        all_msgs = [SystemMessage(content=full_sys)] + list(existing_msgs)
                        tokens_used = estimate_messages_tokens(all_msgs)
                        token_limit = get_context_limit(_OPENROUTER_PRIMARY_MODEL)
                        percent = tokens_used / token_limit if token_limit else 0

                        if percent >= CONTEXT_CRITICAL_THRESHOLD:
                            # Rotate under the SAME scope that resolved thread_id above.
                            # Unscoped rotation archives/creates a PRIMARY-scoped thread
                            # while the presence-scoped thread stays active + over-limit:
                            # the reply loses all short-term context and every subsequent
                            # send re-rotates (persistent amnesia on that channel).
                            _rot_scope = await resolve_list_agent_id(ch, request)
                            print(f"[chat] Context critical ({percent*100:.1f}%) on {ch} "
                                  f"-- auto-rotating (scope={_rot_scope})")
                            from src.memory.threads import auto_rotate_thread
                            rotation_info = await auto_rotate_thread(ch, agent_id=_rot_scope)
                            thread_id = rotation_info["new_thread_id"]
                            pre_send_msg_count = 2
            except Exception as e:
                print(f"[chat] Pre-send context check failed (non-fatal): {e}")
        finally:
            exit_channel_db_scope(_pre_scope)

        now_iso = datetime.now(timezone.utc).isoformat()

        def _sse(data: dict) -> str:
            return f"data: {_json.dumps(data)}\n\n"

        _HEARTBEAT_INTERVAL = 10

        async def _event_stream():
            # Route DB ops to steward DB if steward channel
            _stream_scope = enter_channel_db_scope(ch)
            _cancel_event = asyncio.Event()
            _running_tasks[ch] = _cancel_event
            _update_channel_status(ch, processing=True, started_at=_time.time(),
                                   step="Starting...", step_count=0)
            # #121 — use this operator's own model creds for the duration of the run
            # (server-side; falls back to the Cove default when unset).
            _byok_tok = None
            try:
                from src.models.provider import set_request_byok
                _byok_tok = set_request_byok(_byok_provider, _byok_key)
            except Exception:
                _byok_tok = None
            # CF-57 — bind THIS presence's Nextcloud creds for the run so the agent's
            # calendar/file tools act as the requesting presence (multi-Cove), resolved
            # the same way the Files/Calendar UI does (get_nc_creds). Unset → tools fall
            # back to the env globals (single-user unchanged).
            _nc_tok = None
            try:
                from src.dashboard.routes.nextcloud import get_nc_creds, NC_ADMIN_USER, NC_ADMIN_PASSWORD
                from src.tools.nextcloud_tools import set_request_nc_creds
                from src.config import _is_steward_channel, _is_merchant_channel
                if (_is_steward_channel(ch) or _is_merchant_channel(ch)) and NC_ADMIN_PASSWORD:
                    # Managers (Stuart/Mercer) have NO NC user of their own -- they act in the
                    # cove ADMIN NC space, NOT the requesting operator's. Without this, a manager
                    # invoked from an operator's chat authenticates AS that operator and writes
                    # into their folder (e.g. Mercer reports landing in JAG), which also defeats
                    # the narrow Inbox share. Mirrors the admin-pinning files.py uses for KB paths.
                    _nc_url, _nc_user, _nc_pass = "", NC_ADMIN_USER, NC_ADMIN_PASSWORD
                else:
                    _nc_url, _nc_user, _nc_pass = await get_nc_creds(request)
                if _nc_user:
                    _nc_tok = set_request_nc_creds(_nc_url, _nc_user, _nc_pass)
            except Exception:
                _nc_tok = None
            # CF-59 — bind the acting presence so the agent's Links-board tool writes
            # THIS operator's board (multi-Cove); unset → single-mode file fallback.
            _links_tok = None
            try:
                from src.dashboard.routes.presence import get_current_presence as _gcp
                from src.tools.links_tools import set_request_links_presence
                _lp = await _gcp(request)
                if _lp and _lp.get("id"):
                    _links_tok = set_request_links_presence(str(_lp["id"]))
            except Exception:
                _links_tok = None

            try:
                if rotation_info:
                    yield _sse({"type": "rotation", "data": {
                        "occurred": True,
                        "old_thread_id": rotation_info["old_thread_id"],
                        "new_thread_id": rotation_info["new_thread_id"],
                        "new_thread": rotation_info["new_thread"],
                        "memories_extracted": rotation_info["memories_extracted"],
                        "old_message_count": rotation_info["old_message_count"],
                    }})

                yield _sse({"type": "status", "text": "Thinking..."})
                _update_channel_status(ch, step="Thinking...")

                async with get_checkpointer() as checkpointer:
                    graph = await get_channel_graph(ch, checkpointer)
                    cfg = {"configurable": {"thread_id": thread_id},
                           "recursion_limit": CHAT_RECURSION_LIMIT}  # #D26
                    # Include input_mode in the message metadata so the agent knows
                    # whether the operator is typing, dictating, or in voice mode.
                    # Voice/dictate → agent should respond more concisely (spoken output).
                    msg_kwargs = {"created_at": now_iso, "input_mode": input_mode}
                    graph_input = {
                        "messages": [HumanMessage(content=user_message, additional_kwargs=msg_kwargs)],
                        "agent_id": agent_id, "channel": ch,
                        "input_mode": input_mode,
                        "agent_identity": presence_identity,
                    }

                    response_text = "(no response)"
                    model_name = ""
                    thinking_text = None
                    step_count = 0
                    last_heartbeat = _time.time()

                    _SENTINEL = object()

                    async def _next_event(ai):
                        try:
                            return await ai.__anext__()
                        except StopAsyncIteration:
                            return _SENTINEL

                    aiter = graph.astream(graph_input, config=cfg).__aiter__()
                    pending_task = None

                    while True:
                        if _cancel_event.is_set():
                            if pending_task and not pending_task.done():
                                pending_task.cancel()
                            yield _sse({"type": "cancelled"})
                            yield _sse({"type": "done", "data": {"response": "Stopped.", "cancelled": True}})
                            return

                        if pending_task is None:
                            pending_task = asyncio.ensure_future(_next_event(aiter))

                        done, _ = await asyncio.wait(
                            [pending_task], timeout=_HEARTBEAT_INTERVAL
                        )

                        if not done:
                            started = _channel_status.get(ch, {}).get("started_at", _time.time())
                            elapsed = round(_time.time() - started, 1)
                            yield _sse({"type": "heartbeat", "elapsed": elapsed,
                                        "step": _channel_status.get(ch, {}).get("step", "Working...")})
                            last_heartbeat = _time.time()
                            continue

                        event = pending_task.result()
                        pending_task = None
                        if event is _SENTINEL:
                            break

                        for node_name, state_update in event.items():
                            step_count += 1
                            msgs = state_update.get("messages", [])

                            if node_name == "agent":
                                for msg in msgs:
                                    meta = getattr(msg, "response_metadata", {}) or {}
                                    extra = getattr(msg, "additional_kwargs", {}) or {}
                                    msg_model = (
                                        meta.get("model_name") or meta.get("model")
                                        or meta.get("model_id") or ""
                                    )
                                    if msg_model:
                                        model_name = msg_model

                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        for tc in msg.tool_calls:
                                            args_preview = ", ".join(
                                                f"{k}={repr(v)[:60]}" for k, v in tc.get("args", {}).items()
                                            )
                                            yield _sse({"type": "tool_call", "tool": tc["name"], "args": args_preview})
                                            _update_channel_status(ch, step=f"Calling {tc['name']}...",
                                                                   step_count=step_count)
                                            _add_activity_step(ch, f"Tool: {tc['name']}({args_preview})")
                                    else:
                                        raw_content = getattr(msg, "content", "")
                                        if raw_content:
                                            response_text, thinking_text = _extract_thinking(raw_content)
                                        if not thinking_text:
                                            thinking_text = (
                                                extra.get("reasoning_content")
                                                or extra.get("reasoning")
                                                or meta.get("reasoning_content")
                                                or meta.get("reasoning")
                                                or None
                                            )
                                        _update_channel_status(ch, step="Composing response...",
                                                               step_count=step_count)

                            elif node_name == "tools":
                                for msg in msgs:
                                    content = getattr(msg, "content", "")
                                    preview = content[:120] + "..." if len(content) > 120 else content
                                    yield _sse({"type": "tool_result", "preview": preview})
                                    _add_activity_step(ch, f"Result: {preview}")
                                yield _sse({"type": "status", "text": "Processing results..."})
                                _update_channel_status(ch, step="Processing results...",
                                                       step_count=step_count)

                        if _time.time() - last_heartbeat >= _HEARTBEAT_INTERVAL:
                            elapsed = round(_time.time() - _channel_status.get(ch, {}).get("started_at", _time.time()), 1)
                            yield _sse({"type": "heartbeat", "elapsed": elapsed,
                                        "step": _channel_status.get(ch, {}).get("step", "Working...")})
                            last_heartbeat = _time.time()

                yield _sse({"type": "done", "data": {
                    "thread_id": thread_id,
                    "response": response_text,
                    "model": model_name,
                    "thinking": thinking_text,
                    "channel": ch,
                }})

            except asyncio.CancelledError:
                yield _sse({"type": "done", "data": {"response": "Stopped.", "cancelled": True}})
            except GraphRecursionError:
                # #D26: the turn genuinely hit the step ceiling — say so clearly instead
                # of a generic error (which used to get misread as a model failure).
                msg = ("This turn hit its step ceiling (too many tool rounds in one go). "
                       "Continue, or split the ask into smaller steps.")
                yield _sse({"type": "error", "message": msg, "code": "recursion_limit"})
                yield _sse({"type": "done", "data": {"response": msg, "error": msg,
                                                     "recursion_limit": True}})
            except Exception as e:
                yield _sse({"type": "error", "message": str(e)})
                yield _sse({"type": "done", "data": {"response": f"Error: {e}", "error": str(e)}})
            finally:
                _running_tasks.pop(ch, None)
                await _persist_activity_steps(ch, thread_id=thread_id)
                _clear_channel_status(ch)
                try:
                    from src.memory.threads import update_thread_stats
                    await update_thread_stats(thread_id, message_count=pre_send_msg_count + 2)
                except Exception as e:
                    print(f"[chat] Thread stats update failed (non-fatal): {e}")
                exit_channel_db_scope(_stream_scope)
                try:
                    from src.models.provider import clear_request_byok
                    clear_request_byok(_byok_tok)
                except Exception:
                    pass
                try:
                    from src.tools.nextcloud_tools import clear_request_nc_creds
                    if _nc_tok is not None:
                        clear_request_nc_creds(_nc_tok)
                except Exception:
                    pass
                try:
                    from src.tools.links_tools import clear_request_links_presence
                    if _links_tok is not None:
                        clear_request_links_presence(_links_tok)
                except Exception:
                    pass

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Cancel
# =============================================================================

@router.post("/api/chat/cancel")
async def cancel_chat(request: Request):
    """Cancel a running send request."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    ch = body.get("channel", "") or get_default_channel()
    cancel_event = _running_tasks.get(ch)
    if cancel_event and isinstance(cancel_event, asyncio.Event):
        cancel_event.set()
        return {"cancelled": True, "channel": ch}
    return {"cancelled": False, "reason": "no active request"}


# =============================================================================
# Reset Thread
# =============================================================================

@router.delete("/api/chat/reset")
async def reset_chat(request: Request, channel: str = ""):
    """Start a new chat thread (archives current, creates fresh).

    Generates a summary + extracts memories from the current thread before
    archiving, so the next thread can seed with continuity context.
    """
    ch = channel or get_default_channel()
    # Same scope resolution as send/history: steward/merchant channels scope
    # per-presence (_manager_thread_scope), personal channels to the presence's
    # own agent. _personal_agent_id here archived/created the WRONG thread for
    # manager channels on presences whose agent_identity is empty.
    agent_id = await resolve_list_agent_id(ch, request)
    _scope = enter_channel_db_scope(ch)
    try:
        from src.memory.memory import store_memory
        from src.memory.threads import (
            get_active_thread, archive_thread, create_thread,
            generate_thread_summary,
        )

        active = await get_active_thread(ch, agent_id)
        summary_stored = False

        if active:
            # Generate and store summary BEFORE archiving so the next thread
            # can pick up continuity. archive_thread does extraction but not summary.
            try:
                from src.memory.checkpointer import get_checkpointer
                from src.graphs.channels import get_channel_graph

                async with get_checkpointer() as checkpointer:
                    graph = await get_channel_graph(ch, checkpointer)
                    config = {"configurable": {"thread_id": active["thread_id"]}}
                    snapshot = await graph.aget_state(config)

                messages = []
                if snapshot and snapshot.values:
                    messages = snapshot.values.get("messages", [])

                if messages and len(messages) > 2:
                    summary = await generate_thread_summary(
                        messages, active["thread_id"], ch, agent_id
                    )
                    if summary and len(summary) > 50:
                        # Use steward agent_id for steward channels (shared memory pool)
                        from src.memory.memory import _memory_agent_id
                        mem_agent = _memory_agent_id(ch, agent_id)
                        # Store summary as memory for future loading
                        await store_memory(
                            content=f"[Thread summary — {active['thread_id']}] {summary}",
                            category="context",
                            importance=0.85,
                            tags=["thread-summary", ch],
                            agent_id=mem_agent,
                            source_thread=active["thread_id"],
                            source_channel=ch,
                            source_summary=f"Summary from manual thread reset ({len(messages)} messages)",
                        )
                        summary_stored = True

                        # Also store summary on the thread record for seeding
                        from src.memory.database import get_db
                        async with get_db() as conn:
                            await conn.execute(
                                """UPDATE chat_threads SET summary = %s
                                   WHERE thread_id = %s""",
                                (summary, active["thread_id"]),
                            )
            except Exception as e:
                print(f"[chat] Pre-archive summary failed (non-fatal): {e}")

            await archive_thread(active["thread_id"], agent_id=agent_id)

        new_thread = await create_thread(channel=ch, agent_id=agent_id)
        return {
            "success": True,
            "channel": ch,
            "new_thread_id": new_thread["thread_id"],
            "summary_stored": summary_stored,
            "message": "Thread archived with summary and fresh thread created.",
        }
    except Exception as e:
        return {"success": True, "message": f"Reset attempted: {e}"}
    finally:
        exit_channel_db_scope(_scope)
