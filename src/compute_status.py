# =============================================================================
# compute_status.py — CF-96: ONE compute state, read by every surface.
# =============================================================================
# Compute establishment (llm / voice / video_asr / gpu) is written in ONE place
# (config.set_compute_config) and every heavy-work surface — the onboarding
# chooser, Settings, the Rent GPU card, the Pipeline Services panel, and the
# video pipeline's gating — must reflect the SAME state. Before this, each
# surface re-derived its own idea of the world from raw yaml/env, so an operator
# could rent a GPU and still see "nothing set up" on Rent GPU and a degraded
# Pipeline Services panel.
#
# compute_status() is the single reader. It folds key-presence (AT-1 pipeline
# keys) and endpoint/token presence into a `ready` verdict + a human `why`, so
# no surface has to guess. The token is NEVER included in the output (the read
# API redacts it — same contract as get_compute_config's callers).
#
# Pure logic, framework-free (lazy config import only) so the tests can import
# it without FastAPI. Repo rule: new feature area → new file.
# =============================================================================
import logging

log = logging.getLogger(__name__)


def _host_of(url: str) -> str:
    """Human host label for an external endpoint URL (scheme + path stripped)."""
    u = (url or "").strip()
    for pre in ("https://", "http://", "wss://", "ws://"):
        if u.startswith(pre):
            u = u[len(pre):]
            break
    return u.split("/", 1)[0].strip()


def _video_asr_status(cfg: dict) -> dict:
    """Can this Cove transcribe video, and by which path? Folds in AT-1 keys.

    Returns {mode, ready, backend, host, path, label, why}:
      backend  gpu | cloud | none      (what actually does the work)
      path     gpu-rented | gpu-local | cloud | off   (surface-facing label key)
      host     the external endpoint host when renting a GPU, else ""
    """
    va = cfg.get("video_asr") or {}
    mode = (va.get("mode") or "cloud").strip()
    url = (va.get("url") or "").strip()
    token = (va.get("token") or "").strip()

    if mode == "external":
        host = _host_of(url)
        if url and token:
            return {"mode": mode, "ready": True, "backend": "gpu", "host": host,
                    "path": "gpu-rented",
                    "label": f"rented GPU ({host})" if host else "rented GPU",
                    "why": f"endpoint {host} + grant token set" if host
                           else "endpoint + grant token set"}
        missing = "endpoint" if not url else "grant token"
        return {"mode": mode, "ready": False, "backend": "none", "host": host,
                "path": "off",
                "label": "rented GPU — not connected",
                "why": f"external mode but no {missing}"}

    if mode == "local":
        return {"mode": mode, "ready": True, "backend": "gpu", "host": "",
                "path": "gpu-local", "label": "local GPU",
                "why": "local GPU transcription"}

    # cloud (default): needs a BYOK ASR key (env OR saved in-app, AT-1).
    has_key = False
    provider = ""
    try:
        from src.dashboard.routes.pipeline_keys import first_asr_provider_key
        provider, _k = first_asr_provider_key()
        has_key = bool(provider)
    except Exception:
        try:
            from src.dashboard.routes.pipeline_keys import any_asr_key
            has_key = any_asr_key()
        except Exception:
            has_key = False
    if has_key:
        lbl = f"cloud ({provider.title()} key)" if provider else "cloud"
        return {"mode": mode, "ready": True, "backend": "cloud", "host": "",
                "path": "cloud", "label": lbl,
                "why": f"cloud ASR key present ({provider})" if provider
                       else "cloud ASR key present"}
    return {"mode": mode, "ready": False, "backend": "none", "host": "",
            "path": "off", "label": "off — add a key or rent a GPU",
            "why": "no local GPU, no cloud ASR key, no rented endpoint"}


def _llm_status(cfg: dict) -> dict:
    """Where the brain runs. Pre-flip the only external consumer is video_asr;
    llm readiness is reported at the mode level (cloud/local always 'ready';
    external needs a url)."""
    llm = cfg.get("llm") or {}
    mode = (llm.get("mode") or "cloud").strip()
    url = (llm.get("url") or "").strip()
    if mode == "external":
        return {"mode": mode, "ready": bool(url), "url_host": _host_of(url),
                "why": f"external endpoint {_host_of(url)}" if url
                       else "external mode but no endpoint"}
    return {"mode": mode, "ready": True, "why": f"{mode} brain"}


def _voice_status(cfg: dict) -> dict:
    """jules ASR/TTS backend. Uses resolve_voice_urls (the single voice resolver)
    for the enabled verdict so this never drifts from what jules actually does."""
    voice = cfg.get("voice") or {}
    mode = (voice.get("mode") or "local").strip()
    enabled = mode != "off"
    try:
        from src.config import resolve_voice_urls
        enabled = bool(resolve_voice_urls().get("enabled"))
    except Exception:
        pass
    return {"mode": mode, "ready": enabled,
            "why": ("off" if mode == "off" else
                    ("enabled" if enabled else "no reachable voice endpoint"))}


def _gpu_record() -> dict:
    """Provisioner/host-detected GPU record (compute.gpu in cove.yaml). A record,
    not a mode — present/name/vram_mb. Empty when no GPU was detected at install."""
    try:
        from src.config import load_cove_config
        gpu = (load_cove_config().get("compute") or {}).get("gpu") or {}
        if not isinstance(gpu, dict):
            return {"present": False}
        present = bool(gpu.get("present") or gpu.get("name") or gpu.get("vram_mb"))
        return {"present": present, "name": (gpu.get("name") or ""),
                "vram_mb": gpu.get("vram_mb") or 0}
    except Exception:
        return {"present": False}


def compute_status() -> dict:
    """The single source every compute surface reads. Never returns any token.

    Shape:
      {
        "video_asr": {mode, ready, backend, host, path, label, why},
        "llm":       {mode, ready, why, [url_host]},
        "voice":     {mode, ready, why},
        "gpu":       {present, name, vram_mb},
      }
    Callers that only need "can we transcribe" read video_asr.ready / .backend.
    """
    try:
        from src.config import get_compute_config
        cfg = get_compute_config() or {}
    except Exception:
        cfg = {}
    return {
        "video_asr": _video_asr_status(cfg),
        "llm": _llm_status(cfg),
        "voice": _voice_status(cfg),
        "gpu": _gpu_record(),
    }
