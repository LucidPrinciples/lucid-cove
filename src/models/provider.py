"""
Model provider — per-agent model routing with 2-tier fallback chain.

Each agent has a primary and fallback model assignment (in agent.yaml).
The models registry (config/models.yaml) defines available models with
their provider, model string, and context window.

Supported providers:
  - openrouter: Cloud models via OpenRouter API (Kimi K2.5, etc.)
  - google: Google Gemini models via google-generativeai SDK
  - groq: Groq inference API
  - ollama: Local models on P620 RTX 3090
  - xai-oauth: xAI Grok via device-code OAuth2 (Responses-API)

Agent → model mapping is in agent.yaml (model_primary, model_fallback).
Model definitions are in config/models.yaml.
"""

import asyncio
import json
import os
from src.env import env
import time
from pathlib import Path
from functools import lru_cache

import httpx

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from src.models.xai_oauth import _xai_oauth_client

from src.config import (
    get_instance, get_primary_agent_id,
    get_agent_model_assignment, get_model_from_registry,
    load_models_registry,
)


# =============================================================================
# OpenRouter reasoning capture — raw HTTP hook
# =============================================================================
# LangChain's async path discards non-standard response fields before we can
# access them. This httpx event hook captures reasoning_content from the raw
# OpenRouter JSON before the openai SDK's Pydantic models strip it.

_last_reasoning: dict[str, str | None] = {"value": None}


async def _capture_reasoning_hook(response: httpx.Response):
    """Event hook: extract reasoning_content from raw OpenRouter JSON."""
    try:
        await response.aread()
        raw = json.loads(response.content)

        reasoning = None

        # Chat Completions format — choices[].message.reasoning_content
        for choice in raw.get("choices", []):
            msg = choice.get("message", {})
            r = msg.get("reasoning_content") or msg.get("reasoning")
            if r and isinstance(r, str):
                reasoning = r
                break

        # Responses API fallback — output items with type=reasoning
        if not reasoning:
            for item in raw.get("output", []):
                if isinstance(item, dict) and item.get("type") == "reasoning":
                    summary = item.get("summary", [])
                    if summary:
                        parts = [s.get("text", "") for s in summary if isinstance(s, dict)]
                        reasoning = "\n".join(parts).strip()
                    if not reasoning:
                        reasoning = item.get("content") or item.get("text")
                    if reasoning:
                        break

        _last_reasoning["value"] = reasoning
    except Exception:
        _last_reasoning["value"] = None


def get_last_reasoning() -> str | None:
    """Retrieve and clear reasoning captured from the last OpenRouter call."""
    val = _last_reasoning["value"]
    _last_reasoning["value"] = None
    return val


# =============================================================================
# OpenRouter reasoning capture — ChatOpenAI subclass
# =============================================================================

class ChatOpenRouterWithReasoning(ChatOpenAI):
    """ChatOpenAI subclass for OpenRouter.

    Reasoning capture is handled by the httpx event hook above
    (_capture_reasoning_hook), not by overriding _create_chat_result.
    This subclass exists as a marker type and to accept http_async_client.
    """
    pass


def _get_admin_display_name() -> str:
    """Build agent display name from agent.yaml config."""
    instance = get_instance()
    return instance.get("name", "Agent")


# =============================================================================
# Context window limits (tokens) — used for monitoring + thread lifecycle
# =============================================================================

MODEL_CONTEXT_LIMITS = {
    "moonshotai/kimi-k2.5": 128_000,
    "kimi-k2.5": 128_000,
    "gemini-2.5-flash-preview-05-20": 1_000_000,
    "llama-3.3-70b-versatile": 128_000,
    "deepseek/deepseek-v3.2": 64_000,
    "deepseek-v3.2": 64_000,
    "qwen3:30b-a3b": 32_768,
    "qwen3:8b": 32_768,
    "qwen3:32b": 32_768,
}

# Ollama num_ctx — explicit so we don't get silent truncation at 2048
OLLAMA_NUM_CTX = 32_768

# ── Fallback chain configuration (#D24) ─────────────────────────────────────
# Cloud middle-hop: different upstream provider, no GPU cold-load, fast on
# big prompts.  Used when primary (also OpenRouter) times out — gives us
# a second cloud path before falling to the local heavyweight.
CLOUD_FALLBACK_MODEL = "deepseek-v3.2"   # via openrouter, NOT the same upstream
# Local fallback gets extra time — 20GB model cold-loading on 3090 + eval
# of a 19k-token prompt can't finish inside 120s.  180s is still bounded.
LOCAL_FALLBACK_TIMEOUT = 180

# Warning thresholds (percentage of context used)
CONTEXT_WARN_THRESHOLD = 0.70   # yellow
CONTEXT_CRITICAL_THRESHOLD = 0.85  # red — should trigger thread rotation


