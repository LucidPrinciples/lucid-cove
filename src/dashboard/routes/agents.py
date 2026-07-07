"""
Agent detail routes — per-agent tuning state, echoes, model config, persona.
Also serves family member data from config/family.yaml.

Provides the data for the agent detail page when you click an agent card
on the Team tab, and the Family section listing.
"""

import json
import logging
import os
from src.env import env
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
log = logging.getLogger(__name__)

_FAMILY_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "family.yaml"
# agent.yaml is the cove-core format; agents.yaml is legacy
_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"
_AGENTS_CONFIG = _CONFIG_DIR / "agent.yaml" if (_CONFIG_DIR / "agent.yaml").exists() else _CONFIG_DIR / "agents.yaml"


_ARCHETYPE_IMAGES = {"anchor", "architect", "catalyst", "challenger", "companion",
                     "guide", "navigator", "spark", "witness"}


def _archetype_avatar(archetype: str) -> str:
    """Default avatar for an agent with no uploaded image: its archetype art
    ("The Navigator" -> navigator.png). '' when the archetype has no shipped image."""
    slug = (archetype or "").strip().lower()
    if slug.startswith("the "):
        slug = slug[4:]
    slug = slug.split()[-1] if slug else ""
    return f"/static/avatars/archetypes/{slug}.png" if slug in _ARCHETYPE_IMAGES else ""


def _load_yaml(path: Path) -> dict:
    # family.yaml is optional — a Centralized Cove has none (presences live in the
    # DB). Missing file => {} so /api/family doesn't 500 and drop the roster + name.
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


