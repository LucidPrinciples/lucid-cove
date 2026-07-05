"""
Cove Settings API — read and update family-level configuration.

Serves the MC Settings tab. Reads from config/cove.yaml via config.py.
Write operations merge updates into the existing config and save back.

Endpoints:
  GET  /api/settings/cove          — full Cove settings
  PUT  /api/settings/cove          — update Cove settings (partial merge)
  GET  /api/settings/features      — feature flags only
  PUT  /api/settings/features      — update feature flags
  GET  /api/settings/capacity      — capacity settings only
  GET  /api/settings/identity      — Cove identity + operator
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from src.env import env
from src.config import (
    get_cove_settings,
    get_cove_identity,
    get_cove_operator,
    get_capacity_config,
    get_feature_flags,
    get_billing_config,
    get_auth_config,
    get_naming_config,
    get_team_config,
    save_cove_config,
    list_build_team_agents,
    get_agent_model_assignment,
    set_team_model,
    load_models_registry,
    get_compute_config,
    set_compute_config,
    COMPUTE_MODES,
)

router = APIRouter()


# ── Read endpoints ───────────────────────────────────────────────────────────


@router.get("/api/settings/cove")
async def cove_settings():
    """Full Cove settings for the Settings tab."""
    try:
        return get_cove_settings()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to load cove settings: {e}"},
        )


@router.get("/api/settings/identity")
async def cove_identity():
    """Cove identity and operator info."""
    return {
        **get_cove_identity(),
        "operator": get_cove_operator(),
    }


# ── Team model management (Stuart-level) ─────────────────────────────────────


@router.get("/api/settings/team-models")
async def team_models():
    """Build-team agents + their resolved model assignments, plus the catalog of
    available models for the dropdowns. The Stuart-level model management layer."""
    try:
        agents = []
        for a in list_build_team_agents():
            m = get_agent_model_assignment(a["id"])
            agents.append({**a, "primary": m.get("primary") or "", "fallback": m.get("fallback") or ""})
        catalog = [
            {
                "id": m.get("id"),
                "name": m.get("name", m.get("id")),
                "provider": m.get("provider", ""),
                "type": m.get("type", ""),
            }
            for m in load_models_registry() if m.get("id")
        ]
        return {"agents": agents, "catalog": catalog}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to load team models: {e}"})


@router.post("/api/settings/reload")
async def reload_config():
    """Clear cached config so edits (cove.yaml, models.yaml) take effect without a
    container restart. A restart is only needed when a brand-new provider's API key
    must be loaded from the environment for the first time."""
    cleared = []
    try:
        import src.config as _cfg
        for name in ("load_cove_config", "load_models_registry", "load_config"):
            fn = getattr(_cfg, name, None)
            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()
                cleared.append(name)
        return {"ok": True, "cleared": cleared}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


class TeamModelUpdate(BaseModel):
    primary: Optional[str] = None
    fallback: Optional[str] = None


@router.put("/api/settings/team-models/{agent_id}")
async def update_team_model(agent_id: str, body: TeamModelUpdate):
    """Set a build-team agent's primary/fallback model (Stuart-level). Writes cove.yaml."""
    try:
        ok = set_team_model(agent_id, primary=body.primary, fallback=body.fallback)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "save failed"})
        return {"ok": True, "agent_id": agent_id, "model": get_agent_model_assignment(agent_id)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Compute backends (Admin Presence — where heavy work runs) ────────────────


async def _is_admin_presence(request: Request) -> bool:
    """True only for an Admin (operator) Presence — the role allowed to change cove-wide
    compute routing (LLM / voice / video transcription)."""
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request) or {}
        return (p.get("cove_role") or "") == "admin"
    except Exception:
        return False


@router.get("/api/settings/compute")
async def compute_settings():
    """Effective compute-backend config (llm / voice / video_asr) + allowed modes for
    the Settings dropdowns. Read-open within the Cove; writes are Admin-only."""
    try:
        cfg = get_compute_config()
        # Never expose a raw GPU-rent token to the browser (read-open within the Cove).
        # Report presence only as has_token — for EVERY section (a token can ride any
        # external backend, not just video_asr).
        for _sec, _val in list(cfg.items()):
            if isinstance(_val, dict) and "token" in _val:
                _val = dict(_val)
                _val["has_token"] = bool(_val.pop("token", ""))
                cfg[_sec] = _val
        return {"compute": cfg,
                "modes": {k: list(v) for k, v in COMPUTE_MODES.items()}}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to load compute config: {e}"})


class ComputeUpdate(BaseModel):
    mode: Optional[str] = None
    url: Optional[str] = None
    token: Optional[str] = None


