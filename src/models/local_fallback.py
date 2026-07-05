"""Local-model fallback resolution (batch8 #11 / CF-106).

The problem this kills: several code paths fell back to a HARDCODED local id
(`qwen3:30b-a3b`) when no cloud brain was reachable. On a box that never pulled
that exact tag, the fallback resolved to a 404 model and the agent answered with
a silent "trouble responding" (or, on Clearfield's ezra ltp-dispatch, a bare
no-fallback error). A hardcoded id we can't see on the box is never a safe
fallback.

Resolution order at failure time:
  1. the configured model (handled by the caller before it reaches here)
  2. the BEST model ACTUALLY INSTALLED on the box — reuse `recommend_local`'s
     pick against live Ollama tags
  3. fail LOUD (`LocalModelUnavailable`) — never a 404 model, never a silent
     "trouble responding". The message tells the operator exactly what to do.

Sync on purpose: the fallback points in provider.py (`get_primary_model`,
`get_local_model`) are synchronous, so this probes Ollama's /api/tags with a
short-timeout SYNC client rather than dragging an event loop into the hot path.
Result is cached for the process (installed models don't change mid-request);
`reset_local_fallback_cache()` clears it for tests / after a pull.
"""
from __future__ import annotations

import time

from src.env import env
from src.models.machine_probe import (
    _is_embedding_model, gpu_from_config, recommend_local,
)


class LocalModelUnavailable(RuntimeError):
    """Raised when no local chat model is installed to fall back to. LOUD on
    purpose — the caller should surface this, never swap in a hardcoded id."""


_CACHE: dict = {"model": None, "ts": 0.0}
_CACHE_TTL = 60.0  # seconds — a pulled model shows up within a minute


def reset_local_fallback_cache() -> None:
    _CACHE["model"] = None
    _CACHE["ts"] = 0.0


def _ollama_base() -> str:
    return (env("OLLAMA_BASE_URL") or "http://host.docker.internal:11434").rstrip("/")


def _probe_installed_sync() -> list:
    """Sync probe of Ollama's installed tags. Returns a providers-shaped list
    (matching `probe_local_providers`) so `recommend_local` can consume it.
    Best-effort — an unreachable server yields reachable=False, never raises."""
    base = _ollama_base()
    entry = {"id": "ollama", "name": "Ollama", "url": base,
             "reachable": False, "models": []}
    import httpx  # deferred: only needed at probe time, keeps import light for tests
    try:
        with httpx.Client(timeout=4) as c:
            r = c.get(base + "/api/tags")
        if r.status_code == 200:
            models = []
            for m in (r.json().get("models") or []):
                name = m.get("name") or m.get("model") or ""
                if name:
                    models.append({"name": name, "size_bytes": m.get("size"),
                                   "chat": not _is_embedding_model(name)})
            entry["reachable"] = True
            entry["models"] = models
    except Exception:
        pass
    return [entry]


def resolve_local_fallback_model(force: bool = False) -> str:
    """Best INSTALLED local model name, or raise LocalModelUnavailable.

    Never returns a hardcoded id. Caches the pick for _CACHE_TTL seconds.
    """
    now = time.time()
    if not force and _CACHE["model"] and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["model"]
    providers = _probe_installed_sync()
    rec = recommend_local(gpu_from_config(), providers)
    model = rec.get("model")
    if not model:
        raise LocalModelUnavailable(
            "No local model installed to fall back to. Add one in Settings "
            "(e.g. `ollama pull qwen3:8b`) or connect a cloud key."
        )
    _CACHE["model"] = model
    _CACHE["ts"] = now
    return model