def estimate_tokens(text: str) -> int:
    """Estimate token count from text. ~4 chars per token is a reasonable
    approximation for English text across most models."""
    return len(text) // 4


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens for a list of LangChain messages.
    Accounts for message overhead (~4 tokens per message for role/formatting)."""
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += estimate_tokens(content) + 4
        elif isinstance(content, list):
            # Multi-part messages
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text", ""))) + 4
                else:
                    total += estimate_tokens(str(part)) + 4
    return total


def get_context_limit(model_name: str = None) -> int:
    """Get the context window limit for a model. Defaults to Kimi limit."""
    if model_name and model_name in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model_name]
    # Default to primary model limit
    return MODEL_CONTEXT_LIMITS.get(_OPENROUTER_PRIMARY_MODEL, 128_000)


# =============================================================================
# Model resolution — registry-based lookup
# =============================================================================

_DEFAULT_LOCAL_MODEL = "qwen3:30b-a3b"  # last-resort seed only; real fallbacks resolve from installed tags (#11)
_OPENROUTER_PRIMARY_MODEL = "moonshotai/kimi-k2.5"  # default if registry lookup fails


def _resolve_local_fallback() -> str:
    """The local model to fall back to when no cloud brain is reachable: the best
    model ACTUALLY INSTALLED on the box (#11/CF-106), never a hardcoded id. If the
    resolver can't find one it raises LocalModelUnavailable LOUD — but callers on
    the legacy hot path keep working against the seed id if the probe itself fails
    for an unexpected reason (network hiccup, not 'nothing installed')."""
    from src.models.local_fallback import resolve_local_fallback_model, LocalModelUnavailable
    try:
        return resolve_local_fallback_model()
    except LocalModelUnavailable:
        raise
    except Exception as _e:
        print(f"[provider] local-fallback probe failed ({_e}); using seed id")
        return _DEFAULT_LOCAL_MODEL


_WARNED_UNKNOWN_MODELS: set = set()

# #D38: bare (slash-free) model ids that name a CLOUD model family. A slash-free
# unknown id is normally treated as a local Ollama tag — but a cloud family name
# (e.g. 'kimi-k2.5', a typo for the registry's 'kimi-k2.5-openrouter') is NOT a
# local tag, and asking Ollama for it is a guaranteed 404 on EVERY call. These
# fragments let us recognise the misroute and keep it on a cloud path instead.
_CLOUD_MODEL_FRAGMENTS = (
    "kimi", "gemini", "gpt", "claude", "sonnet", "opus", "haiku",
    "deepseek", "glm", "grok", "mistral", "command", "o1", "o3", "o4",
    "llama-4", "llama4", "maverick", "qwen-max", "qwen-plus",
)

# Provider-suffix labels we append to registry ids (kimi-k2.5-openrouter); stripped
# when matching a bare id back to its registry entry.
_PROVIDER_SUFFIXES = ("-openrouter", "-google", "-groq", "-openai", "-direct", "-anthropic")


def _looks_like_cloud_id(model_id: str) -> bool:
    """A slash-free id that names a known cloud model family (so it is a
    misconfiguration, never a local Ollama tag). Pure."""
    mid = (model_id or "").lower()
    return any(frag in mid for frag in _CLOUD_MODEL_FRAGMENTS)


def _recover_cloud_model(model_id: str, registry: list) -> tuple[str, str] | None:
    """Map a bare cloud id (a registry-id typo) back to its real registry entry
    by matching its base against each CLOUD entry's id / de-suffixed id /
    model_string / model_string's last path segment. Returns (provider,
    model_string) or None. Pure — takes the registry list, loads nothing."""
    want = (model_id or "").strip().lower()
    if not want:
        return None
    for m in registry or []:
        if (m.get("type") or "").lower() == "local" or m.get("provider") == "ollama":
            continue
        mid = (m.get("id") or "").lower()
        mstr = (m.get("model_string") or "").lower()
        base = mid
        for suf in _PROVIDER_SUFFIXES:
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        candidates = {mid, base, mstr, mstr.rsplit("/", 1)[-1]}
        if want in candidates:
            return m.get("provider", "openrouter"), m.get("model_string", model_id)
    return None


def _resolve_model_string(model_id: str) -> tuple[str, str]:
    """Resolve a model registry ID to (provider, model_string).

    Returns (provider, model_string) from the registry, or falls back to
    treating the ID as a raw model string with provider inference. The
    inference is kept for self-host flexibility (any local Ollama tag works
    without registry ceremony) but it warns ONCE per unknown id — a typo'd
    registry id (e.g. 'kimi-k2.5' instead of 'kimi-k2.5-openrouter') otherwise
    becomes a silent daily ollama 404 with every call landing on the fallback.

    #D38 guard: a slash-free id that names a cloud family is NEVER routed to the
    Ollama provider (that's a guaranteed 404 on every call). We first try to
    recover it to its real registry entry; failing that we keep it on OpenRouter
    (the cloud path) rather than Ollama. Either way the misroute warns ONCE.
    """
    model_def = get_model_from_registry(model_id)
    if model_def:
        return model_def["provider"], model_def.get("model_string", model_id)

    # Explicit provider path ('deepseek/deepseek-v3.2') → openrouter, unchanged.
    if "/" in model_id:
        provider = "openrouter"
    elif _looks_like_cloud_id(model_id):
        # #D38: a cloud family name that isn't a registry id. Recover it to the
        # real entry if we can; otherwise keep it on the cloud path, never Ollama.
        recovered = _recover_cloud_model(model_id, load_models_registry())
        if recovered:
            if model_id not in _WARNED_UNKNOWN_MODELS:
                _WARNED_UNKNOWN_MODELS.add(model_id)
                print(f"[provider] recovered misrouted cloud id '{model_id}' → "
                      f"{recovered[0]}/{recovered[1]} (registry-id typo). Fix the "
                      f"assignment to '{recovered[1]}' or its registry id to silence this.")
            return recovered
        provider = "openrouter"
        if model_id not in _WARNED_UNKNOWN_MODELS:
            _WARNED_UNKNOWN_MODELS.add(model_id)
            print(f"[provider] WARNING: cloud model id '{model_id}' is not in the "
                  f"registry and has no exact match — routing to OpenRouter, NOT Ollama "
                  f"(a cloud id on Ollama 404s every call). Add it to config/models.yaml.")
        return provider, model_id
    else:
        provider = "ollama"

    if model_id not in _WARNED_UNKNOWN_MODELS:
        _WARNED_UNKNOWN_MODELS.add(model_id)
        print(f"[provider] WARNING: model id '{model_id}' is not in the registry "
              f"(config/models.yaml) — inferring provider '{provider}'. If this is a "
              f"registry id typo, fix the assignment; every call on it will fail over "
              f"to the fallback model.")
    return provider, model_id


def _get_context_window(model_id: str) -> int:
    """Get context window for a model from registry."""
    model_def = get_model_from_registry(model_id)
    if model_def:
        return model_def.get("context_window", 32768)
    return MODEL_CONTEXT_LIMITS.get(model_id, 32768)


# =============================================================================
# Provider client factories
# =============================================================================

def _openrouter_client(model: str, temperature: float, key: str = None) -> ChatOpenRouterWithReasoning:
    """Internal factory for an OpenRouter-backed ChatOpenAI instance.

    Uses ChatOpenRouterWithReasoning to capture reasoning_content from
    models that support extended thinking (Kimi k2.5, etc.). `key` (the operator's
    BYOK key) wins over the env when present.
    """
    api_key = key or env("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not configured")
    _async_client = httpx.AsyncClient(
        event_hooks={"response": [_capture_reasoning_hook]},
        timeout=httpx.Timeout(120.0),
    )
    # OpenRouter reasoning is OPT-IN via extra_body — and it is model-specific on purpose:
    #   • Kimi k2.5: MUST stay OFF. Kimi's extended-reasoning mode breaks OpenAI-compatible
    #     tool calling — it emits native <|tool_calls_section_begin|> tokens instead of
    #     structured tool_calls (VPS/Socrates works precisely because it never set this).
    #   • GLM-5.x: MUST be ON. GLM does reasoning AND structured tool calling together, but
    #     OpenRouter returns no reasoning_content unless asked — without this the thinking
    #     block stays empty. So gate by the model string.
    # Anything else defaults OFF (safe). Capture still happens via the httpx hook above.
    _extra_kwargs = {}
    if "glm" in (model or "").lower():
        _extra_kwargs["extra_body"] = {"reasoning": {"effort": "high"}}
    return ChatOpenRouterWithReasoning(
        model=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=temperature,
        timeout=120,
        max_retries=0,
        default_headers={
            "HTTP-Referer": "https://lucidcove.local",
            "X-Title": f"{_get_admin_display_name()} Personal Assistant",
        },
        http_async_client=_async_client,
        **_extra_kwargs,
    )


def _ollama_base_url() -> str:
    """Where Ollama lives. The Admin `compute.llm` setting wins when set to an external
    box (the 'borrow a GPU' / P620 offramp); otherwise the OLLAMA_BASE_URL env / default."""
    try:
        from src.config import get_compute_config
        llm = get_compute_config().get("llm", {})
        if llm.get("mode") == "external" and llm.get("url"):
            return llm["url"]
    except Exception:
        pass
    return env("OLLAMA_BASE_URL", "http://host.docker.internal:11434")


def _ollama_client(model_string: str, temperature: float) -> ChatOllama:
    """Create an Ollama client for a local model."""
    return ChatOllama(
        model=model_string,
        base_url=_ollama_base_url(),
        temperature=temperature,
        num_ctx=OLLAMA_NUM_CTX,
        timeout=120,
    )


def _openai_client(model_string: str, temperature: float, key: str = None):
    """Create an OpenAI client (direct). `key` = the operator's BYOK key, else env."""
    api_key = key or env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    return ChatOpenAI(
        model=model_string,
        api_key=api_key,
        temperature=temperature,
        timeout=120,
        max_retries=0,
    )


def _google_client(model_string: str, temperature: float, key: str = None):
    """Create a Google Gemini client via ChatOpenAI-compatible endpoint.

    Uses the Gemini OpenAI-compatible API so we can stay in LangChain.
    Requires GOOGLE_API_KEY env var (or the operator's BYOK key).
    """
    api_key = key or env("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not configured")
    return ChatOpenAI(
        model=model_string,
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        temperature=temperature,
        timeout=120,
        max_retries=0,
    )


def _groq_client(model_string: str, temperature: float, key: str = None):
    """Create a Groq client via OpenAI-compatible endpoint.

    Requires GROQ_API_KEY env var (or the operator's BYOK key).
    """
    api_key = key or env("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not configured")
    return ChatOpenAI(
        model=model_string,
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
        temperature=temperature,
        timeout=60,
        max_retries=0,
    )


def _moonshot_client(model_string: str, temperature: float, key: str = None):
    """Create a Moonshot/Kimi client via OpenAI-compatible endpoint.

    Direct API — bypasses OpenRouter for lower latency and no timeout issues.
    Requires MOONSHOT_API_KEY env var.

    NOTE: Kimi K2.5 only accepts temperature=1. The Moonshot API returns 400
    for any other value. We force temperature=1 here regardless of what the
    caller requests.
    """
    api_key = key or env("MOONSHOT_API_KEY")
    if not api_key:
        raise RuntimeError("MOONSHOT_API_KEY not configured")
    return ChatOpenAI(
        model=model_string,
        api_key=api_key,
        base_url="https://api.moonshot.ai/v1",
        temperature=1.0,  # Kimi K2.5 requires exactly 1
        timeout=120,
        max_retries=0,
    )


def _featherless_client(model_string: str, temperature: float, key: str = None):
    """Featherless — OpenAI-compatible, FLAT-RATE subscription inference (no per-token
    bills). The sanctioned way to run big open models like GLM 5.2 on a fixed monthly
    fee. Requires FEATHERLESS_API_KEY. The $25 tier allows 4 concurrent requests, so
    scope this to a few presences (steward/merchant/personal) and keep a fallback."""
    api_key = key or env("FEATHERLESS_API_KEY")
    if not api_key:
        raise RuntimeError("FEATHERLESS_API_KEY not configured")
    return ChatOpenAI(
        model=model_string,
        api_key=api_key,
        base_url="https://api.featherless.ai/v1",
        temperature=temperature,
        timeout=120,
        max_retries=0,
    )


# ── BYOK (#121): request-scoped operator model creds ────────────────────────
# The chat handler sets the operator's chosen provider + key for the duration of a
# request; the deep model factory reads it here. Server-side only (a contextvar is
# never serialized to the browser), so the raw key never leaves the process. Unset
# → env keys, i.e. exactly today's behavior.
import contextvars as _ctx
_byok_ctx = _ctx.ContextVar("byok_model", default=None)

# Default model per provider for the case where the operator's chosen provider differs
# from the Cove's configured model — so their key actually drives the agent.
BYOK_DEFAULT_MODEL = {
    "openrouter": "openrouter/auto",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "moonshot": "kimi-k2.5",  # Kimi K2.5 (128k) — the real brain; NOT the weak 8k base
    "featherless": "zai-org/GLM-5.2",
    "ollama": _DEFAULT_LOCAL_MODEL,
}

# Map a provider to the env var its client reads for the API key.
_PROVIDER_ENV_VAR = {
    "openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY", "moonshot": "MOONSHOT_API_KEY",
    "featherless": "FEATHERLESS_API_KEY",
}

# The Cove's BRAIN — the provider+model the admin connected via "Add Intelligence". Set
# at connect-time and re-loaded at boot from the admin's account. Drives get_primary_model
# for EVERY agent and scheduled job (not just the connecting operator's own requests).
_cove_primary = None  # (provider, model_string) or None


def apply_cove_model(provider: str, api_key: str = "", model: str = "") -> bool:
    """Make the connected provider+key the Cove's default brain. Stashes the key in the live
    process env (so every client reads it) and records the (provider, model) so
    get_primary_model uses it. Ollama needs no key. Returns True if a brain was set.

    An explicit `model` (e.g. the specific LOCAL model the operator picked from the machine
    probe) wins over the per-provider default — so the brain runs the model that's actually
    installed, never a hardcoded local id we can't see on the box. Omitted → the per-provider
    default (BYOK_DEFAULT_MODEL), i.e. exactly the prior behavior."""
    global _cove_primary
    p = (provider or "").strip().lower()
    if not p or p not in BYOK_DEFAULT_MODEL:
        return False
    var = _PROVIDER_ENV_VAR.get(p)
    if var and api_key:
        os.environ[var] = api_key
    model_string = (model or "").strip() or BYOK_DEFAULT_MODEL[p]
    _cove_primary = (p, model_string)
    # PERSIST so the brain survives restarts. It was in-memory ONLY (os.environ + this
    # global) -> a fresh install worked right after onboarding, then reverted to local/none
    # on the next restart/deploy/reboot. Store as a cove-level default that carries the
    # credential, separate from any per-presence BYOK. (Chords 2026-07-17)
    try:
        from src.config import save_feature_overrides as _sfo
        _sfo({"cove_brain": {"provider": p, "model": model_string, "key": (api_key or "").strip()}})
    except Exception as _e:
        print(f"[provider] Cove brain persist failed (in-memory only): {_e}")
    print(f"[provider] Cove brain set → {p} ({model_string})")
    return True


def _restore_cove_brain() -> bool:
    """Reload the persisted cove brain (provider/model/key) from feature-overrides into the
    live process (os.environ key + _cove_primary) so it survives container restarts. Called
    lazily when _cove_primary is unset. Returns True if a brain was restored."""
    global _cove_primary
    if _cove_primary:
        return True
    try:
        from src.config import _load_feature_overrides as _lfo
        cb = (_lfo() or {}).get("cove_brain") or {}
    except Exception:
        cb = {}
    prov = (cb.get("provider") or "").strip().lower()
    model = (cb.get("model") or "").strip()
    key = (cb.get("key") or "").strip()
    if not prov or prov not in BYOK_DEFAULT_MODEL:
        return False
    var = _PROVIDER_ENV_VAR.get(prov)
    if var and key and not (os.environ.get(var) or "").strip():
        os.environ[var] = key
    _cove_primary = (prov, model or BYOK_DEFAULT_MODEL[prov])
    print(f"[provider] Cove brain restored from disk → {prov} ({_cove_primary[1]})")
    return True


def current_cove_brain() -> dict:
    """The Cove's effective primary 'brain' — the {provider, model} that get_primary_model
    resolves to when an agent has no explicit assignment. The admin's Add-Intelligence
    choice (_cove_primary) if set, else the env-OpenRouter → Ollama floor get_primary_model
    actually falls to. Lets the Team-page model manager SHOW what '(Cove default)' means
    instead of a blank. Per-request BYOK overrides aren't reflected here — they're
    request-scoped, not the standing default."""
    if not _cove_primary:
        _restore_cove_brain()
    if _cove_primary:
        return {"provider": _cove_primary[0], "model": _cove_primary[1]}
    if (env("OPENROUTER_API_KEY") or "").strip():
        return {"provider": "openrouter", "model": _OPENROUTER_PRIMARY_MODEL}
    return {"provider": "ollama", "model": _DEFAULT_LOCAL_MODEL}


def model_is_runnable(model_id: str) -> bool:
    """True if a registry model can actually run right now: Ollama (no key needed), or its
    provider's API key is present in the env — which is also where apply_cove_model stashes
    the connected brain's key. Lets the Team-page grid clear the onboarding 'add
    intelligence' nag only on a pick that will really work, not a cloud model with no key
    behind it (preserving the no-false-connected hardening)."""
    provider, _ = _resolve_model_string(model_id)
    p = (provider or "").strip().lower()
    if p == "ollama":
        return True
    var = _PROVIDER_ENV_VAR.get(p)
    return bool(var and (env(var) or "").strip())


def set_request_byok(provider: str, api_key: str = "", model: str = ""):
    """Set the operator's BYOK provider + key (+ optional model) for THIS request.

    Returns a reset token for clear_request_byok. Ollama needs no key; an explicit
    `model` (the tag picked from machine-probe, e.g. dolphin-phi) is REQUIRED for
    local to be correct — without it, get_primary_model used to substitute the
    hardcoded qwen3:30b-a3b seed and 404 on stranger boxes that never pulled it.
    Returns None (no-op) when there's nothing usable to set."""
    p = (provider or "").strip().lower()
    m = (model or "").strip()
    if p == "ollama":
        return _byok_ctx.set({"provider": "ollama", "api_key": "", "model": m})
    if p and api_key:
        return _byok_ctx.set({"provider": p, "api_key": api_key, "model": m})
    return None


def clear_request_byok(token):
    try:
        if token is not None:
            _byok_ctx.reset(token)
    except Exception:
        pass


def _byok_now():
    try:
        return _byok_ctx.get()
    except Exception:
        return None


def _client_for(provider: str, model_string: str, temperature: float, key: str = None):
    if provider == "openrouter":
        return _openrouter_client(model_string, temperature, key=key)
    elif provider == "openai":
        return _openai_client(model_string, temperature, key=key)
    elif provider == "moonshot":
        return _moonshot_client(model_string, temperature, key=key)
    elif provider == "ollama":
        return _ollama_client(model_string, temperature)
    elif provider == "google":
        return _google_client(model_string, temperature, key=key)
    elif provider == "groq":
        return _groq_client(model_string, temperature, key=key)
    elif provider == "featherless":
        return _featherless_client(model_string, temperature, key=key)
    elif provider == "xai-oauth":
        return _xai_oauth_client(model_string, temperature)
    else:
        raise RuntimeError(f"Unknown provider '{provider}'")


def get_model_client(model_id: str, temperature: float = 0.7):
    """Get a LangChain model client for any model in the registry. The universal factory
    — all model access should go through here.

    Honors a request-scoped BYOK override (#121) when set: same provider as the Cove's
    model → inject the operator's key; a different provider → use that provider's default
    model + their key (so their choice drives the agent). Unset → env keys (unchanged).
    """
    provider, model_string = _resolve_model_string(model_id)
    byok = _byok_now()
    if byok:
        bp = byok.get("provider")
        bmodel = (byok.get("model") or "").strip()
        if bp == provider:
            # Same provider: honor explicit BYOK model when set (local tag pick),
            # else keep the assignment's model_string.
            use = bmodel or model_string
            return _client_for(provider, use, temperature, key=byok.get("api_key"))
        if bp in BYOK_DEFAULT_MODEL:
            # Different provider: operator's pick wins. Prefer their explicit model,
            # else per-provider default — but never the ollama seed when no model
            # was named (resolve from installed tags instead).
            if bmodel:
                use = bmodel
            elif bp == "ollama":
                use = _resolve_local_fallback()
            else:
                use = BYOK_DEFAULT_MODEL[bp]
            return _client_for(bp, use, temperature, key=byok.get("api_key"))
    return _client_for(provider, model_string, temperature)


def get_primary_model(temperature: float = 0.7) -> ChatOpenAI | ChatOllama:
    """Get the instance-level primary model. Legacy compatibility.

    New code should use get_model_client() with a specific model ID.

    Resolution order: (1) a per-request operator BYOK override (a member's own key beats
    the Cove default); (2) the Cove's connected BRAIN (apply_cove_model — what "Add
    Intelligence" set, used by every agent/job); (3) env OpenRouter; (4) local Ollama.
    Without (1)+(2) a fresh keyless self-host fell straight to an absent env key, then
    Ollama (often not running), so the operator's connected key was ignored and the agent
    silently never replied.
    """
    # 1. Per-request operator override (Add-Intelligence / Settings BYOK).
    # MUST honor an explicit model tag — BYOK_DEFAULT_MODEL["ollama"] is only a
    # seed id (qwen3:30b-a3b) and 404s on boxes that never pulled it (install-pass
    # Wendy screenshot: operator picked dolphin-phi, chat still hit the seed).
    byok = _byok_now()
    if byok:
        bp = byok.get("provider")
        if bp in BYOK_DEFAULT_MODEL:
            try:
                bmodel = (byok.get("model") or "").strip()
                if bmodel:
                    use = bmodel
                elif bp == "ollama":
                    use = _resolve_local_fallback()
                else:
                    use = BYOK_DEFAULT_MODEL[bp]
                return _client_for(bp, use, temperature, key=byok.get("api_key"))
            except Exception as _e:
                print(f"[provider] BYOK primary failed ({_e}); falling back")
    # 2. The Cove's connected brain — the admin's Add-Intelligence choice (key is in env).
    if not _cove_primary:
        _restore_cove_brain()
    if _cove_primary:
        try:
            return _client_for(_cove_primary[0], _cove_primary[1], temperature)
        except Exception as _e:
            print(f"[provider] Cove brain failed ({_e}); falling back")
    # 3 / 4. Env OpenRouter, then local Ollama (resolved from INSTALLED tags —
    # never a hardcoded id that might 404 on this box; #11/CF-106).
    try:
        return _openrouter_client(_OPENROUTER_PRIMARY_MODEL, temperature)
    except RuntimeError as e:
        print(f"[provider] WARNING: {e} — falling back to local Ollama")
        return _ollama_client(_resolve_local_fallback(), temperature)


def get_local_model(temperature: float = 0.7) -> ChatOllama:
    """Get the best INSTALLED local Ollama model. Legacy compatibility.

    Resolves from live Ollama tags (#11/CF-106) — no hardcoded id. Raises
    LocalModelUnavailable LOUD when nothing is installed rather than returning a
    404 model that reads as a silent 'trouble responding'."""
    return _ollama_client(_resolve_local_fallback(), temperature)


# =============================================================================
# JouleWork metric writer
# =============================================================================

async def _write_jw_metric(
    *,
    agent_id: str,
    operation_type: str,
    operation_label: str,
    model_used: str,
    provider: str,
    tokens_in: int | None,
    tokens_out: int | None,
    duration_ms: int,
    tool_calls_made: int = 0,
    succeeded: bool = True,
) -> None:
    """Write one JouleWork metric row. Never raises — failures are logged only."""
    try:
        from src.memory.database import get_db
        from src.models.pricing import estimate_llm_cost
        tokens_total = (tokens_in or 0) + (tokens_out or 0)
        duration_s = duration_ms / 1000.0
        jw_score = round(tokens_total * duration_s, 4) if tokens_total else None
        # Close-enough dollar estimate from the semi-live price map (#183).
        # None for unpriced models; real 0.0 for local ollama.
        cost_usd = estimate_llm_cost(provider, model_used, tokens_in, tokens_out)
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO jw_metrics
                   (agent_id, operation_type, operation_label, model_used, provider,
                    tokens_in, tokens_out, tokens_total, duration_ms, tool_calls_made,
                    succeeded, jw_score, cost_usd)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (agent_id, operation_type, operation_label, model_used, provider,
                 tokens_in, tokens_out, tokens_total or None,
                 duration_ms, tool_calls_made, succeeded, jw_score, cost_usd),
            )
        # Fold this call into the flow profile (rolling avg of LLM tokens),
        # so the pre-flight estimator self-updates as runs accrue (#183).
        if succeeded and tokens_total:
            from src.models.flow_profiles import record_llm
            await record_llm(operation_label, tokens_total)
    except Exception as e:
        print(f"[jw_metrics] write failed (non-fatal): {e}")


