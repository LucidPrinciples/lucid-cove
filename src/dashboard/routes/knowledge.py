# =============================================================================
# knowledge.py — Ezra Knowledge (Functional Health)
#
# Spec: AgentSkills/Working/Specs/ezra-knowledge-v1-2026-08-13.md
# Product: Ollama model pick + focused sessions (like Model Lab) for a specific
# domain (Functional Health). Uses Neo-Dolphin-Mistral-7B-GGUF as the pinned model.
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

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.env import env

log = logging.getLogger("knowledge")

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")

_RUN_TIMEOUT_S = 180
# Ollama knobs — keep_alive=0 unloads weights after each call
_KB_KEEP_ALIVE = "0"
_KB_NUM_CTX = 8192
_MAX_PROMPT_CHARS = 12000
_MAX_SYSTEM_CHARS = 8000
_MAX_TITLE = 200
# Pinned model for Functional Health
_PINNED_MODEL_TAG = "hf.co/mishmashly/Neo-Dolphin-Mistral-7B-GGUF:latest"
_ROTATION_THRESHOLD = 40
_DEFAULT_DIRECTION = (
    "You are a Functional Health research partner. Stay on health, recovery, "
    "nutrition, labs, and training. Be direct and source-honest. If you are "
    "not sure, say so. Do not manage family logistics, calendars, or Cove ops. "
    "This room is isolated from Day and Deep."
)