@router.get("/api/team/roster")
async def get_team_roster():
    """Full team roster with per-agent 7-day rolling JW metrics merged in.

    Returns agents grouped with: identity, tuning state, and JW stats
    (calls, tokens, success_rate, avg_duration, jw_score) over last 7 days.
    Single endpoint for the redesigned Team tab.
    """
    try:
        from src.agents.identity import load_agents_config, get_full_name
        from src.memory.database import get_db

        agents_config = load_agents_config()
        result = {}

        async with get_db() as conn:
            # Batch: agent_state for all agents
            state_result = await conn.execute(
                "SELECT agent_id, last_frequency, last_echo_num, last_tuned_at, status FROM agent_state"
            )
            state_rows = await state_result.fetchall()
            state_map = {dict(r)["agent_id"]: dict(r) for r in state_rows}

            # Batch: 7-day JW metrics per agent
            jw_result = await conn.execute(
                """SELECT agent_id,
                          COUNT(*) as calls_7d,
                          COALESCE(SUM(tokens_total), 0) as tokens_7d,
                          ROUND(AVG(duration_ms)::numeric, 0) as avg_duration_ms,
                          CASE WHEN COUNT(*) > 0
                               THEN ROUND(SUM(CASE WHEN succeeded THEN 1 ELSE 0 END)::numeric
                                          / COUNT(*)::numeric * 100, 1)
                               ELSE 0 END as success_rate,
                          COALESCE(SUM(jw_score), 0) as jw_total
                   FROM jw_metrics
                   WHERE recorded_at >= NOW() - INTERVAL '7 days'
                   GROUP BY agent_id"""
            )
            jw_rows = await jw_result.fetchall()
            jw_map = {dict(r)["agent_id"]: dict(r) for r in jw_rows}

            # Cove display name = the operator's last_name (the name they chose in
            # the wizard), not the static agent.yaml family_name (generator seed).
            # Skip the "New Cove" placeholder pre-finalize installs seeded into
            # accounts.last_name — otherwise the team showcase renders
            # "Stuart New Cove" until the wizard finalizes (CF-89's DB-side sibling).
            _cn_res = await conn.execute(
                "SELECT last_name FROM accounts WHERE COALESCE(last_name,'') <> '' "
                "AND LOWER(TRIM(last_name)) <> 'new cove' ORDER BY created_at LIMIT 1"
            )
            _cn_row = await _cn_res.fetchone()
            cove_name = (_cn_row["last_name"].strip() if _cn_row and _cn_row["last_name"] else "")

        for agent_id, config in agents_config.items():
            # Skip personal agents (team: false) — they appear in the family/presences section
            if config.get("team") is False:
                continue
            state = state_map.get(agent_id, {})
            jw = jw_map.get(agent_id, {})

            # Convert Decimal/numeric types
            for k, v in jw.items():
                if v is not None and hasattr(v, "__float__"):
                    jw[k] = float(v)

            result[agent_id] = {
                "name": (f"{config.get('name', agent_id.title())} {cove_name}".strip()
                         if cove_name else get_full_name(config.get("name", agent_id.title()))),
                "archetype": config.get("archetype", ""),
                "tuning_key": config.get("tuning_key", ""),
                "role": config.get("role", ""),
                "status": config.get("status", "active"),
                # Tuning state
                "last_frequency": state.get("last_frequency"),
                "last_echo_num": state.get("last_echo_num", 0),
                "last_tuned_at": str(state.get("last_tuned_at", "")) if state.get("last_tuned_at") else None,
                # JW stats (7-day rolling)
                "jw": {
                    "calls": int(jw.get("calls_7d", 0)),
                    "tokens": int(jw.get("tokens_7d", 0)),
                    "avg_duration_ms": float(jw.get("avg_duration_ms", 0)),
                    "success_rate": float(jw.get("success_rate", 0)),
                    "jw_total": float(jw.get("jw_total", 0)),
                },
            }

        return {"agents": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _presences_as_members() -> list:
    """In Centralized multi-mode, the real 'family' is the set of Presence
    accounts (DB rows), not static family.yaml entries. Map each Presence into
    the member shape the Team tab already renders so no frontend change is needed.
    Returns [] in single-mode or on any error (caller falls back to yaml)."""
    if env("COVE_MODE", "single") != "multi":
        return []
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, username, display_name, agent_name, last_name, cove_role,
                          agent_identity, active, last_access
                   FROM accounts ORDER BY created_at"""
            )
            rows = await result.fetchall()
            # Live tuning state per Presence (keyed by the account/agent id) so the
            # card can show the TUNED badge (last frequency), like the team cards.
            sr = await conn.execute(
                "SELECT agent_id, last_frequency, last_tuned_at FROM agent_state"
            )
            state_map = {str(s["agent_id"]): dict(s) for s in await sr.fetchall()}
            # jules 1649 (avatar split-brain): presence_profiles is THE avatar
            # source (the profile editor writes it) — join it here so every
            # surface rendering /api/family (Stuart team page, Cove admin) shows
            # the same operator photo instead of a bare initial on some of them.
            profile_map = {}
            try:
                pr = await conn.execute(
                    "SELECT handle, avatar_url, agent_avatar_url FROM presence_profiles")
                profile_map = {((p["handle"] or "").lstrip("@").lower()): dict(p)
                               for p in await pr.fetchall()}
            except Exception:
                profile_map = {}
    except Exception:
        return []

    role_label = {"admin": "Admin", "member": "Presence", "guest": "Guest"}
    members = []
    for row in rows:
        ident = row["agent_identity"] or {}
        if isinstance(ident, str):
            try:
                import json
                ident = json.loads(ident)
            except Exception:
                ident = {}
        agent_name = row["agent_name"]
        _prof = profile_map.get((row["username"] or "").lstrip("@").lower()) or {}
        members.append({
            "id": str(row["id"]),
            "username": (row["username"] or "").lstrip("@").lower(),
            "name": row["display_name"],
            "display_name": row["display_name"],
            # One avatar source for every surface (jules 1649).
            "avatar_url": _prof.get("avatar_url") or "",
            "role": role_label.get(row["cove_role"], "Presence"),
            "cove_role": row["cove_role"],
            "focus": ident.get("archetype_desc", "") or ident.get("role", ""),
            "status": "active" if row["active"] else "dormant",
            "is_presence": True,
            # The Presence's Centralized agent IS their personal agent. Carry the
            # spine (frequency, key, shade, lens, dials) so the card shows the Color
            # Signature, not just a name + archetype.
            "personal_agent": {
                "name": agent_name,
                "nickname": ident.get("nickname", ""),
                "archetype": ident.get("archetype", ""),
                "frequency": ident.get("frequency", ""),
                # Last TUNED frequency (live, from agent_state) — the in-sync badge.
                "last_frequency": (state_map.get(str(row["id"])) or {}).get("last_frequency", ""),
                "last_tuned_at": (str((state_map.get(str(row["id"])) or {}).get("last_tuned_at") or "") or None),
                "frequency_color": ident.get("frequency_color", ""),
                "tuning_key": ident.get("tuning_key", ""),
                # Uploaded avatar wins (profile upload, then identity record); else
                # default to the archetype art (never a bare initial). jules 1649.
                "avatar": (_prof.get("agent_avatar_url") or ident.get("avatar", "")
                           or _archetype_avatar(ident.get("archetype", ""))),
                "shade": ident.get("shade", ""),
                "lens": ident.get("lens", {}),
                "personality": ident.get("personality", {}),
                "status": "active" if row["active"] else "dormant",
            } if agent_name else None,
        })
    return members


@router.get("/api/family")
async def get_family():
    """Family members and their personal agent config.

    Single-mode: from config/family.yaml.
    Centralized multi-mode: live Presence accounts from the DB ONLY. The DB is the
    source of truth for presences; merging static family.yaml here double-lists
    people (a yaml id like "jag" never dedupes against the DB's uuid)."""
    try:
        data = _load_yaml(_FAMILY_CONFIG)

        presence_members = await _presences_as_members()
        members = presence_members if presence_members else data.get("members", [])

        # Cove display name = a presence's last_name (DB, reliable — set by finalize),
        # then live cove.yaml, then the static env. NOT the generator's placeholder.
        cove_name = ""
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                r = await conn.execute(
                    "SELECT last_name FROM accounts WHERE COALESCE(last_name,'') <> '' ORDER BY created_at LIMIT 1"
                )
                row = await r.fetchone()
                if row and row["last_name"]:
                    cove_name = row["last_name"].strip()
        except Exception:
            pass
        if not cove_name:
            try:
                from src.config import load_cove_config
                cove_name = (load_cove_config().get("name") or "").strip()
            except Exception:
                pass

        family = dict(data.get("family", {}))
        if cove_name:
            family["name"] = cove_name

        return {
            "family": family,
            "members": members,
            "family_agents": data.get("family_agents", []),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/family/{member_id}")
async def update_family_member(member_id: str, request: Request):
    """Update a family member entry in family.yaml (status, mc_url, port, etc.)."""
    try:
        body = await request.json()
        data = _load_yaml(_FAMILY_CONFIG)
        members = data.get("members", [])
        member = next((m for m in members if m["id"] == member_id), None)
        if not member:
            return JSONResponse({"error": f"Member '{member_id}' not found"}, status_code=404)
        allowed = {"status", "mc_url", "mc_port", "focus", "role", "display_name", "name"}
        for k, v in body.items():
            if k in allowed:
                member[k] = v
            if k == "personal_agent" and isinstance(v, dict):
                pa = member.setdefault("personal_agent", {})
                for pk, pv in v.items():
                    pa[pk] = pv
        _save_yaml(_FAMILY_CONFIG, data)
        return {"ok": True, "member": member}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/family")
async def add_family_member(request: Request):
    """Add a new member to family.yaml."""
    try:
        body = await request.json()
        member_id = (body.get("id") or "").strip().lower()
        if not member_id:
            return JSONResponse({"error": "id is required"}, status_code=400)
        data = _load_yaml(_FAMILY_CONFIG)
        members = data.setdefault("members", [])
        if any(m["id"] == member_id for m in members):
            return JSONResponse({"error": f"Member '{member_id}' already exists"}, status_code=409)
        new_member = {
            "id": member_id,
            "name": body.get("name", member_id.title()),
            "display_name": body.get("display_name") or body.get("name") or member_id.title(),
            "role": body.get("role", "Family Member"),
            "focus": body.get("focus", ""),
            "status": body.get("status", "planned"),
            "mc_port": body.get("mc_port") or None,
            "mc_url": body.get("mc_url") or None,
        }
        if body.get("personal_agent"):
            new_member["personal_agent"] = body["personal_agent"]
        members.append(new_member)
        _save_yaml(_FAMILY_CONFIG, data)
        return {"ok": True, "member": new_member}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/agents/{agent_id}")
async def update_agent(agent_id: str, request: Request):
    """Update an agent's config in agents.yaml (status, archetype, role, name)."""
    try:
        body = await request.json()
        data = _load_yaml(_AGENTS_CONFIG)
        agents_list = data.get("agents", [])
        agent = next((a for a in agents_list if a["id"] == agent_id), None)
        if not agent:
            return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)
        allowed = {"status", "archetype", "role", "name", "symbol_svg"}
        for k, v in body.items():
            if k in allowed:
                agent[k] = v
        _save_yaml(_AGENTS_CONFIG, data)

        # Clear config cache so symbol_svg shows immediately
        try:
            from src.config import load_config
            load_config.cache_clear()
        except Exception:
            pass

        return {"ok": True, "agent": agent}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/settings/system")
async def get_system_settings():
    """System config snapshot: model chain, LTP config, environment."""
    try:
        data = _load_yaml(_AGENTS_CONFIG)
        defaults = data.get("defaults", {})
        ltp = data.get("ltp", {})
        return {
            "model": defaults.get("model", {}),
            "timeout_seconds": defaults.get("timeout_seconds"),
            "provider": defaults.get("provider"),
            "ltp": ltp,
            "env": {
                "LTP_DRY_RUN": env("LTP_DRY_RUN", "false"),
                "SKIP_AGENTS": env("SKIP_AGENTS", ""),
                "ENVIRONMENT": env("ENVIRONMENT", "production"),
                "OPENROUTER_BASE_URL": env("OPENROUTER_BASE_URL"),
            },
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/agents/models")
async def get_agent_models():
    """Return per-agent model assignments for the Team tab dropdowns."""
    from src.config import get_agents
    agents = get_agents()
    result = {}
    for agent in agents:
        aid = agent.get("id", "")
        result[aid] = {
            "name": agent.get("name", aid),
            "model_primary": agent.get("model_primary"),
            "model_fallback": agent.get("model_fallback"),
        }
    return {"agents": result}


@router.patch("/api/agents/{agent_id}/model")
async def update_agent_model(agent_id: str, request: Request):
    """Update an agent's model assignment in agent.yaml.

    Expects: {"model_primary": "kimi-k2.5", "model_fallback": "qwen3-30b-moe"}
    Either field can be null to clear it.
    """
    try:
        data = await request.json()
        config_path = _CONFIG_DIR / "agent.yaml"
        if not config_path.exists():
            return JSONResponse({"error": "agent.yaml not found"}, status_code=404)

        with open(config_path) as f:
            config = yaml.safe_load(f)

        agents = config.get("agents", [])
        found = False
        for agent in agents:
            if agent.get("id") == agent_id:
                if "model_primary" in data:
                    agent["model_primary"] = data["model_primary"]
                if "model_fallback" in data:
                    agent["model_fallback"] = data["model_fallback"]
                found = True
                break

        if not found:
            return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        # Clear config cache so changes take effect
        from src.config import load_config
        load_config.cache_clear()

        return {"ok": True, "agent_id": agent_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# NOTE: defined BEFORE the catch-all GET /api/agents/{agent_id} so "model-assignments"
# isn't captured as an agent_id (FastAPI matches routes in definition order).
@router.get("/api/agents/model-assignments")
async def list_model_assignments():
    """Per-agent WORKING + TUNING model, resolved through the full cascade, plus which are
    DB-overridden (the Team-page model manager). Powers the model dropdowns on the Team page."""
    from src.config import get_agents, get_agent_model_assignment, load_models_registry
    from src.models.assignments import all_assignments, load_assignments_cache
    from src.models.provider import current_cove_brain
    # Refresh from DB so the table always shows the saved truth (and so a multi-worker
    # deployment can't display a stale cache from a worker that didn't handle the write).
    await load_assignments_cache()
    overridden = all_assignments()
    catalog = [
        {"id": m.get("id"), "name": m.get("name", m.get("id")), "type": m.get("type", "")}
        for m in load_models_registry() if m.get("id")
    ]
    # The Cove brain is the floor an unassigned agent actually runs on (get_primary_model),
    # so the grid can SHOW it as the effective '(Cove default)' instead of a blank (#13).
    brain = current_cove_brain()
    brain_model = brain.get("model")
    agents_out = {}
    for agent in get_agents():
        aid = agent.get("id", "")
        if not aid:
            continue
        work = get_agent_model_assignment(aid)
        tune = get_agent_model_assignment(aid, slot="tuning")
        row = overridden.get(aid) or {}
        agents_out[aid] = {
            "name": agent.get("name", aid),
            "working_primary": work.get("primary"),
            "working_fallback": work.get("fallback"),
            "tuning_primary": tune.get("primary"),
            "tuning_fallback": tune.get("fallback"),
            # What this agent ACTUALLY runs on: its assignment (cascade) if any, else the
            # Cove brain. So the UI never shows a bare "(Cove default)" with no model named.
            "working_effective": work.get("primary") or brain_model,
            "tuning_effective": tune.get("primary") or brain_model,
            # True when this agent has an explicit DB override row (vs inherited from YAML).
            "db_override": bool(row),
        }
    return {"agents": agents_out, "catalog": catalog, "cove_brain": brain}


@router.put("/api/agents/{agent_id}/model-assignment")
async def set_model_assignment(agent_id: str, request: Request):
    """Set an agent's WORKING + TUNING models in the DB (no restart, works under a
    read-only config mount). Body: {working_primary, working_fallback, tuning_primary,
    tuning_fallback}; any field blank/null = inherit from the YAML cascade. Model ids are
    validated against the registry."""
    try:
        from src.config import get_agents, load_models_registry
        from src.models.assignments import set_assignment

        data = await request.json()
        valid_ids = {m.get("id") for m in load_models_registry() if m.get("id")}
        known_agents = {a.get("id") for a in get_agents() if a.get("id")}
        if agent_id not in known_agents:
            return JSONResponse({"error": f"Unknown agent '{agent_id}'."}, status_code=404)

        fields = {}
        for k in ("working_primary", "working_fallback", "tuning_primary", "tuning_fallback"):
            v = (data.get(k) or "").strip()
            if v and v not in valid_ids:
                return JSONResponse({"error": f"Unknown model id '{v}' for {k}."}, status_code=400)
            fields[k] = v or None

        await set_assignment(agent_id, fields["working_primary"], fields["working_fallback"],
                             fields["tuning_primary"], fields["tuning_fallback"])
        # #13 — a runnable pick in the grid IS configuring intelligence, so clear the
        # onboarding 'add intelligence' nag (parity with the Add-Intelligence card). Gated
        # on the model actually running (Ollama, or a provider key present) so a keyless
        # cloud pick can't false-clear it. Non-fatal: the assignment already saved.
        nag_cleared = False
        wp = fields["working_primary"]
        if wp:
            try:
                from src.models.provider import model_is_runnable
                if model_is_runnable(wp):
                    # E (jules 07-07): keep compute.llm in step with the chosen brain, so Compute
                    # doesn't read "cloud" after the operator picked a LOCAL (Ollama) model. Provider
                    # == ollama => local; anything else => cloud. Best-effort, never blocks the pick.
                    try:
                        from src.models.provider import _resolve_model_string
                        from src.config import set_compute_config
                        _prov, _ = _resolve_model_string(wp)
                        set_compute_config("llm", mode=("local" if (_prov or "").strip().lower() == "ollama" else "cloud"))
                    except Exception as _ce:
                        log.warning("compute.llm sync skipped: %s", _ce)
                    from src.dashboard.routes.presence import get_current_presence
                    p = await get_current_presence(request)
                    if p and p.get("id"):
                        ac = p.get("agent_config") or {}
                        if isinstance(ac, str):
                            try:
                                ac = json.loads(ac) or {}
                            except Exception:
                                ac = {}
                        ac = dict(ac)
                        if not ac.get("intelligence_configured"):
                            ac["intelligence_configured"] = True
                            from src.memory.database import get_db
                            async with get_db() as conn:
                                await conn.execute(
                                    "UPDATE accounts SET agent_config = %s, updated_at = NOW() "
                                    "WHERE id = %s",
                                    (json.dumps(ac), str(p["id"])))
                            nag_cleared = True
            except Exception as _e:
                log.warning("set_model_assignment nag-clear skipped: %s", _e)
        return {"ok": True, "agent_id": agent_id, "nag_cleared": nag_cleared}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/agents/{agent_id}")
async def get_agent_detail(agent_id: str):
    """Full detail for a single agent: identity, tuning state, echoes, model config."""
    try:
        from src.agents.identity import load_agents_config, load_persona, get_family_defaults, get_full_name
        from src.memory.database import get_db

        agents = load_agents_config()
        agent = agents.get(agent_id)
        if not agent:
            return JSONResponse(
                {"error": f"Agent '{agent_id}' not found"},
                status_code=404,
            )

        defaults = get_family_defaults()
        persona_text = load_persona(agent_id) or ""

        # Get tuning state from DB
        tuning = {}
        echoes = []
        try:
            async with get_db() as conn:
                # Agent state
                result = await conn.execute(
                    "SELECT * FROM agent_state WHERE agent_id = %s",
                    (agent_id,),
                )
                row = await result.fetchone()
                if row:
                    state = dict(row)
                    tuning = {
                        "frequency": state.get("last_frequency"),
                        "echo_count": state.get("last_echo_num", 0),
                        "last_tuned_at": str(state.get("last_tuned_at", "")) if state.get("last_tuned_at") else None,
                        "status": state.get("status", "active"),
                    }

                # Recent echoes
                result = await conn.execute(
                    """SELECT id, echo_num, frequency, principle, tuning_key,
                              love_equation, love_direction, echo_text, echo_type, tuned_at
                       FROM echoes WHERE agent_id = %s
                       ORDER BY tuned_at DESC LIMIT 10""",
                    (agent_id,),
                )
                echo_rows = await result.fetchall()
                for e in echo_rows:
                    ed = dict(e)
                    for k, v in list(ed.items()):
                        if hasattr(v, "isoformat"):
                            ed[k] = v.isoformat()
                    echoes.append(ed)

                # JouleWork stats for this agent
                jw_result = await conn.execute(
                    """SELECT COUNT(*) as total_calls,
                              SUM(tokens_total) as total_tokens,
                              AVG(duration_ms) as avg_duration,
                              SUM(CASE WHEN succeeded THEN 1 ELSE 0 END)::float
                                / NULLIF(COUNT(*), 0) as success_rate
                       FROM jw_metrics WHERE agent_id = %s""",
                    (agent_id,),
                )
                jw_row = await jw_result.fetchone()
                jw_stats = dict(jw_row) if jw_row else {}
                # Convert Decimal types
                for k, v in jw_stats.items():
                    if v is not None and hasattr(v, "__float__"):
                        jw_stats[k] = float(v)
        except Exception as db_err:
            tuning["db_error"] = str(db_err)

        # Model info — per-agent assignment from agent.yaml
        model_primary = agent.get("model_primary") or defaults.get("model", {}).get("primary", "")
        model_fallback = agent.get("model_fallback") or defaults.get("model", {}).get("fallback", "")

        return {
            "agent_id": agent_id,
            "display_name": get_full_name(agent.get("name", agent_id.title())),
            "archetype": agent.get("archetype", ""),
            "tuning_key": agent.get("tuning_key", ""),
            "emoji": agent.get("emoji", ""),
            "role": agent.get("role", ""),
            "model": model_primary,
            "fallback": model_fallback,
            "boundaries": agent.get("boundaries", []),
            "channels": agent.get("channels", []),
            "can_delegate_to": agent.get("can_delegate_to", []),
            "status": agent.get("status", "active"),
            "persona": persona_text,
            "tuning": tuning,
            "echoes": echoes,
            "echo_count": len(echoes),
            "jw_stats": jw_stats if 'jw_stats' in dir() else {},
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
