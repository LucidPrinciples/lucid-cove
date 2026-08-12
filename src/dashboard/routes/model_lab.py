# =============================================================================
# model_lab.py — Soren Model Lab + Tester (#MODELLAB1 v1)
#
# Spec: AgentSkills/Working/Specs/model-lab-v1-2026-08-11.md
# Product: Ollama model pick + focused sessions + structured single/A-B runs.
# Does NOT write agent/steward memory. Promote → Ezra Knowledge is stage 2.
#
# Jules:Julian :: Gabs:Gabe :: Model Lab:Soren — MC Tools card + /model-lab page.
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

log = logging.getLogger("model_lab")

router = APIRouter()

COVE_MODE = env("COVE_MODE", "single")

_RUN_TIMEOUT_S = 180
# Lab-only Ollama knobs — do not change global chat provider defaults.
# keep_alive=0 unloads weights after each call so the GPU does not stay hot
# between Lab turns (P620 overheat lesson). Smaller ctx than production chat.
_LAB_KEEP_ALIVE = "0"
_LAB_NUM_CTX = 8192
_MAX_PROMPT_CHARS = 12000
_MAX_SYSTEM_CHARS = 8000
_MAX_TITLE = 200
# Ollama tags + HF-style pulls (hf.co/org/name:tag). Allow / but not path junk.
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$")


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
    """Split model output into (answer, thinking) for Lab testing.

    Thinking is kept for inspection — Lab is an evaluation surface.
    Answer is the operator-facing body with wrappers removed.
    """
    if not text:
        return "", ""
    raw = text
    thinking_parts: list[str] = []
    for m in _THINK_RE.finditer(raw):
        block = m.group(0)
        # peel tags
        inner = re.sub(
            r"^</?(?:think|thinking|reasoning)>|</(?:think|thinking|reasoning)>$",
            "",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # simpler: strip first/last tag lines
        inner = re.sub(r"^<[^>]+>", "", block)
        inner = re.sub(r"</[^>]+>$", "", inner)
        thinking_parts.append(inner.strip())
    answer = _THINK_RE.sub("", raw)
    # Unclosed trailing think → all thinking, no answer
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




# =============================================================================
# Helpers
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


def _valid_tag(tag: str) -> tuple[bool, str]:
    t = (tag or "").strip()
    if not t:
        return False, "model tag is required"
    if ".." in t or t.startswith("/") or "//" in t:
        return False, "invalid model tag"
    if not _TAG_RE.match(t):
        return False, "invalid model tag"
    return True, t


def _clamp_temp(raw, default: float = 0.7) -> float:
    try:
        t = float(raw if raw is not None else default)
    except (TypeError, ValueError):
        t = default
    return max(0.0, min(2.0, t))


async def _get_ollama_models() -> dict:
    """Live Ollama inventory. Never invents tags."""
    from src.models.machine_probe import probe_local_providers

    providers = await probe_local_providers()
    ollama = next((p for p in providers if p.get("id") == "ollama"), None)
    if not ollama:
        return {
            "reachable": False,
            "url": "",
            "models": [],
            "error": "Ollama provider not configured",
            "hint": "",
        }
    models = []
    for m in ollama.get("models") or []:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        models.append({
            "name": name,
            "size_bytes": m.get("size_bytes"),
            "chat": bool(m.get("chat", True)),
        })
    models.sort(key=lambda x: (not x["chat"], x["name"].lower()))
    return {
        "reachable": bool(ollama.get("reachable")),
        "url": ollama.get("url") or "",
        "models": models,
        "error": ollama.get("error") or "",
        "hint": ollama.get("hint") or "",
    }


def _lab_ollama_client(model_tag: str, temperature: float):
    """ChatOllama pinned for Lab: unload after generate, modest context."""
    from langchain_ollama import ChatOllama
    from src.models.provider import _ollama_base_url

    return ChatOllama(
        model=model_tag,
        base_url=_ollama_base_url(),
        temperature=temperature,
        num_ctx=_LAB_NUM_CTX,
        timeout=_RUN_TIMEOUT_S,
        keep_alive=_LAB_KEEP_ALIVE,
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
        # Ollama unloads when keep_alive is 0 on a generate with empty prompt.
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

    client = _lab_ollama_client(model_tag, temperature)
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
                agent_id="soren",
                operation_type="model-lab",
                operation_label=label,
                model_used=model_tag,
                provider="ollama",
                tokens_in=usage.get("input_tokens") or meta.get("prompt_eval_count"),
                tokens_out=usage.get("output_tokens") or meta.get("eval_count"),
                duration_ms=duration_ms,
                succeeded=True,
            )
            # Prefer storing the full model output so thinking is not discarded.
            return raw, duration_ms
        except Exception:
            duration_ms = int((time.monotonic() - t0) * 1000)
            try:
                await _write_jw_metric(
                    agent_id="soren",
                    operation_type="model-lab",
                    operation_label=label,
                    model_used=model_tag,
                    provider="ollama",
                    tokens_in=None,
                    tokens_out=None,
                    duration_ms=duration_ms,
                    succeeded=False,
                )
            except Exception:
                pass
            raise
    finally:
        # Always try to free VRAM after Lab work (success or fail).
        await _unload_ollama_tag(model_tag)


def _session_owned_sql(presence_id):
    where, params = _presence_filter(presence_id)
    return where, params


# =============================================================================
# Page
# =============================================================================

@router.get("/model-lab", response_class=HTMLResponse)
async def serve_model_lab(request: Request):
    static_dir = Path(__file__).parent.parent / "static"
    path = static_dir / "model-lab.html"
    if not path.exists():
        return HTMLResponse("<h1>Model Lab not found</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# =============================================================================
# Inventory
# =============================================================================

@router.get("/api/model-lab/models")
async def list_models(request: Request):
    try:
        data = await _get_ollama_models()
        return {"ok": True, **data}
    except Exception as e:
        log.exception("list models")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


# =============================================================================
# Sessions
# =============================================================================

@router.get("/api/model-lab/sessions")
async def list_sessions(request: Request, status: str = ""):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    st = (status or "").strip().lower()
    try:
        async with get_db() as conn:
            if st in ("open", "closed"):
                r = await conn.execute(
                    f"""SELECT * FROM model_lab_sessions
                        WHERE {where} AND status = %s
                        ORDER BY updated_at DESC LIMIT 100""",
                    params + (st,),
                )
            else:
                r = await conn.execute(
                    f"""SELECT * FROM model_lab_sessions
                        WHERE {where}
                        ORDER BY updated_at DESC LIMIT 100""",
                    params,
                )
            rows = await r.fetchall()
    except Exception as e:
        log.warning("list sessions: %s", e)
        return {"items": [], "error": "model_lab tables missing — run migration 045"}
    return {"items": [_row(x) for x in rows]}


@router.post("/api/model-lab/sessions")
async def create_session(request: Request):
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    ok, tag_or_err = _valid_tag(body.get("model_tag") or "")
    if not ok:
        return JSONResponse({"ok": False, "error": tag_or_err}, status_code=400)
    title = (body.get("title") or "").strip()[:_MAX_TITLE] or tag_or_err
    system_prompt = (body.get("system_prompt") or "").strip()[:_MAX_SYSTEM_CHARS]
    temperature = _clamp_temp(body.get("temperature"), 0.7)
    presence_id = await _get_presence_id(request)

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO model_lab_sessions
                   (presence_id, title, model_tag, system_prompt, temperature, status)
                   VALUES (%s, %s, %s, %s, %s, 'open') RETURNING *""",
                (presence_id, title, tag_or_err, system_prompt, temperature),
            )
            row = await r.fetchone()
    except Exception as e:
        log.exception("create session")
        return JSONResponse(
            {"ok": False, "error": f"DB error (migration 045 applied?): {e}"},
            status_code=500,
        )
    return {"ok": True, "item": _row(row)}


@router.get("/api/model-lab/sessions/{session_id}")
async def get_session(request: Request, session_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM model_lab_sessions WHERE id = %s AND {where}",
                (session_id,) + params,
            )
            session = await r.fetchone()
            if not session:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            m = await conn.execute(
                """SELECT * FROM model_lab_messages
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
            # Primary bubble = answer when split worked; else full content
            m["content"] = answer if (answer or thinking) else m["content"]
            if not answer and thinking:
                m["content"] = ""  # thinking-only turn
        cleaned.append(m)
    return {
        "ok": True,
        "item": _row(session),
        "messages": cleaned,
    }


@router.patch("/api/model-lab/sessions/{session_id}")
async def update_session(request: Request, session_id: int):
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
    if "model_tag" in body:
        ok, tag_or_err = _valid_tag(body.get("model_tag") or "")
        if not ok:
            return JSONResponse({"ok": False, "error": tag_or_err}, status_code=400)
        sets.append("model_tag = %s")
        vals.append(tag_or_err)
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
                f"""UPDATE model_lab_sessions SET {', '.join(sets)}
                    WHERE id = %s AND {where} RETURNING *""",
                tuple(vals) + (session_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        log.exception("update session")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    # Closing a Lab session should free VRAM even if the last generate failed.
    if (row.get("status") or "") == "closed":
        await _unload_ollama_tag(row.get("model_tag") or "")
    return {"ok": True, "item": _row(row)}


@router.post("/api/model-lab/sessions/{session_id}/chat")
async def session_chat(request: Request, session_id: int):
    """Append user turn, invoke pinned Ollama tag, store assistant turn. Lab-only."""
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

    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM model_lab_sessions WHERE id = %s AND {where}",
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

            await conn.execute(
                """INSERT INTO model_lab_messages (session_id, role, content)
                   VALUES (%s, 'user', %s)""",
                (session_id, content),
            )
            hist = await conn.execute(
                """SELECT role, content FROM model_lab_messages
                   WHERE session_id = %s ORDER BY id ASC LIMIT 40""",
                (session_id,),
            )
            history = await hist.fetchall()
    except Exception as e:
        log.exception("session chat load")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)

    # Build multi-turn blob for the pinned model (still single user prompt + system).
    # Keep transcript simple: system from session + concatenated recent turns.
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
            label=f"model-lab/session#{session_id}",
        )
        err = ""
    except Exception as e:
        log.exception("session chat invoke")
        reply, latency_ms, err = "", 0, str(e)[:500]

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO model_lab_messages
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
                "UPDATE model_lab_sessions SET updated_at = NOW() WHERE id = %s",
                (session_id,),
            )
    except Exception as e:
        log.exception("session chat save")
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
            {"ok": False, "error": err, "item": item},
            status_code=502,
        )
    return {"ok": True, "item": item, "latency_ms": latency_ms}


