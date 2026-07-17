"""
machine_probe.py — detect what a box can ACTUALLY run, so onboarding offers real options
instead of a hardcoded model id.

Answers, at runtime, the two questions the Add-Intelligence step needs:
  1. Is there a GPU, and how much VRAM?  (which local models are viable)
  2. What local model servers are running, and what models do they have pulled?

GPU note: the app runs in a container that usually can't see the host GPU (Ollama runs on
the host, reached over host.docker.internal), so a live nvidia-smi here often returns
nothing. The provisioner runs on the HOST where nvidia-smi works and records the GPU into
cove.yaml (compute.gpu); we read that as the authoritative source and fall back to a
best-effort live probe. Local model servers ARE reachable at runtime over
host.docker.internal, so those are probed live.

We probe a small, editable list of OpenAI-compatible local runtimes. Nothing is assumed
installed; an empty result is a valid answer ("connect a cloud key or pull a model"). No
model is ever loaded into VRAM by probing — we only hit list endpoints.
"""

import logging
import re
import shutil
import subprocess

import httpx

from src.env import env

log = logging.getLogger(__name__)

# Local model servers we know how to probe. Each is just an HTTP endpoint; the default host
# is host.docker.internal (the app container reaching a server on the host). Override any URL
# via env. kind "ollama" uses the native /api/tags (gives real on-disk size); kind "openai"
# uses the OpenAI-standard /v1/models (no size — we estimate from the model name). Add a
# provider by appending one row here (e.g. llama.cpp's llama-server, GPT4All).
LOCAL_PROVIDERS = [
    {"id": "ollama",   "name": "Ollama",    "kind": "ollama",
     "url_env": "OLLAMA_BASE_URL",   "default_url": "http://host.docker.internal:11434"},
    {"id": "lmstudio", "name": "LM Studio", "kind": "openai",
     "url_env": "LMSTUDIO_BASE_URL", "default_url": "http://host.docker.internal:1234"},
    {"id": "jan",      "name": "Jan",       "kind": "openai",
     "url_env": "JAN_BASE_URL",      "default_url": "http://host.docker.internal:1337"},
]


def _provider_url(p: dict) -> str:
    return (env(p["url_env"]) or p["default_url"]).rstrip("/")


def _is_embedding_model(name: str) -> bool:
    """True for embedding / reranker models (nomic-embed-text, mxbai-embed, bge, gte, e5,
    all-minilm, ...). They're installed alongside chat models but can't drive an agent, so
    they must never be RECOMMENDED or offered as a brain — a real bug the probe caught
    (it picked the tiny nomic-embed-text as 'smallest')."""
    n = (name or "").lower()
    if "embed" in n or "minilm" in n or "reranker" in n or "rerank" in n:
        return True
    # bge / gte / e5 embedding families — match as a name segment, not a stray substring.
    return bool(re.search(r"(?:^|[/:_-])(bge|gte|e5)(?:[-_:./]|\d|$)", n))


def _estimate_params_b(model_name: str):
    """Rough parameter count (in billions) parsed from a model name, e.g. 'qwen3:30b-a3b'→30,
    'llama-3.1-8b-instruct'→8. Used to estimate VRAM footprint when a server doesn't report a
    real size. Returns None if no size token is present."""
    if not model_name:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", model_name.lower())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _footprint_mb(size_bytes, model_name):
    """Best estimate of a model's VRAM footprint in MB. Prefer a real on-disk size (Ollama
    reports bytes); else estimate from the param count (~0.7 GB per billion params — a Q4-ish
    weight plus runtime overhead). None when we can't tell."""
    if size_bytes:
        return int(size_bytes / (1024 * 1024))
    pb = _estimate_params_b(model_name)
    if pb:
        return int(pb * 700)
    return None


async def _probe_ollama(base: str) -> list:
    async with httpx.AsyncClient(timeout=4) as c:
        r = await c.get(base + "/api/tags")
    if r.status_code != 200:
        return []
    out = []
    for m in (r.json().get("models") or []):
        name = m.get("name") or m.get("model") or ""
        if name:
            out.append({"name": name, "size_bytes": m.get("size"),
                        "chat": not _is_embedding_model(name)})
    return out


async def _probe_openai(base: str) -> list:
    async with httpx.AsyncClient(timeout=4) as c:
        r = await c.get(base + "/v1/models")
    if r.status_code != 200:
        return []
    out = []
    for m in (r.json().get("data") or []):
        mid = m.get("id") or ""
        if mid:
            out.append({"name": mid, "size_bytes": None,
                        "chat": not _is_embedding_model(mid)})
    return out


