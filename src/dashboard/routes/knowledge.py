# =============================================================================
# knowledge.py — Ezra Knowledge (Functional Health)
#
# Spec: AgentSkills/Working/Specs/ezra-knowledge-v1-2026-08-13.md
# Product: Installable domain threads (Functional Health, Inventions).
# Uses Neo-Dolphin-Mistral-7B-GGUF as the pinned model for Functional Health.
# Does NOT write agent/steward memory directly, but conversations can be summarized
# and extracted.
#
# Ezra:Ezra — MC Tools card + /knowledge page.
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from src.env import env
from src.dashboard.routes.model_lab import (
    _get_presence_id,
    _presence_filter,
    _row,
    _clamp_temp,
    _message_text,
    _split_model_output,
    _strip_think_blocks,
    _unload_ollama_tag,
    _lab_ollama_client as _kb_ollama_client, # Reuse Lab's client with keep_alive=0
    _RUN_TIMEOUT_S,
    _LAB_NUM_CTX as _KB_NUM_CTX,
    _MAX_PROMPT_CHARS,
    _MAX_SYSTEM_CHARS,
    _MAX_TITLE,
)
from src.models.provider import _write_jw_metric
from langchain_core.messages import HumanMessage, SystemMessage

log = logging.getLogger("knowledge")

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")

# Pinned model for Functional Health
_PINNED_MODEL_TAG = "hf.co/mishmashly/Neo-Dolphin-Mistral-7B-GGUF:latest"
_ROTATION_THRESHOLD = 40
_DEFAULT_DIRECTION_FH = (
    "You are a Functional Health research partner. Stay on health, recovery, "
    "nutrition, labs, and training. Be direct and source-honest. If you are "
    "not sure, say so. Do not manage family logistics, calendars, or Cove ops. "
    "This room is isolated from Day and Deep."
)
# Alias kept for tests and older callers.
_DEFAULT_DIRECTION = _DEFAULT_DIRECTION_FH
_DEFAULT_DIRECTION_INV = (
    "You are an Inventions research partner. Stay on product design, market "
    "analysis, patent research, and technical feasibility. Be direct and "
    "source-honest. If you are not sure, say so. Do not manage family "
    "logistics, calendars, or Cove ops. This room is isolated from Day and Deep."
)

_THREAD_KINDS = {
    "functional-health": {
        "id": "functional-health",
        "title": "Functional Health",
        "model_tag": _PINNED_MODEL_TAG,
        "system_prompt": _DEFAULT_DIRECTION_FH,
        "icon": "⚕️",
    },
    "inventions": {
        "id": "inventions",
        "title": "Inventions",
        "model_tag": _PINNED_MODEL_TAG, # Can be changed later if a specific model is fine-tuned for inventions
        "system_prompt": _DEFAULT_DIRECTION_INV,
        "icon": "💡",
    },
}

def _extractive_summary(history) -> str:
    """Continuity briefing without steward memory or a second model call."""
    lines = []
    for h in history or []:
        role = (h.get("role") if isinstance(h, dict) else None) or ""
        text = ((h.get("content") if isinstance(h, dict) else "") or "").strip()
        if not text or role not in ("user", "assistant"):
            continue
        if role == "assistant":
            # Ensure thinking blocks are stripped for summary, but keep actual content
            text = _strip_think_blocks(text) or text
        if len(text) > 360:
            text = text[:360] + "…"
        lines.append(f"{role}: {text}")
    if not lines:
        return ""
    if len(lines) <= 16:
        body = lines
    else:
        body = lines[:6] + ["…"] + lines[-10:]
    return "Summary of previous Knowledge session:\n" + "\n".join(body)