async def write_asr_metric(
    *,
    agent_id: str,
    operation_label: str,
    minutes: float,
    service: str = "default",
    model_label: str | None = None,
    flow: str = "video-pipeline",
    step: str = "transcribe",
) -> None:
    """Log a transcription (ASR) call's cost + minutes (#183).

    LLM calls flow through _write_jw_metric; the ASR path doesn't (no tokens),
    so this records the minutes-based cost into jw_metrics and folds asr_minutes
    into the flow profile. Never raises.
    """
    if not minutes or minutes <= 0:
        return
    try:
        from src.memory.database import get_db
        from src.models.pricing import estimate_asr_cost
        from src.models.flow_profiles import record_observation
        cost_usd = estimate_asr_cost(minutes, service)
        duration_ms = int(minutes * 60_000)
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO jw_metrics
                   (agent_id, operation_type, operation_label, model_used, provider,
                    tokens_in, tokens_out, tokens_total, duration_ms, tool_calls_made,
                    succeeded, jw_score, cost_usd)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (agent_id, "asr", operation_label, model_label or service, "asr",
                 None, None, None, duration_ms, 0, True, None, cost_usd),
            )
        await record_observation(flow, step, "asr_minutes", minutes)
    except Exception as e:
        print(f"[jw_metrics] asr write failed (non-fatal): {e}")


