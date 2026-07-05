"""
Model price map + cost estimator (#183 build #1).

A semi-live, close-enough cost estimate for in-the-moment decisions — NOT a
billing ledger. Every LLM call already logs tokens/model/provider in
jw_metrics; this module turns that into a dollar figure.

Three price kinds, all USD:
  - LLM      -> per million tokens (in / out), keyed by (provider, model_string)
  - ASR      -> per minute, keyed by service name
  - compute  -> per minute (GPU time), keyed by backend name

Local own-GPU work is $0 by design (ollama, local ASR, local-gpu).

Sourcing is "semi-live":
  - Static baseline ships in config/model-prices.json (hand-maintained).
  - The 'openrouter' LLM section can be refreshed from the OpenRouter models
    API via refresh_openrouter_prices(). The refresh updates an in-memory map
    and best-effort persists a cache file. estimate_*_cost() never touches the
    network — it reads the in-memory map, so cost logging stays in the hot path
    without a fetch.
"""

import json
import os
from src.env import env
import time
from pathlib import Path

import httpx

# config/model-prices.json — a hand-maintained repo STATIC asset. /app/config is the
# per-install INSTANCE config dir and is NOT populated with repo static assets, so on a
# deployed Cove the instance path is empty and the loader would silently disable cost
# estimates on every install (CF-66 / #8). Resolve across the instance config dir then the
# cove-core repo mount, first existing wins — the same instance->cove-core fallback that
# src/config.py uses (CONFIG_DIR -> CORE_CONFIG_DIR) and src/knowledge/kb_paths.py uses for KB.
_INSTANCE_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"   # = /app/config in container
_CORE_CONFIG_DIR = Path("/cove-core/config")                            # repo-bundled via /cove-core:ro


def _static_path_candidates() -> list[Path]:
    """model-prices.json locations, highest priority first (de-duplicated)."""
    cands = [
        _INSTANCE_CONFIG_DIR / "model-prices.json",   # direct repo run, or instance copy if present
        _CORE_CONFIG_DIR / "model-prices.json",       # deployed container fallback (/cove-core:ro)
    ]
    seen, out = set(), []
    for c in cands:
        s = str(c)
        if s not in seen:
            seen.add(s)
            out.append(c)
    return out


def _resolve_static_path() -> Path:
    """First existing candidate; else the top candidate (for a clear log message)."""
    cands = _static_path_candidates()
    for c in cands:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return cands[0]

# Optional writable cache for refreshed OpenRouter prices. Config mounts are
# often read-only, so the live refresh persists here instead.
_DATA_DIR = env("DATA_DIR", "/app/data")
_CACHE_PATH = Path(_DATA_DIR) / "model-prices.cache.json"

# OpenRouter live price source. Pricing is returned in USD *per token* as
# strings under data[].pricing.{prompt,completion}.
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Refresh cadence guard — don't refetch more than this often.
_REFRESH_TTL_S = 6 * 3600

_price_map: dict | None = None
_last_refresh_ts: float = 0.0


# =============================================================================
# Loading
# =============================================================================