async def probe_local_providers() -> list:
    """Probe each known local server. One entry per provider: reachable + its models.
    Best-effort — an unreachable server (the common case: not installed) is reported with
    reachable=False, never an error that blocks the rest."""
    results = []
    for p in LOCAL_PROVIDERS:
        base = _provider_url(p)
        entry = {"id": p["id"], "name": p["name"], "url": base,
                 "reachable": False, "models": []}
        try:
            models = await (_probe_ollama(base) if p["kind"] == "ollama" else _probe_openai(base))
            entry["reachable"] = True
            entry["models"] = models
        except Exception as e:
            msg = str(e)
            entry["error"] = msg[:120]
            # C6: stock Linux Ollama binds 127.0.0.1 only, so the container's
            # host-gateway route gets connection-refused even though "Ollama is
            # installed" per the docs. Name the actual fix, not just the error.
            if p["kind"] == "ollama" and ("refused" in msg.lower()
                                          or "all connection attempts failed" in msg.lower()):
                entry["hint"] = ("Ollama is running but only listening on 127.0.0.1, so the "
                                 "Cove container can't reach it. Set OLLAMA_HOST=0.0.0.0 and "
                                 "restart Ollama. macOS: launchctl setenv OLLAMA_HOST 0.0.0.0, "
                                 "then quit and reopen the Ollama app. Linux: systemctl edit "
                                 "ollama -> Environment=\"OLLAMA_HOST=0.0.0.0\", then "
                                 "systemctl restart ollama.")
        results.append(entry)
    return results


def detect_gpu() -> dict:
    """Best-effort LIVE GPU probe via nvidia-smi. Usually empty inside the app container (the
    host GPU isn't passed through), so the endpoint prefers the provisioner-recorded value
    from cove.yaml. Returns {present, name, vram_mb}."""
    if not shutil.which("nvidia-smi"):
        return {"present": False}
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0 or not r.stdout.strip():
            return {"present": False}
        parts = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        name = parts[0] if parts else ""
        vram_mb = int(float(parts[1])) if len(parts) > 1 and parts[1] else None
        return {"present": True, "name": name, "vram_mb": vram_mb}
    except Exception:
        return {"present": False}


def gpu_from_config() -> dict:
    """The GPU the provisioner detected on the HOST at provision time (nvidia-smi works there),
    stored in cove.yaml compute.gpu. Authoritative for sizing when present."""
    try:
        from src.config import load_cove_config
        g = ((load_cove_config().get("compute") or {}).get("gpu") or {})
        if g.get("present"):
            return {"present": True, "name": g.get("name", ""), "vram_mb": g.get("vram_mb")}
    except Exception:
        pass
    return {"present": False}


def recommend_local(gpu: dict, providers: list) -> dict:
    """Pick a sensible local default from what's ACTUALLY installed — never a hardcoded id.
    With known VRAM: the largest model that fits (best quality that still runs). CPU-only or
    unknown VRAM: the smallest model (most likely to be usable). Nothing installed → no
    recommendation, with guidance. Returns {provider, model, reason}."""
    candidates = []
    for p in providers:
        if not p.get("reachable"):
            continue
        for m in p.get("models", []):
            if m.get("chat") is False:   # embedding / reranker — never a brain
                continue
            candidates.append({
                "provider": p["id"], "model": m["name"],
                "footprint_mb": _footprint_mb(m.get("size_bytes"), m["name"]),
            })
    if not candidates:
        return {"provider": None, "model": None,
                "reason": "No local chat model found. Pull one (e.g. `ollama pull qwen3:8b`) "
                          "or connect a cloud key."}
    vram = gpu.get("vram_mb") if gpu.get("present") else None
    if vram:
        usable = vram * 0.9
        fits = [c for c in candidates if c["footprint_mb"] and c["footprint_mb"] <= usable]
        if fits:
            best = max(fits, key=lambda c: c["footprint_mb"])
            return {"provider": best["provider"], "model": best["model"],
                    "reason": f"Largest model that fits your ~{round(vram / 1024)}GB GPU."}
        sized = [c for c in candidates if c["footprint_mb"]]
        if sized:
            best = min(sized, key=lambda c: c["footprint_mb"])
            return {"provider": best["provider"], "model": best["model"],
                    "reason": "Smallest installed model — your GPU may be tight for larger ones."}
    sized = [c for c in candidates if c["footprint_mb"]]
    if sized:
        best = min(sized, key=lambda c: c["footprint_mb"])
        return {"provider": best["provider"], "model": best["model"],
                "reason": "Smallest installed model — safest pick without a detected GPU."}
    best = candidates[0]
    return {"provider": best["provider"], "model": best["model"],
            "reason": "First installed model found."}


def cloud_keys_present() -> list:
    """Which cloud providers already have a key in the env (so onboarding can show them as
    ready-to-use without re-entering a key)."""
    from src.models.provider import _PROVIDER_ENV_VAR
    return [prov for prov, var in _PROVIDER_ENV_VAR.items() if (env(var) or "").strip()]


async def machine_probe() -> dict:
    """The full machine report for the Add-Intelligence step: GPU, local servers + their
    installed models, cloud keys already present, and a recommendation drawn from what's
    actually on the box."""
    gpu = gpu_from_config()
    if not gpu.get("present"):
        gpu = detect_gpu()
    providers = await probe_local_providers()
    rec = recommend_local(gpu, providers)
    return {"gpu": gpu, "providers": providers,
            "cloud_keys": cloud_keys_present(), "recommendation": rec}