# =============================================================================
# Tester runs (single + A/B)
# =============================================================================

@router.get("/api/model-lab/runs")
async def list_runs(request: Request, status: str = ""):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    st = (status or "").strip().lower()
    try:
        async with get_db() as conn:
            if st:
                r = await conn.execute(
                    f"""SELECT * FROM model_lab_runs
                        WHERE {where} AND status = %s
                        ORDER BY updated_at DESC LIMIT 100""",
                    params + (st,),
                )
            else:
                r = await conn.execute(
                    f"""SELECT * FROM model_lab_runs
                        WHERE {where}
                        ORDER BY
                          CASE status
                            WHEN 'running' THEN 0
                            WHEN 'queued' THEN 1
                            WHEN 'failed' THEN 2
                            WHEN 'done' THEN 3
                            ELSE 4
                          END,
                          updated_at DESC
                        LIMIT 100""",
                    params,
                )
            rows = await r.fetchall()
    except Exception as e:
        log.warning("list runs: %s", e)
        return {"items": [], "error": "model_lab tables missing — run migration 045"}
    return {"items": [_row(x) for x in rows]}


@router.get("/api/model-lab/runs/{run_id}")
async def get_run(request: Request, run_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT * FROM model_lab_runs WHERE id = %s AND {where}",
                (run_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    item = _row(row)
    for key in ("response_a", "response_b"):
        raw = item.get(key) or ""
        if not raw:
            item[f"{key}_thinking"] = ""
            continue
        answer, thinking = _split_model_output(raw)
        item[f"{key}_raw"] = raw
        item[f"{key}_thinking"] = thinking
        # Keep response_* as answer for the main pane; thinking alongside
        if answer or thinking:
            item[key] = answer
    return {"ok": True, "item": item}


@router.post("/api/model-lab/runs")
async def create_run(request: Request):
    """
    Body: {
      title?, kind: single|ab, model_a, model_b?, system_prompt?, user_prompt,
      temperature?, run_now?: bool
    }
    """
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    kind = (body.get("kind") or "single").strip().lower()
    if kind not in ("single", "ab"):
        return JSONResponse({"ok": False, "error": "kind must be single|ab"}, status_code=400)

    ok_a, model_a = _valid_tag(body.get("model_a") or "")
    if not ok_a:
        return JSONResponse({"ok": False, "error": f"model_a: {model_a}"}, status_code=400)

    model_b = ""
    if kind == "ab":
        ok_b, model_b = _valid_tag(body.get("model_b") or "")
        if not ok_b:
            return JSONResponse({"ok": False, "error": f"model_b: {model_b}"}, status_code=400)
        if model_b == model_a:
            return JSONResponse(
                {"ok": False, "error": "model_a and model_b must differ for A/B"},
                status_code=400,
            )

    user_prompt = (body.get("user_prompt") or body.get("prompt") or "").strip()
    if not user_prompt:
        return JSONResponse({"ok": False, "error": "user_prompt is required"}, status_code=400)
    user_prompt = user_prompt[:_MAX_PROMPT_CHARS]
    system_prompt = (body.get("system_prompt") or "").strip()[:_MAX_SYSTEM_CHARS]
    title = (body.get("title") or "").strip()[:_MAX_TITLE]
    if not title:
        title = f"{kind}: {model_a}" + (f" vs {model_b}" if kind == "ab" else "")
    temperature = _clamp_temp(body.get("temperature"), 0.3)
    run_now = bool(body.get("run_now", True))
    presence_id = await _get_presence_id(request)

    try:
        async with get_db() as conn:
            r = await conn.execute(
                """INSERT INTO model_lab_runs
                   (presence_id, title, kind, model_a, model_b, system_prompt,
                    user_prompt, temperature, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING *""",
                (
                    presence_id,
                    title,
                    kind,
                    model_a,
                    model_b,
                    system_prompt,
                    user_prompt,
                    temperature,
                ),
            )
            row = await r.fetchone()
    except Exception as e:
        log.exception("create run")
        return JSONResponse(
            {"ok": False, "error": f"DB error (migration 045 applied?): {e}"},
            status_code=500,
        )

    item = _row(row)
    if run_now:
        await _schedule_run(item["id"])
        item["status"] = "running"
    return {"ok": True, "item": item, "run_now": run_now}


@router.post("/api/model-lab/runs/{run_id}/run")
async def run_run(request: Request, run_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"SELECT id, status FROM model_lab_runs WHERE id = %s AND {where}",
                (run_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if row.get("status") == "running":
        return {"ok": True, "status": "running"}
    await _schedule_run(run_id)
    return {"ok": True, "status": "running"}


@router.post("/api/model-lab/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: int):
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"""UPDATE model_lab_runs SET status='cancelled', updated_at=NOW()
                    WHERE id = %s AND {where}
                      AND status IN ('queued', 'running', 'failed')
                    RETURNING *""",
                (run_id,) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found or not cancellable"}, status_code=404)
    return {"ok": True, "item": _row(row)}


@router.patch("/api/model-lab/runs/{run_id}")
async def patch_run(request: Request, run_id: int):
    """Operator judgment notes only in v1 (no promote hook yet)."""
    from src.memory.database import get_db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    notes = (body.get("notes") or "").strip()[:4000]
    presence_id = await _get_presence_id(request)
    where, params = _session_owned_sql(presence_id)
    try:
        async with get_db() as conn:
            r = await conn.execute(
                f"""UPDATE model_lab_runs SET notes = %s, updated_at = NOW()
                    WHERE id = %s AND {where} RETURNING *""",
                (notes, run_id) + params,
            )
            row = await r.fetchone()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True, "item": _row(row)}


async def _run_tester(run_id: int):
    from src.memory.database import get_db

    try:
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT * FROM model_lab_runs WHERE id = %s",
                (run_id,),
            )
            run = await r.fetchone()
            if not run:
                return
            await conn.execute(
                """UPDATE model_lab_runs
                   SET status='running', started_at=NOW(), updated_at=NOW(), error=''
                   WHERE id=%s""",
                (run_id,),
            )
    except Exception as e:
        log.exception("tester start %s: %s", run_id, e)
        return

    response_a = ""
    response_b = ""
    latency_a = None
    latency_b = None
    err = ""

    try:
        response_a, latency_a = await _invoke_ollama_tag(
            model_tag=run["model_a"],
            system_prompt=run.get("system_prompt") or "",
            user_prompt=run.get("user_prompt") or "",
            temperature=float(run.get("temperature") or 0.3),
            label=f"model-lab/run-A#{run_id}",
        )
        if run.get("kind") == "ab" and (run.get("model_b") or "").strip():
            response_b, latency_b = await _invoke_ollama_tag(
                model_tag=run["model_b"],
                system_prompt=run.get("system_prompt") or "",
                user_prompt=run.get("user_prompt") or "",
                temperature=float(run.get("temperature") or 0.3),
                label=f"model-lab/run-B#{run_id}",
            )
        status = "done"
    except Exception as e:
        log.exception("tester run %s failed", run_id)
        err = str(e)[:500]
        status = "failed"

    try:
        async with get_db() as conn:
            # Don't clobber a cancel that landed mid-flight.
            await conn.execute(
                """UPDATE model_lab_runs SET
                     status = CASE WHEN status = 'cancelled' THEN status ELSE %s END,
                     response_a = %s,
                     response_b = %s,
                     latency_a_ms = %s,
                     latency_b_ms = %s,
                     error = %s,
                     finished_at = NOW(),
                     updated_at = NOW()
                   WHERE id = %s AND status IN ('running', 'cancelled')""",
                (
                    status,
                    response_a,
                    response_b,
                    latency_a,
                    latency_b,
                    err,
                    run_id,
                ),
            )
        log.info("model-lab run #%s %s", run_id, status)
    except Exception:
        log.exception("tester finalize %s", run_id)


async def _schedule_run(run_id: int):
    async def _runner():
        await _run_tester(run_id)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        asyncio.ensure_future(_runner())
