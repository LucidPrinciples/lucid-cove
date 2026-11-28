"""Cove Model / Fallback / Local floor — the only hop planner.

A Cove has three settings. Nothing else picks a model: not the per-agent grid,
not turn scoring, not Tuner V2, not Presence stored picks (until the operator
names them). Duplicate hops are skipped by model_string, not by id-vs-tag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["chat", "background"]

CHAT_OPERATION_TYPES = frozenset({"channel", "chat"})
CHAT_FAST_FAIL_S = 30.0

# Every invoke_with_fallback / plan_hops caller: operation_type → kind.
# Anything not in CHAT_OPERATION_TYPES is background (Model, Fallback, floor).
OPERATION_KIND = {
    "channel": "chat",
    "chat": "chat",
    "protocol": "background",
    "tuning": "background",
    "workflow": "background",
    "task": "background",
    "tool": "background",
    "memory": "background",
    "archive_digestion": "background",
    "background": "background",
}


def hop_kind(operation_type: str | None) -> Kind:
    ot = (operation_type or "").lower()
    if ot in CHAT_OPERATION_TYPES:
        return "chat"
    return "background"


def should_retry_chat_primary(elapsed_s: float, err: BaseException) -> bool:
    """Retry Model once only after a fast fail. A full-length timeout gets a plain line."""
    name = type(err).__name__.lower()
    msg = str(err).lower()
    if "timeout" in name or "timeout" in msg:
        return False
    return elapsed_s < CHAT_FAST_FAIL_S


def chat_plain_line(err: BaseException | None = None, *, no_model: bool = False) -> str:
    if no_model:
        return "The model did not answer (no valid Model is set)."
    err_type = type(err).__name__ if err is not None else ""
    err_msg = str(err) if err is not None else ""
    low_type = err_type.lower()
    low_msg = err_msg.lower()
    if "timeout" in low_type or "timeout" in low_msg:
        why = "timed out"
    elif "refus" in low_msg or "401" in err_msg or "403" in err_msg:
        why = "refused"
    else:
        why = "could not connect"
    return f"The model did not answer ({why})."


@dataclass(frozen=True)
class CoveSettings:
    model: str | None
    fallback: str | None
    floor: str | None
    detail: str = ""


def _registry() -> list[dict]:
    try:
        from src.config import load_models_registry
        return [m for m in (load_models_registry() or []) if m.get("id")]
    except Exception:
        return []


def registry_entry(token: str | None) -> dict | None:
    """Match a registry row by id or by model_string. None if neither matches."""
    if not token:
        return None
    t = str(token).strip()
    if not t:
        return None
    for m in _registry():
        if m.get("id") == t or (m.get("model_string") or "").strip() == t:
            return m
    return None


def registry_id(token: str | None) -> str | None:
    row = registry_entry(token)
    return (row.get("id") if row else None)


def model_string_of(token: str | None) -> str | None:
    row = registry_entry(token)
    if not row:
        return None
    return (row.get("model_string") or row.get("id") or "").strip() or None


def _load_override_keys() -> tuple[str | None, str | None, str | None]:
    from src.config import get_model_override, get_model_override_fallback, get_cove_local_floor
    return get_model_override(), get_model_override_fallback(), get_cove_local_floor()


def _brain_as_registry_id() -> str | None:
    try:
        from src.models.provider import current_cove_brain
        brain = current_cove_brain() or {}
    except Exception:
        brain = {}
    token = (brain.get("model") or "").strip()
    rid = registry_id(token)
    if rid:
        return rid
    if token:
        print(f"[cove_hops] WARNING: Cove brain model '{token}' matches no registry id — skipped")
    return None


def _smallest_installed_floor() -> str | None:
    """Smallest registry-listed installed local chat model (never largest)."""
    try:
        from src.models.local_fallback import resolve_smallest_local_model, LocalModelUnavailable
        try:
            tag = resolve_smallest_local_model()
        except LocalModelUnavailable:
            return None
        rid = registry_id(tag)
        return rid or None
    except Exception:
        return None


def load_cove_settings() -> CoveSettings:
    """Resolve the three settings. Unmatched strings are dropped, not inferred."""
    model_tok, fb_tok, floor_tok = _load_override_keys()
    model = registry_id(model_tok) or _brain_as_registry_id()
    fallback = registry_id(fb_tok)
    if fallback and model and model_string_of(fallback) == model_string_of(model):
        fallback = None
    floor = registry_id(floor_tok)
    if not floor:
        floor = _smallest_installed_floor()
    detail = f"model={model} fallback={fallback} floor={floor}"
    return CoveSettings(model=model, fallback=fallback, floor=floor, detail=detail)


def cove_floor_tag() -> str | None:
    """Ollama tag for Local floor: named setting, else smallest installed. Never largest."""
    s = load_cove_settings()
    if not s.floor:
        try:
            from src.models.local_fallback import resolve_smallest_local_model, LocalModelUnavailable
            try:
                return resolve_smallest_local_model()
            except LocalModelUnavailable:
                return None
        except Exception:
            return None
    row = registry_entry(s.floor)
    if row:
        return (row.get("model_string") or row.get("id") or "").strip() or None
    return s.floor


def _append_unique(chain: list[str], token: str | None, seen: set[str]) -> None:
    rid = registry_id(token)
    if not rid:
        if token:
            print(f"[cove_hops] WARNING: '{token}' matches no registry id — hop skipped")
        return
    ms = model_string_of(rid) or rid
    if ms in seen:
        return
    seen.add(ms)
    chain.append(rid)


def plan_cove_chain(kind: Kind = "background", settings: CoveSettings | None = None) -> list[str]:
    """Chat: Model only. Background: Model, Fallback, Local floor.

    Tuner V2 is never injected. It is used only if named as Fallback or floor.
    """
    s = settings or load_cove_settings()
    chain: list[str] = []
    seen: set[str] = set()
    _append_unique(chain, s.model, seen)
    if kind == "chat":
        return chain
    _append_unique(chain, s.fallback, seen)
    _append_unique(chain, s.floor, seen)
    return chain


async def invoke_chat_or_plain(client, messages, timeout: float, clock=None):
    """Chat node helper: one Model attempt, one fast retry, never a second client.

    Full-length timeout → plain line, no second wait. Callers must not create a
    local-floor client on this path.
    """
    import asyncio
    import time as _time
    clock = clock or _time.monotonic
    t0 = clock()
    try:
        return await asyncio.wait_for(client.ainvoke(messages), timeout=timeout)
    except Exception as err:
        elapsed = clock() - t0
        if not should_retry_chat_primary(elapsed, err):
            return chat_plain_line(err)
        try:
            return await asyncio.wait_for(client.ainvoke(messages), timeout=timeout)
        except Exception as err2:
            return chat_plain_line(err2)


def print_cove_settings() -> None:
    """Dry-print resolved Model / Fallback / Local floor and both chains."""
    s = load_cove_settings()
    print(f"Model: {s.model or '(none)'}")
    print(f"Fallback: {s.fallback or '(none)'}")
    print(f"Local floor: {s.floor or '(none)'}")
    print(f"chat: {plan_cove_chain('chat', s)}")
    print(f"background: {plan_cove_chain('background', s)}")


if __name__ == "__main__":
    print_cove_settings()