@router.put("/api/settings/compute/{section}")
async def update_compute(section: str, body: ComputeUpdate, request: Request):
    """Set a compute section (llm|voice|video_asr) mode/url/token. ADMIN PRESENCE ONLY.
    Writes cove.yaml `compute:` (cache cleared on save). `external` URL + token = borrow
    another Cove's GPU (the GPU-rent path); token is the grant the provider handed over."""
    if not await _is_admin_presence(request):
        return JSONResponse(status_code=403, content={"error": "Admin Presence only"})
    try:
        ok = set_compute_config(section, mode=body.mode, url=body.url, token=body.token)
        if not ok:
            return JSONResponse(status_code=400, content={"error": "invalid section/mode or save failed"})
        return {"ok": True, "section": section, "compute": get_compute_config().get(section)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Presence personal-agent model override (presence-level) ──────────────────


@router.get("/api/settings/my-model")
async def my_model(request: Request):
    """The current presence's personal-agent model override + the catalog. Empty
    override means the agent uses the Stuart-set team/instance default."""
    try:
        from src.dashboard.routes.presence import get_current_presence
        from src.config import (get_agent_model_assignment, get_primary_agent_id,
                                 get_model_from_registry)
        p = await get_current_presence(request) or {}
        ai = p.get("agent_identity") or {}
        m = (ai.get("model") if isinstance(ai, dict) else None) or {}

        # The Cove default this presence inherits when no override is set (what the
        # steward set for the team, resolved through the cascade).
        dflt = get_agent_model_assignment(get_primary_agent_id())

        # jules 1816: only present a Cove default that is REAL — an explicit steward
        # assignment (DB/team_models layers) or a connected Cove brain. The static
        # agent.yaml step-3 fallback (a qwen id) used to render as a preselected
        # model on a fresh Cove before ANY intelligence existed.
        default_real = False
        try:
            from src.models.assignments import cached_assignment
            _db = cached_assignment(get_primary_agent_id(), None)
            default_real = bool(_db and (_db.get("primary") or _db.get("fallback")))
        except Exception:
            pass
        if not default_real:
            try:
                from src.config import load_cove_config
                _tm = (load_cove_config().get("team_models") or {}).get(get_primary_agent_id()) or {}
                default_real = bool(_tm.get("primary") or _tm.get("fallback"))
            except Exception:
                pass
        if not default_real:
            try:
                from src.models import provider as _prov
                default_real = bool(getattr(_prov, "_cove_primary", None))
            except Exception:
                pass

        def _nm(mid):
            if not mid:
                return ""
            return (get_model_from_registry(mid) or {}).get("name", mid)

        catalog = [
            {"id": x.get("id"), "name": x.get("name", x.get("id")), "type": x.get("type", "")}
            for x in load_models_registry() if x.get("id")
        ]
        return {
            "primary": m.get("primary") or "",
            "fallback": m.get("fallback") or "",
            "default_primary_name": _nm(dflt.get("primary")) if default_real else "",
            "default_fallback_name": _nm(dflt.get("fallback")) if default_real else "",
            "default_set": default_real,
            "agent_name": p.get("agent_name") or (ai.get("agent_name") if isinstance(ai, dict) else "") or "your agent",
            "catalog": catalog,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.put("/api/settings/my-model")
async def update_my_model(request: Request, body: TeamModelUpdate):
    """Set the current presence's personal-agent model override (agent_identity.model)."""
    try:
        import json
        from src.dashboard.routes.presence import get_current_presence
        from src.memory.database import get_db
        p = await get_current_presence(request)
        if not p or not p.get("id"):
            return JSONResponse(status_code=401, content={"error": "no current presence"})
        ai = dict(p.get("agent_identity") or {})
        model = dict(ai.get("model") or {})
        if body.primary is not None:
            model["primary"] = body.primary
        if body.fallback is not None:
            model["fallback"] = body.fallback
        ai["model"] = model
        async with get_db() as conn:
            await conn.execute(
                "UPDATE accounts SET agent_identity = %s WHERE id = %s",
                (json.dumps(ai), str(p["id"])),
            )
        return {"ok": True, "model": model}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/settings/features")
async def feature_flags():
    """Feature flags — what's enabled for Presences.

    The raw GitHub token is never exposed to the browser — only whether one is
    set (github_pat_set). The masked value keeps existing truthiness checks happy
    without leaking the secret.
    """
    flags = dict(get_feature_flags())
    if flags.get("github_pat"):
        flags["github_pat"] = "********"
        flags["github_pat_set"] = True
    else:
        flags["github_pat_set"] = False
    return flags


@router.get("/api/settings/capacity")
async def capacity():
    """Capacity configuration."""
    return get_capacity_config()


@router.get("/api/settings/billing")
async def billing():
    """Billing and API provider config."""
    return get_billing_config()


@router.get("/api/settings/auth")
async def auth():
    """Auth configuration."""
    return get_auth_config()


@router.get("/api/settings/naming")
async def naming():
    """Naming rules."""
    return get_naming_config()


@router.get("/api/settings/team")
async def team():
    """Team agent configuration."""
    return get_team_config()


# ── Write endpoints ──────────────────────────────────────────────────────────


class CoveSettingsUpdate(BaseModel):
    """Partial update to Cove settings. Only include fields to change."""

    # Identity
    name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None

    # Operator
    operator: Optional[dict] = None

    # Capacity
    max_presences: Optional[int] = None
    recommended_presences: Optional[int] = None
    presence_cap_warning: Optional[int] = None

    # Billing
    api_provider: Optional[str] = None
    billing: Optional[dict] = None

    # Infrastructure
    infrastructure: Optional[str] = None
    timezone: Optional[str] = None

    # Team
    team: Optional[dict] = None

    # Features
    features: Optional[dict] = None

    # Naming
    naming: Optional[dict] = None

    # Auth
    auth: Optional[dict] = None

    # Cascade defaults
    defaults: Optional[dict] = None


@router.put("/api/settings/cove")
async def update_cove_settings(update: CoveSettingsUpdate):
    """Update Cove settings. Partial merge — only include fields to change.

    Writes to config/cove.yaml and clears the config cache.
    """
    # Build update dict from non-None fields
    changes = {k: v for k, v in update.model_dump().items() if v is not None}

    if not changes:
        return JSONResponse(
            status_code=400,
            content={"error": "No changes provided"},
        )

    success = save_cove_config(changes)
    if success:
        return {"success": True, "updated_keys": list(changes.keys())}
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to save settings"},
        )


class FeatureFlagsUpdate(BaseModel):
    """Update individual feature flags."""

    team_tab: Optional[bool] = None
    action_board: Optional[bool] = None
    creation_flows: Optional[bool] = None
    files: Optional[bool] = None
    calendar: Optional[bool] = None
    tuning: Optional[bool] = None
    voice: Optional[bool] = None
    messaging: Optional[bool] = None
    marketplace: Optional[bool] = None
    premium_workflows: Optional[bool] = None
    mirror: Optional[bool] = None


@router.put("/api/settings/features")
async def update_features(update: FeatureFlagsUpdate):
    """Update feature flags. Partial — only include flags to change."""
    changes = {k: v for k, v in update.model_dump().items() if v is not None}

    if not changes:
        return JSONResponse(
            status_code=400,
            content={"error": "No changes provided"},
        )

    success = save_cove_config({"features": changes})
    if success:
        return {"ok": True, "updated_features": changes}
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to save feature flags"},
        )


