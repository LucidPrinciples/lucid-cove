# =============================================================================
# pipeline_keys.py — Cove-wide service keys for the Video Pipeline + Site
# Builder (AT-1, the last migration piece).
# =============================================================================
# The pipeline calls outside services that need credentials:
#   - cloud ASR / transcription:  Groq / OpenAI / Deepgram  (any ONE enables
#     the cloud transcription backend on a GPU-less Cove)
#   - site deploys:               GitHub PAT (Cloudflare-Pages repos)
# Until now these were env-only (.env, container recreate to rotate). This
# module gives them a Settings surface with the same rules as every other
# credential here:
#   - the READ API never echoes a secret — {set, source} booleans only
#   - saving ignores masked "********" echoes so re-saving a form never wipes
#     a stored key; an EXPLICIT empty save clears (that's the rotate/remove)
#   - Cove-wide keys are ADMIN-only (per-presence posting keys — X/YouTube —
#     stay on their existing per-presence endpoints in posting.py; the GET here
#     includes their status so one surface shows the whole pipeline's auth)
# Storage: feature overrides (/app/data/feature-overrides.yaml — the writable
# data volume, same home as github_pat + the YouTube app creds). Resolution
# order everywhere: override → env, via get_service_key().
# =============================================================================
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.env import env

log = logging.getLogger(__name__)
router = APIRouter()

# service name → (feature-override key, env fallback, label)
SERVICES = {
    "groq":     ("groq_api_key",     "GROQ_API_KEY",     "Groq (cloud transcription)"),
    "openai":   ("openai_api_key",   "OPENAI_API_KEY",   "OpenAI (cloud transcription)"),
    "deepgram": ("deepgram_api_key", "DEEPGRAM_API_KEY", "Deepgram (cloud transcription)"),
    "github":   ("github_pat",       "",                 "GitHub PAT (site deploys)"),
}

_MASK = "********"