# Regex for stripping thinking blocks (copied from model_lab)
_THINK_RE = re.compile(
    r"<think>.*?</think>|"
    r"<thinking>.*?</thinking>|"
    r"<reasoning>.*?</reasoning>",
    flags=re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN_RE = re.compile(
    r"<think>.*$|"
    r"<thinking>.*$|"
    r"<reasoning>.*$",
    flags=re.DOTALL | re.IGNORECASE,
)

def _split_model_output(text: str) -> tuple[str, str]:
    """Split model output into (answer, thinking) for display."""
    if not text:
        return "", ""
    raw = text
    thinking_parts: list[str] = []
    for m in _THINK_RE.finditer(raw):
        block = m.group(0)
        inner = re.sub(
            r"^</?(?:think|thinking|reasoning)>|</(?:think|thinking|reasoning)>$",
            "",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        inner = re.sub(r"^<[^>]+>", "", block)
        inner = re.sub(r"</[^>]+>$", "", inner)
        thinking_parts.append(inner.strip())
    answer = _THINK_RE.sub("", raw)
    open_m = _THINK_OPEN_RE.search(answer)
    if open_m:
        thinking_parts.append(re.sub(r"^<[^>]+>", "", open_m.group(0)).strip())
        answer = answer[: open_m.start()]
    answer = answer.strip()
    thinking = "\n\n".join(p for p in thinking_parts if p).strip()
    return answer, thinking

def _strip_think_blocks(text: str) -> str:
    """Answer-only view (history / next-turn context)."""
    answer, _thinking = _split_model_output(text)
    return answer


def _extractive_summary(history) -> str:
    """Continuity briefing without steward memory or a second model call."""
    lines = []
    for h in history or []:
        role = (h.get("role") if isinstance(h, dict) else None) or ""
        text = ((h.get("content") if isinstance(h, dict) else "") or "").strip()
        if not text or role not in ("user", "assistant"):
            continue
        if role == "assistant":
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

def _message_text(content) -> str:
    """Normalize LangChain / Ollama content to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text") or block.get("content") or ""
                if t:
                    parts.append(str(t))
            else:
                t = getattr(block, "text", None) or getattr(block, "content", None)
                if t:
                    parts.append(str(t))
        return "\n".join(parts)
    return str(content)


# =============================================================================
# Helpers (copied from model_lab)
# =============================================================================

async def _get_presence_id(request: Request):
    if COVE_MODE != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        return presence["id"] if presence else None
    except Exception:
        return None

def _presence_filter(presence_id):
    if presence_id:
        return "presence_id = %s", (presence_id,)
    return "(presence_id IS NULL OR presence_id = 0)", ()

def _row(r) -> dict:
    if not r:
        return {}
    out = dict(r)
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out

def _clamp_temp(raw, default: float = 0.7) -> float:
    try:
        t = float(raw if raw is not None else default)
    except (TypeError, ValueError):
        t = default
    return max(0.0, min(2.0, t))

def _kb_ollama_client(model_tag: str, temperature: float):
    """ChatOllama pinned for Knowledge: unload after generate, modest context."""
    from langchain_ollama import ChatOllama
    from src.models.provider import _ollama_base_url

    return ChatOllama(
        model=model_tag,
        base_url=_ollama_base_url(),
        temperature=temperature,
        num_ctx=_KB_NUM_CTX,
        timeout=_RUN_TIMEOUT_S,
        keep_alive=_KB_KEEP_ALIVE,
    )

async def _unload_ollama_tag(model_tag: str) -> None:
    """Best-effort: drop model from VRAM immediately (heat / multi-model safety)."""
    tag = (model_tag or "").strip()
    if not tag:
        return
    try:
        import httpx
        from src.models.provider import _ollama_base_url

        base = (_ollama_base_url() or "").rstrip("/")
        if not base:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{base}/api/generate",
                json={"model": tag, "prompt": "", "keep_alive": 0},
            )
    except Exception as e:
        log.debug("ollama unload %s: %s", tag, e)

async def _invoke_ollama_tag(
    *,
    model_tag: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    label: str,
) -> tuple[str, int]:
    """Pin to one Ollama tag. No cloud hop, no agent assignment chain."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from src.models.provider import _write_jw_metric

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
            raw = _message_text(getattr(response, "content", None)).strip()
            if not raw:
                raise RuntimeError(
                    f"empty response from {model_tag} "
                    f"(model returned no text — try again, check VRAM, or pick another tag)"
                )
            usage = getattr(response, "usage_metadata", {}) or {}
            meta = getattr(response, "response_metadata", {}) or {}
            await _write_jw_metric(
                agent_id="ezra", # Pinned to Ezra
                operation_type="knowledge-session",
                operation_label=label,
                model_used=model_tag,
                provider="ollama",
                tokens_in=usage.get("prompt_eval_count") or meta.get("prompt_eval_count"),
                tokens_out=usage.get("eval_count") or meta.get("eval_count"),
                duration_ms=duration_ms,
                succeeded=True,
            )
            return raw, duration_ms
        except Exception:
            duration_ms = int((time.monotonic() - t0) * 1000)
            try:
                await _write_jw_metric(
                    agent_id="ezra",
                    operation_type="knowledge-session",
                    operation_label=label,
                    model_used=model_tag,
                    provider="ollama",
                    tokens_in=0,
                    tokens_out=0,
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

@router.get("/api/knowledge/sessions")
async def list_knowledge_sessions(request: Request, status: str = ""):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    st = (status or "").strip().lower()
    if st and st != "all":
        where += " AND status = %s"
        params += (st,)
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

    title = (body.get("title") or "").strip()[:_MAX_TITLE] or "Knowledge Session"
    system_prompt = (body.get("system_prompt") or "").strip()[:_MAX_SYSTEM_CHARS]
    if not system_prompt:
        system_prompt = _DEFAULT_DIRECTION
    temperature = _clamp_temp(body.get("temperature"), 0.7)
    presence_id = await _get_presence_id(request)

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO knowledge_sessions
                   (presence_id, title, model_tag, system_prompt, temperature, status)
                   VALUES (%s, %s, %s, %s, %s, 'open') RETURNING *""",
                (presence_id, title, _PINNED_MODEL_TAG, system_prompt, temperature),
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
            m["content"] = answer if (answer or thinking) else m["content"]
            if not answer and thinking:
                m["content"] = ""
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
                       (presence_id, title, model_tag, system_prompt, temperature, status, notes)
                       VALUES (%s, %s, %s, %s, %s, 'open', %s) RETURNING *""",
                    (
                        session.get("presence_id"),
                        title[:_MAX_TITLE],
                        session.get("model_tag") or _PINNED_MODEL_TAG,
                        session.get("system_prompt") or _DEFAULT_DIRECTION,
                        session.get("temperature") or 0.7,
                        (summary or "")[:4000],
                    ),
                )
                new_session = await new_r.fetchone()
                if summary:
                    seed = (
                        f"[Thread continuation from session {session_id}]\n\n"
                        f"{summary}\n\nContinue this Functional Health thread. "
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
        item["content"] = answer if (answer or thinking) else item["content"]
        if not answer and thinking:
            item["content"] = ""
    if err and not reply:
        return JSONResponse(
            {"ok": False, "error": err, "item": item, "rotation": rotation},
            status_code=502,
        )
    return {
        "ok": True,
        "item": item,
        "latency_ms": latency_ms,
        "rotation": rotation,
        "session_id": session_id,
    }