# =============================================================================
# Fallback-aware invocation — primary API for all LLM calls
# =============================================================================

async def invoke_with_fallback(
    messages: list,
    *,
    temperature: float = 0.7,
    timeout: int = 150,
    label: str = "llm",
    agent_id: str = "stuart",
    operation_type: str = "protocol",
    routing_bias: str = "balanced",
    message_text: str = "",
) -> str:
    """Invoke the agent's model chain with #D55 score-ordered hops + #D16 rescue.

    Resolves assignment (working or tuning slot), plans hops via
    ``src.models.router`` (local-first when easy, API-first when hard), then
    walks the chain on timeout / empty / error. Cloud middle hop and installed
    local floor remain available when runnable.

    Returns response text as a plain string. Writes a JouleWork metric row per attempt.
    """
    # Last user-ish text for the scorer when caller didn't pass message_text.
    if not message_text and messages:
        try:
            for _m in reversed(list(messages)):
                _c = getattr(_m, "content", None)
                if isinstance(_c, str) and _c.strip():
                    # skip pure system blobs
                    _cls = type(_m).__name__.lower()
                    if "system" in _cls:
                        continue
                    message_text = _c
                    break
        except Exception:
            pass

    _slot = "tuning" if operation_type == "tuning" else None
    assignment = get_agent_model_assignment(agent_id, slot=_slot)
    primary_id = assignment.get("primary") or current_cove_brain().get("model")
    fallback_id = assignment.get("fallback")

    # Tuning: prefer ltp-tuner-v2 as local candidate when installed (#D44).
    if operation_type == "tuning" and not fallback_id:
        try:
            from src.models.local_fallback import resolve_tuner_model
            _tuner = resolve_tuner_model()
            if _tuner:
                fallback_id = _tuner
                print(f"[{label}] Tuning work — resolved ltp-tuner-v2 '{_tuner}'")
        except Exception:
            pass

    from src.models.router import plan_hops, format_plan_log, record_hop_failure
    plan = plan_hops(
        agent_id=agent_id,
        primary_id=primary_id,
        fallback_id=fallback_id,
        message_text=message_text or "",
        message_count=len(messages or []),
        operation_type=operation_type,
        routing_bias=routing_bias or "balanced",
        cloud_middle_id=CLOUD_FALLBACK_MODEL,
        allow_cloud_middle=True,
    )
    print(format_plan_log(plan, label=label))

    chain = list(plan.chain or [])
    if not chain:
        # Absolute last ditch — same as pre-router single primary
        if primary_id:
            chain = [primary_id]
        if fallback_id and fallback_id not in chain:
            chain.append(fallback_id)

    last_err: Exception | None = None
    for hop_i, model_id in enumerate(chain):
        provider, model_str = _resolve_model_string(model_id)
        # Local hops get the longer cold-load budget; others use caller timeout
        hop_timeout = LOCAL_FALLBACK_TIMEOUT if provider == "ollama" else timeout
        # First hop: keep fail-fast spirit of #D16 when not local
        if hop_i == 0 and provider != "ollama":
            hop_timeout = min(hop_timeout, max(90, timeout))
        t0 = time.monotonic()
        try:
            client = get_model_client(model_id, temperature=temperature)
            response = await asyncio.wait_for(client.ainvoke(messages), timeout=hop_timeout)
            duration_ms = int((time.monotonic() - t0) * 1000)
            content = (response.content or "").strip()
            if content:
                usage = getattr(response, "usage_metadata", {}) or {}
                meta = getattr(response, "response_metadata", {}) or {}
                await _write_jw_metric(
                    agent_id=agent_id, operation_type=operation_type, operation_label=label,
                    model_used=model_str, provider=provider,
                    tokens_in=usage.get("input_tokens") or meta.get("prompt_eval_count"),
                    tokens_out=usage.get("output_tokens") or meta.get("eval_count"),
                    duration_ms=duration_ms, succeeded=True,
                )
                print(
                    f"[{label}] Completed via {provider}/{model_str} "
                    f"(hop {hop_i + 1}/{len(chain)}, score={plan.score.score}, {len(content)} chars)"
                )
                return content
            print(f"[{label}] {provider}/{model_str} returned empty — next hop")
            await _write_jw_metric(
                agent_id=agent_id, operation_type=operation_type, operation_label=label,
                model_used=model_str, provider=provider,
                tokens_in=None, tokens_out=None, duration_ms=duration_ms, succeeded=False,
            )
            record_hop_failure(model_id, provider)
            last_err = RuntimeError(f"empty content from {provider}/{model_str}")
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            print(
                f"[{label}] {provider}/{model_str} failed ({type(e).__name__}: {e}) — next hop"
            )
            await _write_jw_metric(
                agent_id=agent_id, operation_type=operation_type, operation_label=label,
                model_used=model_str, provider=provider,
                tokens_in=None, tokens_out=None, duration_ms=duration_ms, succeeded=False,
            )
            record_hop_failure(model_id, provider)
            last_err = e

    # If chain never included a local floor, try installed local once more
    try:
        from src.models.local_fallback import resolve_local_fallback_model, LocalModelUnavailable
        try:
            floor_id = resolve_local_fallback_model()
        except LocalModelUnavailable:
            floor_id = None
    except Exception:
        floor_id = None
    if floor_id and floor_id not in chain:
        provider, model_str = _resolve_model_string(floor_id)
        t0 = time.monotonic()
        try:
            client = get_model_client(floor_id, temperature=temperature)
            response = await asyncio.wait_for(
                client.ainvoke(messages), timeout=LOCAL_FALLBACK_TIMEOUT
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            content = (response.content or "").strip()
            await _write_jw_metric(
                agent_id=agent_id, operation_type=operation_type, operation_label=label,
                model_used=model_str, provider=provider,
                tokens_in=None, tokens_out=None, duration_ms=duration_ms,
                succeeded=bool(content),
            )
            if content:
                print(f"[{label}] Completed via local floor {provider}/{model_str}")
                return content
            record_hop_failure(floor_id, provider)
        except Exception as e:
            record_hop_failure(floor_id, provider)
            last_err = e

    if last_err is not None:
        raise RuntimeError(
            f"[{label}] All model hops failed for {agent_id}. "
            f"Last: {type(last_err).__name__}: {last_err}"
        ) from last_err
    raise RuntimeError(f"[{label}] All model hops failed for {agent_id} (no hops runnable)")