def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into a copy of base (overlay wins)."""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_map() -> dict:
    """Load the static price map, overlaying any persisted live cache."""
    static_path = _resolve_static_path()
    try:
        base = json.loads(static_path.read_text())
    except Exception as e:
        print(f"[pricing] could not load static price map from {static_path} ({e}); "
              f"cost estimates disabled")
        base = {"llm": {}, "asr": {}, "compute": {}}
    if _CACHE_PATH.exists():
        try:
            cache = json.loads(_CACHE_PATH.read_text())
            base = _deep_merge(base, cache)
        except Exception as e:
            print(f"[pricing] ignoring bad price cache ({e})")
    return base


def get_price_map() -> dict:
    """Return the in-memory price map, loading it on first use."""
    global _price_map
    if _price_map is None:
        _price_map = _load_map()
    return _price_map


def reload_prices() -> dict:
    """Force a reload from disk (static + cache). Returns the fresh map."""
    global _price_map
    _price_map = _load_map()
    return _price_map


# =============================================================================
# Estimators — pure, no network, safe to call in the logging hot path
# =============================================================================

def llm_rates(provider: str, model_string: str) -> tuple[float, float] | None:
    """Return (in_per_million, out_per_million) for a model, or None if unknown.

    Falls back to a provider-level '*' wildcard entry (used for ollama, where
    every local model is $0).
    """
    table = get_price_map().get("llm", {}).get((provider or "").lower(), {})
    entry = table.get(model_string) or table.get("*")
    if not entry:
        return None
    return float(entry.get("in", 0.0)), float(entry.get("out", 0.0))


def estimate_llm_cost(provider: str, model_string: str,
                      tokens_in: int | None, tokens_out: int | None) -> float | None:
    """Estimate USD cost of one LLM call. None when the model is unpriced.

    A None return means "we don't know" (leave cost_usd NULL) rather than
    guessing $0 — except local ollama, which is a real $0 via the '*' entry.
    """
    rates = llm_rates(provider, model_string)
    if rates is None:
        return None
    rate_in, rate_out = rates
    cost = ((tokens_in or 0) / 1_000_000.0) * rate_in \
        + ((tokens_out or 0) / 1_000_000.0) * rate_out
    return round(cost, 6)


def estimate_asr_cost(minutes: float, service: str = "default") -> float:
    """Estimate USD cost of transcribing `minutes` of audio."""
    table = get_price_map().get("asr", {})
    entry = table.get(service) or table.get("default") or {"per_minute": 0.0}
    return round(max(0.0, minutes) * float(entry.get("per_minute", 0.0)), 6)


def estimate_compute_cost(minutes: float, backend: str = "local-gpu") -> float:
    """Estimate USD cost of `minutes` of GPU/compute time."""
    table = get_price_map().get("compute", {})
    entry = table.get(backend) or {"per_minute": 0.0}
    return round(max(0.0, minutes) * float(entry.get("per_minute", 0.0)), 6)


# =============================================================================
# Semi-live refresh — OpenRouter LLM prices
# =============================================================================

def _persist_openrouter(openrouter_prices: dict) -> None:
    """Best-effort write the refreshed OpenRouter section to the cache file."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_meta": {"openrouter_refreshed_at": int(time.time())},
            "llm": {"openrouter": openrouter_prices},
        }
        _CACHE_PATH.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        print(f"[pricing] could not persist price cache (non-fatal): {e}")


async def refresh_openrouter_prices(force: bool = False) -> int:
    """Refresh the 'openrouter' LLM section from the OpenRouter models API.

    Semi-live: updates the in-memory map and persists a cache file. Throttled
    by _REFRESH_TTL_S unless force=True. Returns the count of models priced.
    Never raises — on failure the static baseline stays in effect.
    """
    global _last_refresh_ts
    now = time.time()
    if not force and (now - _last_refresh_ts) < _REFRESH_TTL_S:
        return 0
    try:
        headers = {}
        key = env("OPENROUTER_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(_OPENROUTER_MODELS_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
    except Exception as e:
        print(f"[pricing] OpenRouter refresh failed (keeping static prices): {e}")
        return 0

    priced: dict[str, dict] = {}
    for m in data:
        mid = m.get("id")
        pricing = m.get("pricing") or {}
        try:
            # API gives USD per token; store USD per million tokens.
            rate_in = float(pricing.get("prompt", 0.0)) * 1_000_000.0
            rate_out = float(pricing.get("completion", 0.0)) * 1_000_000.0
        except (TypeError, ValueError):
            continue
        if mid and (rate_in or rate_out):
            priced[mid] = {"in": round(rate_in, 4), "out": round(rate_out, 4)}

    if not priced:
        return 0

    # Merge into the live map (keep static entries the API didn't return).
    pm = get_price_map()
    pm.setdefault("llm", {}).setdefault("openrouter", {}).update(priced)
    pm.setdefault("_meta", {})["openrouter_refreshed_at"] = int(now)
    _persist_openrouter(pm["llm"]["openrouter"])
    _last_refresh_ts = now
    print(f"[pricing] OpenRouter prices refreshed: {len(priced)} models")
    return len(priced)
