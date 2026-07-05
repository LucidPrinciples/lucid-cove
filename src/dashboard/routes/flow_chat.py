"""
Flow Chat — lightweight LLM endpoint for Creation Flows.

No memory, no threads, no LangGraph overhead. Just:
  system_prompt + messages + model_id → response

This is the conversational engine that powers all interactive Creation Flows.
Each flow page sends its own system prompt (personality, framework knowledge,
flow-specific instructions) and conversation history. The endpoint routes
through the existing model registry so any configured model can be used.

Dev mode: pass ?dev=1 in the flow URL to show a model selector dropdown.
The flow JS sends the selected model_id with each request.

Conversation logging: Every call is logged to data/flow-conversations/ as JSONL.
Full message pairs are saved for fine-tuning dataset collection.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import get_primary_agent_id, load_models_registry
from src.models.provider import get_model_client, _write_jw_metric, _resolve_model_string
from src.env import env

router = APIRouter()

# ── Conversation logging for fine-tuning data ───────────────────────────────

CONV_LOG_DIR = Path("/app/data/flow-conversations")


async def _log_conversation(
    *,
    model_id: str,
    model_string: str,
    provider: str,
    system_prompt: str,
    messages: list,
    response_text: str,
    duration_ms: int,
    succeeded: bool,
    error: str | None = None,
    flow_id: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
):
    """Append a full conversation exchange to the daily JSONL log.

    Each line is a complete training-ready record: system prompt,
    full message history, model response, metadata. One file per day
    so they stay manageable and easy to grep/filter.
    """
    try:
        CONV_LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = CONV_LOG_DIR / f"flow-conv-{today}.jsonl"

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_id": model_id,
            "model_string": model_string,
            "provider": provider,
            "flow_id": flow_id,
            "system_prompt": system_prompt,
            "messages": messages,
            "response": response_text,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "succeeded": succeeded,
            "error": error,
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        # Never let logging break the actual flow
        print(f"[flow_chat] Warning: conversation log failed: {e}")


@router.post("/api/flow/chat")
async def flow_chat(request: Request):
    """Stateless LLM chat for Creation Flows.

    Body:
        system_prompt: str — The flow's personality/instructions
        messages: list[{role: "user"|"assistant", content: str}] — Conversation history
        model_id: str (optional) — Model registry ID. Defaults to kimi-k2.5.
        temperature: float (optional) — Defaults to 0.7.

    Returns:
        { response: str, model: str, duration_ms: int }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    system_prompt = body.get("system_prompt", "").strip()
    messages_raw = body.get("messages", [])
    model_id = body.get("model_id", "kimi-k2.5")
    temperature = body.get("temperature", 0.7)
    flow_id = body.get("flow_id")  # e.g. "new-cove-setup/step1", "new-cove-setup/names"

    if not system_prompt:
        return JSONResponse({"error": "system_prompt is required"}, status_code=400)
    if not messages_raw:
        return JSONResponse({"error": "messages is required (at least one)"}, status_code=400)

    # Build LangChain message list
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    lc_messages = [SystemMessage(content=system_prompt)]
    for msg in messages_raw:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))

    # Resolve model and invoke
    agent_id = get_primary_agent_id()
    provider, model_string = _resolve_model_string(model_id)

    # #121 — use this operator's own model creds for the guided chat too (BYOK).
    _byok_tok = None
    try:
        from src.dashboard.routes.presence import get_current_presence
        from src.models.provider import set_request_byok
        _fp = await get_current_presence(request)
        _fac = (_fp or {}).get("agent_config") or {}
        if isinstance(_fac, str):
            import json as _fj
            try:
                _fac = _fj.loads(_fac) or {}
            except Exception:
                _fac = {}
        if isinstance(_fac, dict):
            _prov = (_fac.get("model_provider") or "").strip()
            _key = (_fac.get("model_api_key") or "").strip()
            # Guided cove-creation tour ONLY: if the operator hasn't added their own model
            # yet, run the guided conversation on LP's shared guided key (Kimi via
            # OpenRouter). This key is scoped to THIS flow — the operator's normal agent
            # (chat.py) has no such fallback and still requires their own key or Ollama, so
            # LP never pays for anyone's day-to-day usage, only the setup tour.
            if not _key:
                _lp_guided = (env("LP_GUIDED_OPENROUTER_KEY") or "").strip()
                if _lp_guided:
                    _prov, _key = "openrouter", _lp_guided
            _byok_tok = set_request_byok(_prov, _key)
    except Exception:
        _byok_tok = None

    # Tier 3 — the stranger spark. No operator key and no local guided key, but the hub
    # is reachable: run the guided turn on the hub with LP's key (the key never leaves the
    # hub). Returns early; the founder/BYOK tiers below are untouched.
    _has_local_key = False
    try:
        _has_local_key = bool(_key)
    except Exception:
        _has_local_key = False
    if not _has_local_key and not (env("LP_GUIDED_OPENROUTER_KEY") or "").strip():
        from src.dashboard.routes import registry_client as _rc
        if _rc.configured():
            from src.models.spark import guided_complete as _gc
            try:
                _txt = await _gc(request, system_prompt, messages_raw,
                                 temperature=temperature, model_id=model_id, flow_id=flow_id)
            except Exception as _e:
                return JSONResponse({"error": f"spark: {type(_e).__name__}: {_e}"}, status_code=502)
            await _log_conversation(
                model_id=model_id, model_string=model_id, provider="hub-spark",
                system_prompt=system_prompt, messages=messages_raw, response_text=_txt,
                duration_ms=0, succeeded=True, flow_id=flow_id)
            return {"response": _txt, "model": model_id, "model_id": model_id,
                    "provider": "hub-spark", "duration_ms": 0}

    t0 = time.monotonic()
    try:
        try:
            client = get_model_client(model_id, temperature=temperature)
        finally:
            from src.models.provider import clear_request_byok
            clear_request_byok(_byok_tok)
        response = await asyncio.wait_for(client.ainvoke(lc_messages), timeout=120)
        duration_ms = int((time.monotonic() - t0) * 1000)

        content = (response.content or "").strip()

        # Strip thinking blocks (qwen3 <think> tags)
        import re
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Strip chat template artifacts
        for pattern in [r"<\|user\|>", r"<\|assistant\|>", r"<\|system\|>",
                        r"<\|end\|>", r"<\|endoftext\|>"]:
            content = re.sub(pattern, "", content).strip()

        if not content:
            return JSONResponse({"error": "Model returned empty response"}, status_code=502)

        # Track JW metric
        usage = getattr(response, "usage_metadata", {}) or {}
        meta = getattr(response, "response_metadata", {}) or {}
        tokens_in = usage.get("input_tokens") or meta.get("prompt_eval_count")
        tokens_out = usage.get("output_tokens") or meta.get("eval_count")
        await _write_jw_metric(
            agent_id=agent_id,
            operation_type="flow",
            # Carry the flow id so flow_profiles accrue per-flow (#183) — the
            # estimator keys on this. Falls back to a generic label.
            operation_label=(flow_id or f"flow-chat/{model_id}"),
            model_used=model_string,
            provider=provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            succeeded=True,
        )

        # Log full conversation for fine-tuning dataset
        await _log_conversation(
            model_id=model_id,
            model_string=model_string,
            provider=provider,
            system_prompt=system_prompt,
            messages=messages_raw,
            response_text=content,
            duration_ms=duration_ms,
            succeeded=True,
            flow_id=flow_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        return {
            "response": content,
            "model": model_string,
            "model_id": model_id,
            "provider": provider,
            "duration_ms": duration_ms,
        }

    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _write_jw_metric(
            agent_id=agent_id, operation_type="flow",
            operation_label=f"flow-chat/{model_id}",
            model_used=model_string, provider=provider,
            tokens_in=None, tokens_out=None,
            duration_ms=duration_ms, succeeded=False,
        )
        await _log_conversation(
            model_id=model_id, model_string=model_string, provider=provider,
            system_prompt=system_prompt, messages=messages_raw, response_text="",
            duration_ms=duration_ms, succeeded=False,
            error=f"Timeout after {duration_ms}ms", flow_id=flow_id,
        )
        return JSONResponse({"error": f"Model timed out after {duration_ms}ms"}, status_code=504)

    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _write_jw_metric(
            agent_id=agent_id, operation_type="flow",
            operation_label=f"flow-chat/{model_id}",
            model_used=model_string, provider=provider,
            tokens_in=None, tokens_out=None,
            duration_ms=duration_ms, succeeded=False,
        )
        await _log_conversation(
            model_id=model_id, model_string=model_string, provider=provider,
            system_prompt=system_prompt, messages=messages_raw, response_text="",
            duration_ms=duration_ms, succeeded=False,
            error=f"{type(e).__name__}: {e}", flow_id=flow_id,
        )
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@router.get("/api/flow/conversations")
async def flow_conversations(date: str | None = None, model_id: str | None = None):
    """Return logged conversations for review / fine-tuning export.

    Query params:
        date: YYYY-MM-DD (defaults to today)
        model_id: filter to a specific model
    """
    from datetime import date as date_type

    target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = CONV_LOG_DIR / f"flow-conv-{target_date}.jsonl"

    if not log_path.exists():
        return {"date": target_date, "conversations": [], "count": 0}

    conversations = []
    for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if model_id and record.get("model_id") != model_id:
                continue
            conversations.append(record)
        except json.JSONDecodeError:
            continue

    return {
        "date": target_date,
        "conversations": conversations,
        "count": len(conversations),
    }


@router.get("/api/flow/conversations/dates")
async def flow_conversation_dates():
    """List available conversation log dates."""
    if not CONV_LOG_DIR.exists():
        return {"dates": []}

    dates = sorted([
        f.stem.replace("flow-conv-", "")
        for f in CONV_LOG_DIR.glob("flow-conv-*.jsonl")
    ], reverse=True)
    return {"dates": dates}


@router.get("/api/flow/models")
async def flow_models():
    """Return available models for the flow model selector.

    Only returns models that are actually usable (have required env vars).
    """
    import os
    registry = load_models_registry()

    available = []
    for model in registry:
        provider = model.get("provider", "")
        # Check if the provider's API key is configured
        can_use = True
        if provider == "openrouter" and not env("OPENROUTER_API_KEY"):
            can_use = False
        elif provider == "moonshot" and not env("MOONSHOT_API_KEY"):
            can_use = False
        elif provider == "google" and not env("GOOGLE_API_KEY"):
            can_use = False
        elif provider == "groq" and not env("GROQ_API_KEY"):
            can_use = False
        # Ollama is always available (local)

        available.append({
            "id": model["id"],
            "name": model.get("name", model["id"]),
            "provider": provider,
            "type": model.get("type", "unknown"),
            "available": can_use,
            "notes": model.get("notes", ""),
        })

    return {"models": available}