async def _invoke_ollama_tag(
    *,
    model_tag: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    label: str,
) -> tuple[str, int]:
    """Pin to one Ollama tag. No cloud hop, no agent assignment chain."""
    messages = []
    sys = (system_prompt or "").strip()
    if sys:
        messages.append(SystemMessage(content=sys[:_MAX_SYSTEM_CHARS]))
    messages.append(HumanMessage(content=(user_prompt or "")[:_MAX_PROMPT_CHARS]))

    client = _kb_ollama_client(model_tag, temperature)
    t0 = time.monotonic()
    try:
        try:
            response = await asyncio.wait_for(
                client.ainvoke(messages), timeout=_RUN_TIMEOUT_S
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            raw_content = getattr(response, "content", None)
            
            # If the response is truly empty (no content at all), raise an error.
            # Otherwise, return the raw content for _split_model_output to parse,
            # even if it's just thinking blocks.
            if raw_content is None or (isinstance(raw_content, str) and not raw_content.strip()):
                 raise RuntimeError(
                    f"empty response from {model_tag} "
                    f"(model returned no text — try again, check VRAM, or pick another tag)"
                )
            # Return raw content here, _split_model_output will parse thinking
            return raw_content, duration_ms
        except Exception:
            duration_ms = int((time.monotonic() - t0) * 1000)
            try:
                await _write_jw_metric(
                    agent_id="ezra",
                    operation_type="knowledge-session",
                    operation_label=label,
                    model_used=model_tag,
                    provider="ollama",
                    tokens_in=0, # Placeholder, actual usage needs to be extracted from response
                    tokens_out=0, # Placeholder
                    duration_ms=duration_ms,
                    succeeded=False,
                )
            except Exception:
                pass
            raise
    finally:
        await _unload_ollama_tag(model_tag)

def _session_owned_sql(presence_id):
    where, params = _presence_filter(presence_id)
    return where, params

# =============================================================================
# Page
# =============================================================================

@router.get("/knowledge", response_class=HTMLResponse)
async def serve_knowledge_page(request: Request):
    static_dir = Path(__file__).parent.parent / "static"
    path = static_dir / "knowledge.html"
    if not path.exists():
        return HTMLResponse("<h1>Knowledge page not found</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))

# =============================================================================
# Sessions
# =============================================================================

@router.get("/api/knowledge/threads")
async def list_knowledge_threads(request: Request):
    return {"ok": True, "items": list(_THREAD_KINDS.values())}

@router.get("/api/knowledge/sessions")
async def list_knowledge_sessions(request: Request, status: str = "", kind: str = ""):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    st = (status or "").strip().lower()
    if st and st != "all":
        where += " AND status = %s"
        params += (st,)
    
    thread_kind = (kind or "").strip().lower()
    if thread_kind:
        where += " AND thread_kind = %s"
        params += (thread_kind,)

    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"""SELECT * FROM knowledge_sessions
                   WHERE {where}
                   ORDER BY updated_at DESC LIMIT 100""",
                params,
            )
            rows = await r.fetchall()
    except Exception as e:
        log.warning("list knowledge sessions: %s", e)
        return {"ok": False, "items": [], "error": "knowledge tables missing — run migration 046"}
    return {"ok": True, "items": [_row(x) for x in rows]}

@router.post("/api/knowledge/sessions")
async def create_knowledge_session(request: Request):
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    thread_kind = (body.get("thread_kind") or "").strip().lower()
    if not thread_kind or thread_kind not in _THREAD_KINDS:
        return JSONResponse({"ok": False, "error": "Invalid thread_kind"}, status_code=400)

    kind_config = _THREAD_KINDS[thread_kind]

    title = (body.get("title") or "").strip()[:_MAX_TITLE] or kind_config["title"]
    system_prompt = (body.get("system_prompt") or "").strip()[:_MAX_SYSTEM_CHARS] or kind_config["system_prompt"]
    temperature = _clamp_temp(body.get("temperature"), 0.7)
    presence_id = await _get_presence_id(request)

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO knowledge_sessions
                   (presence_id, title, model_tag, system_prompt, temperature, status, thread_kind)
                   VALUES (%s, %s, %s, %s, %s, 'open', %s) RETURNING *""",
                (presence_id, title, kind_config["model_tag"], system_prompt, temperature, thread_kind),
            )
            row = await r.fetchone()
    except Exception as e:
        log.exception("create knowledge session")
        return JSONResponse(
            {"ok": False, "error": f"DB error (migration 046 applied?): {e}"},
            status_code=500,
        )
    return {"ok": True, "item": _row(row)}

@router.get("/api/knowledge/sessions/{session_id}")
async def get_knowledge_session(request: Request, session_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM knowledge_sessions WHERE id = %s AND {where}",
                (session_id,) + params,
            )
            session = await r.fetchone()
            if not session:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            m = await conn.execute(
                """SELECT * FROM knowledge_messages
                   WHERE session_id = %s ORDER BY id ASC LIMIT 500""",
                (session_id,),
            )
            messages = await m.fetchall()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    cleaned = []
    for x in messages:
        m = _row(x)
        if m.get("role") == "assistant" and m.get("content"):
            answer, thinking = _split_model_output(m["content"])
            m["raw_content"] = m["content"]
            m["thinking"] = thinking
            # Think-only / reasoning-only output is usable text, not an empty reply.
            m["content"] = answer or thinking or m["content"]
        cleaned.append(m)
    return {
        "ok": True,
        "item": _row(session),
        "messages": cleaned,
    }

@router.patch("/api/knowledge/sessions/{session_id}")
async def update_knowledge_session(request: Request, session_id: int):
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    sets = []
    vals: list = []

    if "title" in body:
        sets.append("title = %s")
        vals.append((body.get("title") or "").strip()[:_MAX_TITLE])
    if "system_prompt" in body:
        sets.append("system_prompt = %s")
        vals.append((body.get("system_prompt") or "").strip()[:_MAX_SYSTEM_CHARS])
    if "temperature" in body:
        sets.append("temperature = %s")
        vals.append(_clamp_temp(body.get("temperature"), 0.7))
    if "notes" in body:
        sets.append("notes = %s")
        vals.append((body.get("notes") or "").strip()[:4000])
    if "status" in body:
        st = (body.get("status") or "").strip().lower()
        if st not in ("open", "closed"):
            return JSONResponse({"ok": False, "error": "status must be open|closed"}, status_code=400)
        sets.append("status = %s")
        vals.append(st)
        if st == "closed":
            sets.append("closed_at = NOW()")
        else:
            sets.append("closed_at = NULL")

    if not sets:
        return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)

    sets.append("updated_at = NOW()")
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"""UPDATE knowledge_sessions SET {', '.join(sets)}
                    WHERE id = %s AND {where} RETURNING *""",
                tuple(vals) + (session_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        log.exception("update knowledge session")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if (row.get("status") or "") == "closed":
        await _unload_ollama_tag(row.get("model_tag") or "")
    return {"ok": True, "item": _row(row)}

@router.post("/api/knowledge/sessions/{session_id}/chat")
async def knowledge_session_chat(request: Request, session_id: int):
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    content = (body.get("content") or body.get("message") or "").strip()
    if not content:
        return JSONResponse({"ok": False, "error": "message is required"}, status_code=400)
    content = content[:_MAX_PROMPT_CHARS]

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    rotation = None

    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM knowledge_sessions WHERE id = %s AND {where}",
                (session_id,) + params,
            )
            session = await r.fetchone()
            if not session:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            if session.get("status") != "open":
                return JSONResponse(
                    {"ok": False, "error": "session is closed — reopen to chat"},
                    status_code=400,
                )

            count_r = await conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge_messages WHERE session_id = %s",
                (session_id,),
            )
            count_row = await count_r.fetchone()
            prior_n = int((count_row or {}).get("n") or 0)

            if prior_n >= _ROTATION_THRESHOLD:
                old_hist = await conn.execute(
                    """SELECT role, content FROM knowledge_messages
                       WHERE session_id = %s ORDER BY id ASC LIMIT 80""",
                    (session_id,),
                )
                old_msgs = await old_hist.fetchall()
                summary = _extractive_summary(old_msgs)
                await conn.execute(
                    """UPDATE knowledge_sessions
                       SET status = 'closed', closed_at = NOW(),
                           notes = %s, updated_at = NOW()
                       WHERE id = %s""",
                    ((summary or "")[:4000], session_id),
                )
                title = (session.get("title") or "Knowledge Session").strip()
                if not title.endswith("(cont.)"):
                    title = (title[: _MAX_TITLE - 8] + " (cont.)").strip()
                new_r = await conn.execute(
                    """INSERT INTO knowledge_sessions
                       (presence_id, title, model_tag, system_prompt, temperature, status, notes, thread_kind)
                       VALUES (%s, %s, %s, %s, %s, 'open', %s, %s) RETURNING *""",
                    (
                        session.get("presence_id"),
                        title[:_MAX_TITLE],
                        session.get("model_tag") or _PINNED_MODEL_TAG,
                        session.get("system_prompt") or _DEFAULT_DIRECTION_FH, # Use FH default if none
                        session.get("temperature") or 0.7,
                        (summary or "")[:4000],
                        session.get("thread_kind") or "functional-health",
                    ),
                )
                new_session = await new_r.fetchone()
                if summary:
                    # Use the specific system prompt for the new thread kind
                    thread_kind = new_session.get("thread_kind", "functional-health")
                    kind_config = _THREAD_KINDS.get(thread_kind, _THREAD_KINDS["functional-health"])
                    seed = (
                        f"[Thread continuation from session {session_id}]\n\n"
                        f"{summary}\n\nContinue this {kind_config['title']} thread. "
                        "Do not pick up family Day or Deep work."
                    )
                    await conn.execute(
                        """INSERT INTO knowledge_messages (session_id, role, content)
                           VALUES (%s, 'system', %s)""",
                        (new_session["id"], seed[:_MAX_PROMPT_CHARS]),
                    )
                rotation = {
                    "rotated": True,
                    "old_session_id": session_id,
                    "new_session_id": new_session["id"],
                }
                session = new_session
                session_id = new_session["id"]

            await conn.execute(
                """INSERT INTO knowledge_messages (session_id, role, content)
                   VALUES (%s, 'user', %s)""",
                (session_id, content),
            )
            hist = await conn.execute(
                """SELECT role, content FROM knowledge_messages
                   WHERE session_id = %s ORDER BY id ASC LIMIT 40""",
                (session_id,),
            )
            history = await hist.fetchall()
    except Exception as e:
        log.exception("knowledge session chat load")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)

    turns = []
    for h in history:
        role = h.get("role") or ""
        text = (h.get("content") or "").strip()
        if not text:
            continue
        if role == "user":
            turns.append(f"User: {text}")
        elif role == "assistant":
            # For context, assistant replies should be stripped of thinking blocks
            cleaned = _strip_think_blocks(text) or text
            turns.append(f"Assistant: {cleaned}")
    user_blob = "\n\n".join(turns) if turns else content

    try:
        reply, latency_ms = await _invoke_ollama_tag(
            model_tag=session["model_tag"],
            system_prompt=session.get("system_prompt") or "",
            user_prompt=user_blob,
            temperature=float(session.get("temperature") or 0.7),
            label=f"knowledge/session#{session_id}",
        )
        err = ""
    except Exception as e:
        log.exception("knowledge session chat invoke")
        reply, latency_ms, err = "", 0, str(e)[:500]

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO knowledge_messages
                   (session_id, role, content, model_tag, latency_ms, error)
                   VALUES (%s, 'assistant', %s, %s, %s, %s) RETURNING *""",
                (
                    session_id,
                    reply or (f"[error] {err}" if err else ""),
                    session["model_tag"],
                    latency_ms or None,
                    err,
                ),
            )
            asst = await r.fetchone()
            await conn.execute(
                "UPDATE knowledge_sessions SET updated_at = NOW() WHERE id = %s",
                (session_id,),
            )
    except Exception as e:
        log.exception("knowledge session chat save")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)

    item = _row(asst)
    if item.get("content"):
        answer, thinking = _split_model_output(item["content"])
        item["raw_content"] = item["content"]
        item["thinking"] = thinking
        item["content"] = answer or thinking or item["content"]
        
    if err and not reply:
        return JSONResponse(
            {"ok": False, "error": err, "item": item, "rotation": rotation},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    return {
        "ok": True,
        "item": item,
        "latency_ms": latency_ms,
        "rotation": rotation,
        "session_id": session_id,
    }