@router.patch("/api/settings/features")
async def patch_features(request: Request):
    """PATCH feature flags — accepts arbitrary flag keys for flexibility.

    In multi-Presence mode (VPS shared container), saves to the presence's
    preferences column in the DB. In single mode, saves to cove.yaml.
    """
    import os
    cove_mode = env("COVE_MODE", "single")

    body = await request.json()
    if not body or not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "No changes provided"},
        )

    # Accept boolean, string, and list values (lists for excluded_signals, mirror_sources, etc.)
    changes = {k: v for k, v in body.items() if isinstance(v, (bool, str, list))}

    if not changes:
        return JSONResponse(
            status_code=400,
            content={"error": "No valid flags provided"},
        )

    # github_pat is a Cove-level credential. Every reader (_get_github_pat, the site
    # deploy) resolves it via get_feature_flags(), which reads the writable overrides
    # file in BOTH modes but NEVER per-presence prefs. So persist github_pat to the
    # overrides file regardless of mode — otherwise in multi mode it lands in presence
    # prefs where no reader looks and deploy keeps failing "github_pat not set".
    if "github_pat" in changes:
        pat_val = changes.pop("github_pat")
        # Ignore empty / masked values so an echoed GET (which redacts to "********")
        # can never clobber a real saved token.
        if isinstance(pat_val, str) and pat_val and pat_val != "********":
            from src.config import save_feature_overrides
            if not save_feature_overrides({"github_pat": pat_val}):
                return JSONResponse(status_code=500, content={"error": "Failed to save GitHub token"})
        if not changes:
            return {"ok": True, "updated_features": {"github_pat": "saved"}}

    # Multi-Presence mode: save to DB per-presence
    if cove_mode == "multi":
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        if not presence:
            return JSONResponse(
                status_code=401,
                content={"error": "Not authenticated"},
            )

        try:
            import json
            from src.memory.database import get_db
            async with get_db() as conn:
                # Merge into existing preferences
                current_prefs = presence.get("preferences") or {}
                current_features = current_prefs.get("features", {})
                current_features.update(changes)
                current_prefs["features"] = current_features
                await conn.execute(
                    "UPDATE accounts SET preferences = %s, updated_at = NOW() WHERE id = %s",
                    (json.dumps(current_prefs), presence["id"])
                )
            return {"ok": True, "updated_features": changes}
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"error": f"Database error: {e}"},
            )

    # Single mode: save to writable feature overrides (/app/data/feature-overrides.yaml)
    # Config dir is mounted :ro, so we write overrides to the data volume instead.
    from src.config import save_feature_overrides
    success = save_feature_overrides(changes)
    if success:
        return {"ok": True, "updated_features": changes}
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to save feature flags"},
        )