def get_service_key(service: str) -> str:
    """Resolve a pipeline service key: saved override wins, env is the fallback.
    Empty string when neither is set. This is the single resolution point —
    consumers (transcription availability, cloud ASR calls, site deploys)
    should use it instead of reading env directly."""
    spec = SERVICES.get(service)
    if not spec:
        return ""
    flag_key, env_key, _ = spec
    try:
        from src.config import get_feature_flags
        v = (get_feature_flags().get(flag_key) or "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception:
        pass
    return (env(env_key) or "").strip() if env_key else ""


# CF-110 #3 — the Cove-level "Analysis model" for the video moments chain. Saved
# beside the pipeline keys (same feature-overrides store). Empty = "Your agent's
# model (default)" — the moments chain runs its normal agent→cloud→local tiers.
# When set, the moments chain consults THIS model FIRST (before the agent chain).
# The sovereignty gate is enforced at consult time, NOT here: llm.mode=local still
# blocks a paid pick regardless of this value; a local pick is always honored.
ANALYSIS_MODEL_FLAG = "analysis_model"


def get_analysis_model() -> str:
    """The operator-selected analysis model id, or "" when unset (use the default
    agent chain). Saved override only — there is no env fallback for this knob."""
    try:
        from src.config import get_feature_flags
        v = (get_feature_flags().get(ANALYSIS_MODEL_FLAG) or "")
        return v.strip() if isinstance(v, str) else ""
    except Exception:
        return ""


def analysis_model_allowed(provider: str, llm_mode: str) -> bool:
    """Sovereignty gate for the #3 analysis-model pick. A LOCAL (ollama) model is
    always honored. A paid (non-ollama) model is allowed only when the Cove's
    compute mode is NOT local — llm.mode=local never silently moves work onto a
    paid API, regardless of the dropdown."""
    is_local = (provider or "").strip().lower() == "ollama"
    if is_local:
        return True
    return (llm_mode or "cloud").strip().lower() != "local"


def any_asr_key() -> bool:
    """Any cloud-ASR key present (override or env) — enables the cloud
    transcription backend on a GPU-less Cove."""
    return any(get_service_key(s) for s in ("groq", "openai", "deepgram"))


def first_asr_provider_key() -> tuple:
    """(provider, key) for the first cloud-ASR service with a key, in SERVICES
    order — the pair the app hands to pipecat with a cloud transcription job.
    Pipecat only reads its OWN env, which never carries operator-saved keys;
    without this handoff a saved key lifts the degraded banner but every cloud
    job still fails key-less on the voice side. ("", "") when none."""
    for s in ("groq", "openai", "deepgram"):
        k = get_service_key(s)
        if k:
            return s, k
    return "", ""


def _service_status(service: str) -> dict:
    spec = SERVICES[service]
    flag_key, env_key, label = spec
    override = ""
    try:
        from src.config import get_feature_flags
        override = (get_feature_flags().get(flag_key) or "")
        override = override.strip() if isinstance(override, str) else ""
    except Exception:
        pass
    from_env = (env(env_key) or "").strip() if env_key else ""
    return {
        "set": bool(override or from_env),
        "source": ("cove" if override else ("env" if from_env else "")),
        "label": label,
    }


@router.get("/api/pipeline/keys")
async def pipeline_keys_status(request: Request):
    """Auth status for every service the pipeline calls. NEVER returns secrets."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    out = {"ok": True, "services": {s: _service_status(s) for s in SERVICES}}
    # One surface for the whole pipeline: include the per-presence posting status
    # (managed by their own endpoints — /api/posting/*; read-only here).
    try:
        from src.dashboard.routes.posting_identity import resolve_x_creds
        x_creds, _ = await resolve_x_creds(request=request)
        out["posting"] = {"x_configured": x_creds is not None}
    except Exception:
        out["posting"] = {}
    try:
        from src.dashboard.routes.settings import _is_admin_presence
        out["can_edit"] = bool(await _is_admin_presence(request))
    except Exception:
        out["can_edit"] = False
    # CF-110 #3 — analysis-model dropdown state: the current pick + the registry list
    # to populate the select. "" pick == "Your agent's model (default)".
    out["analysis_model"] = {"selected": get_analysis_model(), "options": _analysis_model_options()}
    return out


def _analysis_model_options() -> list:
    """The model registry as {id, label} for the Analysis-model dropdown."""
    opts = []
    try:
        from src.config import load_models_registry
        for m in load_models_registry():
            mid = m.get("id")
            if mid:
                opts.append({"id": mid, "label": m.get("name") or m.get("label") or mid})
    except Exception:
        pass
    return opts


class AnalysisModel(BaseModel):
    model: str = ""


@router.post("/api/pipeline/analysis-model")
async def save_analysis_model(body: AnalysisModel, request: Request):
    """Set / clear the Cove-level video-analysis model. Admin only. Empty CLEARS
    it (back to the default agent chain). An unknown id is rejected so the moments
    chain never resolves a phantom model."""
    from src.dashboard.routes.settings import _is_admin_presence
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    model = (body.model or "").strip()
    if model:
        from src.config import get_model_from_registry
        if not get_model_from_registry(model):
            return JSONResponse(status_code=400, content={
                "error": "Unknown model id — pick one from the registry list."})
    from src.config import save_feature_overrides
    if not save_feature_overrides({ANALYSIS_MODEL_FLAG: model}):
        return JSONResponse(status_code=500, content={"error": "Could not save the analysis model."})
    log.info("analysis model %s by admin", "cleared" if not model else f"set to {model}")
    return {"ok": True, "analysis_model": {"selected": get_analysis_model(),
                                           "options": _analysis_model_options()}}


class ServiceKey(BaseModel):
    service: str
    key: str = ""


@router.post("/api/pipeline/keys")
async def save_pipeline_key(body: ServiceKey, request: Request):
    """Set / rotate / clear one Cove-wide pipeline service key. Admin only.
    An explicit empty key CLEARS it (env fallback, if any, still applies);
    the masked echo is ignored so a re-saved form can't wipe anything."""
    from src.dashboard.routes.settings import _is_admin_presence
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    service = (body.service or "").strip().lower()
    if service not in SERVICES:
        return JSONResponse(status_code=400, content={
            "error": f"Unknown service — expected one of: {', '.join(sorted(SERVICES))}"})
    key = (body.key or "").strip()
    if key == _MASK:
        return {"ok": True, "service": service, **_service_status(service)}  # masked echo: no-op
    flag_key = SERVICES[service][0]
    from src.config import save_feature_overrides
    if not save_feature_overrides({flag_key: key}):
        return JSONResponse(status_code=500, content={"error": "Could not save the key."})
    log.info("pipeline key %s %s by admin", service, "cleared" if not key else "saved")
    return {"ok": True, "service": service, **_service_status(service)}
