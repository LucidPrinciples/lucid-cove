"""Core routes — system status, health, and frontend config.

The /api/config endpoint is THE bridge between agent.yaml and the frontend.
The dynamic JS bootstrap reads it on page load to build tabs, set colors,
and know which channels exist.
"""

import os
from src.env import env, env_bool
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import (
    get_instance, get_frontend_config, get_primary_agent_id,
    get_model_defaults, get_ltp_config,
    load_models_registry, save_models_registry,
    get_steward_channel_config, get_merchant_channel_config,
    load_cove_config,
)
from src.permissions import (
    get_tier_info, TAB_TIER_REQUIREMENTS, TAB_TIER_MAX,
)

router = APIRouter()


def _is_public_app() -> bool:
    """The shared multi-tenant public app has no per-operator agent of its own.
    Agent state/echoes are a Cove feature — gate those reads off so the public
    app never returns another presence's agent data."""
    return env_bool("LP_REGISTRY_MASTER")


class _SkipAgentState(Exception):
    """Internal: bail out of an agent_state read on the public app (no agent)."""


# Build version — cache-bust key for ?v= on static assets.
# Under GIT-FLOW the deployed commit hash changes every deploy, so it's the most
# reliable key (the old .build-version file was only written by the rsync deploy
# script and goes stale under git-flow — which silently broke static cache-busting).
# Order: git HEAD of /cove-core -> .build-version file -> container start timestamp.
# Computed once per container (a recreate = new container = fresh value), so no
# per-request subprocess cost.
_BUILD_VERSION_FALLBACK = datetime.now().strftime("%Y%m%d%H%M%S")
_BUILD_VERSION_CACHE = None

def _get_build_version():
    global _BUILD_VERSION_CACHE
    if _BUILD_VERSION_CACHE:
        return _BUILD_VERSION_CACHE
    version = None
    # Read the deployed commit hash straight from .git (no git binary needed —
    # the slim container may not have one). A fast-forward pull writes the loose ref.
    try:
        head = open("/cove-core/.git/HEAD").read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            version = open(f"/cove-core/.git/{ref}").read().strip()[:10]
        elif head:
            version = head[:10]
    except Exception:
        version = None
    if not version:
        for path in ["/cove-core/.build-version", ".build-version"]:
            try:
                with open(path) as f:
                    v = f.read().strip()
                    if v:
                        version = v
                        break
            except FileNotFoundError:
                continue
    _BUILD_VERSION_CACHE = version or _BUILD_VERSION_FALLBACK
    return _BUILD_VERSION_CACHE

BUILD_VERSION = _get_build_version()  # initial value for non-config uses


# =============================================================================
# Frontend config (THE endpoint the dynamic UI reads on boot)
# =============================================================================

@router.get("/api/config")
async def frontend_config(request: Request):
    """Config subset for the frontend — tabs, channels, accent color, operator.

    No secrets, no internal paths. Just what the UI needs to build itself.
    Called once on page load by the JS bootstrap in index.html.

    In multi-Presence mode, merges per-account preferences into features.
    """
    config = get_frontend_config()
    config["build_version"] = _get_build_version()  # re-read each time so --no-restart deploys bust cache
    config["domain"] = load_cove_config().get("domain", "")  # public Cove domain (cove.yaml) — frontend builds standard service links from it
    # Voice (jules) backend — resolved server-side so the frontend never guesses the
    # host by swapping a subdomain (#205-voice). {enabled, http, ws, same_host_port}.
    try:
        from src.config import resolve_voice_urls
        _v = resolve_voice_urls()
        config["voice"] = {"enabled": _v["enabled"], "http": _v["http"],
                           "ws": _v["ws"], "same_host_port": _v["same_host_port"]}
    except Exception:
        config["voice"] = {"enabled": False, "http": "", "ws": "", "same_host_port": ""}
    # Add tier system data so the frontend can filter tabs per-user
    config["tier"] = get_tier_info()
    config["all_tabs"] = config["tabs"]  # Full set before filtering
    config["tab_tiers"] = {k: int(v) for k, v in TAB_TIER_REQUIREMENTS.items()}
    config["tab_tier_max"] = {k: int(v) for k, v in TAB_TIER_MAX.items()}
    # Whether this is the public, agentless registry-master app. A real Cove (self-host
    # or hosted) is NOT public → its operator always has agent chat (the tier-gate is a
    # public-app construct). The chat UI gates on this so a stray tier can't hide the
    # operator's own agent.
    config["is_public_app"] = _is_public_app()

    # Personal-agent identity for a Centralized data-entry Presence — overrides the
    # container's primary agent in the chat selector + header. Resolved just below.
    _presence_agent_id = None

    # In multi mode, use per-account tier and merge feature preferences
    if env("COVE_MODE", "single") == "multi":
        try:
            from src.dashboard.routes.presence import get_current_presence
            from src.permissions import _parse_tier
            account = await get_current_presence(request)
            # Host-based subdomain routing context (Centralized): which "door" this
            # request arrived on, and whether the session matches it. Cove root and
            # single-mode are unchanged (kind="cove", match=True). Dormant until the
            # wildcard DNS/Caddy is live — handle subdomains simply don't resolve yet.
            try:
                from src.dashboard.host_context import resolve_host_context, request_host, host_match
                # load_cove_config is imported at module level — re-importing it here
                # would make it a function-local and shadow the earlier use (UnboundLocalError).
                # Each presence/manager is its OWN subdomain MC ({handle}.{cove}.{domain},
                # stuart.{cove}.{domain}); the door is selected by host, never a param.
                _cc = load_cove_config()
                _hc = resolve_host_context(request_host(request), _cc)
                # On-box door override: the Cove admin can open a manager MC (Stuart/
                # Mercer) from the box via ?as=<manager>, so it works on localhost where
                # subdomains don't resolve yet (and admin_url is blank with no domain).
                # Identity still gates data (host_match=admin); remote/family use the real
                # subdomain. Only honored for an admin + an actual manager name.
                _as = (request.query_params.get("as") or "").strip().lower()
                _force_personal = False
                if _as and account:
                    _own = (account.get("username") or "").lstrip("@").strip().lower()
                    if account.get("cove_role") == "admin":
                        _mgrs = {
                            ((_cc.get("steward_channel") or {}).get("name") or "").strip().lower(),
                            ((_cc.get("merchant_channel") or {}).get("name") or "").strip().lower(),
                        } - {""}
                        if _as in _mgrs:
                            _hc = {**_hc, "kind": "manager", "label": _as}
                        elif _as == _own:
                            # The admin opening THEIR OWN personal home on the box — the apex
                            # otherwise renders the Cove-admin surface, and their operator
                            # subdomain doesn't resolve on localhost/NAT yet (CF-90). This is
                            # their own identity + data, so it's a pure view switch: render
                            # the personal home (agent + onboarding nags), not the admin apex.
                            _hc = {**_hc, "kind": "operator", "label": _own}
                            _force_personal = True
                    elif _as == _own:
                        # A MEMBER (or any non-admin Presence) opening their own personal home
                        # via ?as=<own handle>. A member never gets the admin apex, but the
                        # bare apex still isn't their Chat — a self-onboard invitee landed on
                        # the cove surface instead of their agent. Same pure view switch as the
                        # admin case: render THEIR personal home. Manager-MC (_as in _mgrs)
                        # stays admin-only; cove_admin below stays gated to cove_role=="admin",
                        # so the founder admin apex is unaffected.
                        _hc = {**_hc, "kind": "operator", "label": _own}
                        _force_personal = True
                config["host_context"] = {
                    "kind": _hc["kind"],
                    "label": _hc["label"],
                    "match": host_match(_hc, account),
                    # Apex door + admin session => render the Cove-admin surface
                    # (presence list stub) instead of the personal home. Server-
                    # authoritative: the client never decides admin on its own.
                    # ?as=<own handle> forces the personal home (see above).
                    "cove_admin": (
                        _hc["kind"] == "cove"
                        and bool(account)
                        and (account.get("cove_role") == "admin")
                        and not _force_personal
                    ),
                }
            except Exception:
                pass
            # The Cove's display name is chosen in the onboarding wizard and saved to
            # cove.yaml. Surface the LIVE value so the header (agentName + family_name)
            # and settings show the name the operator picked — not the generator-seeded
            # agent.yaml / COVE_NAME value.
            try:
                from src.config import load_cove_config as _lcc
                # Prefer the Presence's own last_name (DB, written by finalize = the
                # Cove name the operator chose) — reliable even if the cove.yaml write
                # didn't persist. Fall back to the live cove.yaml name.
                _cn = ""
                if account and (account.get("last_name") or "").strip():
                    _cn = account["last_name"].strip()
                if not _cn:
                    _cn = (_lcc().get("name") or "").strip()
                if _cn:
                    config.setdefault("instance", {})["family_name"] = _cn
            except Exception:
                pass
            if account:
                # Override tier with per-account tier
                account_tier = _parse_tier(account.get("tier", "free"))
                config["tier"] = get_tier_info(account_tier)
                # Identity override applies ONLY to a PERSONAL MC. On a manager/admin
                # MC (stuart.{cove}) the cove-level steward identity drives the header
                # and settings — never the logged-in operator's personal agent. Stuart
                # is the Cove steward, not the person who set it up.
                _is_manager_mc = (config.get("host_context") or {}).get("kind") == "manager"
                if not _is_manager_mc:
                    # Operator/person label = THIS Presence's person, not the container's.
                    # The frontend reads config.instance.operator (nested), not top-level.
                    if account.get("display_name"):
                        config.setdefault("instance", {})["operator"] = account["display_name"]
                    # Centralized data-entry Presence → its own agent (name + id) drives
                    # the chat selector + header instead of the container's primary.
                    _ai = account.get("agent_identity") or {}
                    if _ai:
                        # This operator HAS a personal agent — the agent chat must show,
                        # regardless of tier level (tier-gating is a public-app construct).
                        config["has_personal_agent"] = True
                        _presence_agent_id = str(account["id"])
                        _pname = account.get("agent_name") or _ai.get("agent_name")
                        if _pname:
                            config["name"] = _pname
                            # The frontend derives the personal agent name from
                            # config.agents[0].name (MC.agentName) — reflect the
                            # Presence's OWN agent there instead of the container's.
                            _agent_entry = {
                                "id": _presence_agent_id,
                                "name": _pname,
                                "archetype": _ai.get("archetype", ""),
                                "emoji": _ai.get("emoji", ""),
                                "symbol_svg": "",
                            }
                            if isinstance(config.get("agents"), list) and config["agents"]:
                                config["agents"][0] = _agent_entry
                            else:
                                config["agents"] = [_agent_entry]
                # Merge per-account feature preferences
                prefs = account.get("preferences") or {}
                account_features = prefs.get("features", {})
                if account_features:
                    config["features"] = {**config["features"], **account_features}
                # Admin check — is this Presence in the admin_ids list?
                from src.config import get_admin_ids
                admin_ids = get_admin_ids()
                config["is_cove_admin"] = str(account["id"]) in admin_ids
        except Exception as e:
            import logging
            logging.error(f"[CONFIG] Per-account override failed: {type(e).__name__}: {e}")
            import traceback
            logging.error(f"[CONFIG] Traceback: {traceback.format_exc()}")

    # Manager channels — inject Day/Deep channels + chat_agents for operators
    # Supports steward (Stuart) and merchant (Mercer) manager types
    try:
        sc = get_steward_channel_config()
        mc = get_merchant_channel_config()

        if sc or mc:
            is_operator = False
            if env("COVE_MODE", "single") == "multi":
                try:
                    from src.dashboard.routes.presence import get_current_presence
                    account = await get_current_presence(request)
                    if account and account.get("cove_role") == "admin":
                        is_operator = True
                except Exception:
                    pass
            else:
                is_operator = True

            host_agent_id = _presence_agent_id or get_primary_agent_id()

            # ── Steward channel injection ──
            if sc and (is_operator or not sc.get("operator_only", True)):
                steward_name = sc.get("name", "Stuart").lower()
                steward_agent_id = sc.get("agent_id", "stuart")

                # Skip injection if this agent IS the steward
                if host_agent_id != steward_agent_id:
                    sc_channels = sc.get("channels", {})
                    for ch_key, ch_def in sc_channels.items():
                        channel_name = f"{steward_name}-{ch_key}"
                        config["channels"][channel_name] = {
                            "description": ch_def.get("description", ""),
                            "is_steward": True,
                            "steward_agent_id": steward_agent_id,
                            "rotation_threshold": ch_def.get("rotation_threshold", 40),
                        }

                    config["steward_channel"] = {
                        "name": steward_name,
                        "agent_id": steward_agent_id,
                        "channels": list(sc_channels.keys()),
                    }

            # ── Merchant channel injection ──
            if mc and (is_operator or not mc.get("operator_only", True)):
                merchant_name = mc.get("name", "Mercer").lower()
                merchant_agent_id = mc.get("agent_id", "mercer")

                # Skip injection if this agent IS the merchant
                if host_agent_id != merchant_agent_id:
                    mc_channels = mc.get("channels", {})
                    for ch_key, ch_def in mc_channels.items():
                        channel_name = f"{merchant_name}-{ch_key}"
                        config["channels"][channel_name] = {
                            "description": ch_def.get("description", ""),
                            "is_merchant": True,
                            "merchant_agent_id": merchant_agent_id,
                            "rotation_threshold": ch_def.get("rotation_threshold", 40),
                        }

                    config["merchant_channel"] = {
                        "name": merchant_name,
                        "agent_id": merchant_agent_id,
                        "channels": list(mc_channels.keys()),
                    }

            # ── Build chat_agents list — host + all injected managers ──
            host_channels = [k for k, v in config["channels"].items()
                             if not v.get("is_steward") and not v.get("is_merchant")]
            steward_channels = [k for k, v in config["channels"].items()
                                if v.get("is_steward")]
            merchant_channels = [k for k, v in config["channels"].items()
                                 if v.get("is_merchant")]

            chat_agents = [
                {
                    "id": host_agent_id,
                    "name": config.get("name", host_agent_id.capitalize()),
                    "channels": host_channels,
                    "is_steward": False,
                    "is_merchant": False,
                },
            ]

            if steward_channels and sc:
                chat_agents.append({
                    "id": sc.get("agent_id", "stuart"),
                    "name": sc.get("name", "Stuart"),
                    "channels": steward_channels,
                    "is_steward": True,
                    "is_merchant": False,
                    "archetype": sc.get("archetype", ""),
                    "admin_url": sc.get("admin_url", ""),
                    "emoji": sc.get("emoji", ""),
                })

            if merchant_channels and mc:
                chat_agents.append({
                    "id": mc.get("agent_id", "mercer"),
                    "name": mc.get("name", "Mercer"),
                    "channels": merchant_channels,
                    "is_steward": False,
                    "is_merchant": True,
                    "archetype": mc.get("archetype", ""),
                    "admin_url": mc.get("admin_url", ""),
                    "emoji": mc.get("emoji", ""),
                })

            if len(chat_agents) > 1:
                config["chat_agents"] = chat_agents

    except Exception as e:
        import logging
        logging.error(f"[CONFIG] Manager channel injection failed: {e}")

    # Add agent tuning state (used by sweep to check if Presence has tuned today)
    try:
        if _is_public_app():
            raise _SkipAgentState
        from src.memory.database import get_db
        agent_id = _presence_agent_id or get_primary_agent_id()
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT last_tuned_at, last_frequency, last_echo_num FROM agent_state WHERE agent_id = %s",
                (agent_id,)
            )
            row = await result.fetchone()
            if row:
                r = dict(row)
                config["agent"] = {
                    "last_tuned_at": str(r["last_tuned_at"]) if r.get("last_tuned_at") else None,
                    "last_frequency": r.get("last_frequency"),
                    "last_echo_num": r.get("last_echo_num"),
                }
    except Exception:
        pass

    return JSONResponse(
        content=config,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# =============================================================================
# Status and health
# =============================================================================

@router.get("/health")
async def health():
    agent_id = get_primary_agent_id()
    return {"status": "ok", "agent": agent_id}


@router.get("/api/status")
async def get_status():
    """Agent status — tuning state, connections, model health."""
    try:
        from src.memory.database import get_db
        instance = get_instance()
        from src.utils.time_utils import app_tz
        tz = app_tz()
        now = datetime.now(tz)
        agent_id = get_primary_agent_id()
        dry_run = env_bool("LTP_DRY_RUN", "false")

        agent_state = {}
        latest_echo = None

        # Public app has no per-operator agent — skip agent_state/echoes reads so
        # we never leak another presence's agent data. Return empty agent fields.
        if not _is_public_app():
            async with get_db() as conn:
                result = await conn.execute(
                    "SELECT * FROM agent_state WHERE agent_id = %s", (agent_id,)
                )
                row = await result.fetchone()
                if row:
                    r = dict(row)
                    agent_state = {
                        "agent_id": r["agent_id"],
                        "name": r["display_name"],
                        "archetype": r["archetype"],
                        "status": r["status"],
                        "last_frequency": r.get("last_frequency"),
                        "last_echo_num": r.get("last_echo_num", 0),
                        "last_tuned_at": str(r["last_tuned_at"]) if r.get("last_tuned_at") else None,
                    }

                result = await conn.execute(
                    "SELECT echo_num, frequency, echo_text, tuned_at "
                    "FROM echoes WHERE agent_id = %s ORDER BY echo_num DESC LIMIT 1",
                    (agent_id,),
                )
                row = await result.fetchone()
                if row:
                    r = dict(row)
                    latest_echo = {
                        "echo_num": r["echo_num"],
                        "frequency": r["frequency"],
                        "echo_text": r["echo_text"],
                        "tuned_at": str(r["tuned_at"]),
                    }

        # Nextcloud connection check
        nc_ok = False
        try:
            import httpx
            nc_url = env("NEXTCLOUD_URL")
            if nc_url:
                # Try per-user env vars first (single mode), then admin (multi mode)
                nc_user = env("NEXTCLOUD_USER")
                nc_pass = env("NEXTCLOUD_PASSWORD")
                if not nc_pass:
                    nc_user = env("NEXTCLOUD_ADMIN_USER", "admin")
                    nc_pass = env("NEXTCLOUD_ADMIN_PASSWORD")
                if nc_pass:
                    async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=5) as client:
                        r = await client.get(f"{nc_url}/remote.php/dav/files/{nc_user}/")
                        nc_ok = r.status_code in (200, 207)
        except Exception:
            pass

        return {
            "status": "online",
            "timestamp": now.isoformat(),
            "agent": agent_state,
            "latest_echo": latest_echo,
            "nextcloud": {"connected": nc_ok},
            "dry_run": dry_run,
            "operator": instance.get("operator", "Operator"),
        }

    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


# =============================================================================
# PWA manifest (dynamic — Cove name, single-sourced)
# =============================================================================

async def resolve_cove_name() -> str:
    """Single source of truth for the Cove's display name.

    The founding operator's account.last_name (the name chosen at wizard finalize)
    is authoritative — the same resolver /api/family, /api/team/roster and the header
    use. Falls back to cove.yaml's name, then the agent.yaml family_name/instance name,
    then 'Cove'. Anything that SHOWS the Cove name (PWA title, team last names) should
    use this so a stale generator seed ('New Cove') in one config file can't leak.
    """
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Skip the provisioner's "New Cove" placeholder — pre-finalize installs
            # seeded it into accounts.last_name (legacy rows may still hold it), and
            # it must never win over the guarded fallbacks (CF-89's DB-side sibling).
            r = await conn.execute(
                "SELECT last_name FROM accounts WHERE COALESCE(last_name,'') <> '' "
                "AND LOWER(TRIM(last_name)) <> 'new cove' "
                "ORDER BY created_at LIMIT 1")
            row = await r.fetchone()
        if row and (row.get("last_name") or "").strip():
            return row["last_name"].strip()
    except Exception:
        pass
    from src.config import load_cove_config
    cn = (load_cove_config().get("name") or "").strip()
    if cn and cn.lower() != "new cove":
        return cn
    inst = get_instance()
    fam = (inst.get("family_name") or inst.get("name") or "").strip()
    if fam.lower() == "new cove":
        fam = ""
    return fam or "Cove"


async def resolve_host_pwa_name(request) -> str:
    """Per-subdomain label for the PWA/home-screen shortcut, so each door gets its
    own name (stuart.{cove} -> 'Stuart', alex.{cove} -> that presence's agent, the
    Cove root -> the Cove name). The subdomain alone selects the door, so this needs
    no session. Falls back to the Cove name for the root / unknown hosts.
    """
    try:
        from src.dashboard.host_context import resolve_host_context, request_host
        from src.config import load_cove_config
        cove = load_cove_config()
        hc = resolve_host_context(request_host(request), cove)
        kind = hc.get("kind")
        label = (hc.get("label") or "").strip()

        if kind == "manager":
            # Use the manager's proper-cased name from cove.yaml (Stuart / Mercer).
            for ch in ("steward_channel", "merchant_channel"):
                nm = ((cove.get(ch) or {}).get("name") or "").strip()
                if nm and nm.lower() == label.lower():
                    return nm
            return label.capitalize() if label else await resolve_cove_name()

        if kind == "handle" and label:
            # Presence door — the agent that lives behind it (the presence's own agent).
            try:
                from src.memory.database import get_db
                async with get_db() as conn:
                    r = await conn.execute(
                        "SELECT agent_name, display_name FROM accounts "
                        "WHERE LOWER(username) = LOWER(%s) AND active = TRUE LIMIT 1",
                        (label,))
                    row = await r.fetchone()
                if row:
                    nm = (row.get("agent_name") or row.get("display_name") or "").strip()
                    if nm:
                        return nm
            except Exception:
                pass
            return label.capitalize()

        if kind == "haven":
            return (await resolve_cove_name()) + " Haven"
    except Exception:
        pass
    return await resolve_cove_name()


@router.get("/static/manifest.json")
async def pwa_manifest(request: Request):
    """Serve the PWA manifest with a per-subdomain home-screen label.

    Host-aware (resolve_host_pwa_name): stuart.{cove} -> 'Stuart MC', a presence door
    -> that presence's agent (e.g. 'Atlas MC'), the Cove root -> '<Cove> MC'. All names
    trace to the single source (operator last_name → cove.yaml) so the stale agent.yaml
    'New Cove' seed can never leak.
    """
    name = await resolve_host_pwa_name(request)
    return JSONResponse({
        "name": f"{name} Mission Control",
        "short_name": f"{name} MC",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0f",
        "theme_color": "#0a0a0f",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


# =============================================================================
# Settings endpoints
# =============================================================================

@router.get("/api/settings")
async def settings_overview():
    """General settings overview — instance info for team.js and settings.js."""
    instance = get_instance()
    from src.config import load_cove_config
    _cove_name = (load_cove_config().get("name") or "").strip()
    return {
        "settings": {
            "family_name": _cove_name or instance.get("family_name", ""),
            "name": instance.get("name", "Agent"),
            "type": instance.get("type", "personal"),
            "operator": instance.get("operator", "Operator"),
            "timezone": instance.get("timezone", "America/New_York"),
        },
    }


@router.get("/api/settings/system")
async def settings_system():
    """System configuration for the settings tab."""
    defaults = get_model_defaults()
    model_conf = defaults.get("model", {}) if isinstance(defaults.get("model"), dict) else {}
    ltp_conf = get_ltp_config()

    return {
        "model": {
            "primary": model_conf.get("primary", env("PRIMARY_MODEL", "unknown")),
            "fallback": model_conf.get("fallback", env("FALLBACK_MODEL", "unknown")),
        },
        "provider": defaults.get("provider", "openrouter"),
        "timeout_seconds": defaults.get("timeout_seconds", 60),
        "ltp": {
            "source": ltp_conf.get("source", env("LTP_SOURCE", "unknown")),
            "delivery": ltp_conf.get("delivery", "git-pull"),
            "schedule": ltp_conf.get("schedule", "06:30 ET"),
            "timezone": ltp_conf.get("timezone", "America/New_York"),
        },
        "env": {
            "LTP_DRY_RUN": env("LTP_DRY_RUN", "false"),
            "ENVIRONMENT": env("ENVIRONMENT", "production"),
        },
    }


@router.get("/api/settings/nextcloud")
async def settings_nextcloud():
    """Nextcloud connection info."""
    nc_url = env("NEXTCLOUD_URL")
    nc_user = env("NEXTCLOUD_USER") or env("NEXTCLOUD_ADMIN_USER")
    nc_pass = env("NEXTCLOUD_PASSWORD") or env("NEXTCLOUD_ADMIN_PASSWORD")
    cove_mode = env("COVE_MODE", "single")
    return {
        "url": nc_url or "not configured",
        "username": nc_user or "not configured",
        "password": nc_pass or "",
        "mode": "per-user" if cove_mode == "multi" else "shared",
        "has_password": bool(nc_pass),
        "caldav_status": "configured" if nc_pass else "not configured",
        "webdav_status": "configured" if nc_pass else "not configured",
    }


@router.get("/api/settings/matrix")
async def settings_matrix():
    """Connect (Matrix) homeserver info + the master registration secret — the
    NC-block pattern (jules 1653): the steward can wire external clients/tools
    without docker exec."""
    server_name = env("MATRIX_SERVER_NAME")
    hub_url = env("MATRIX_HUB_URL")
    public_url = env("MATRIX_PUBLIC_URL")
    reg_secret = env("MATRIX_REG_SECRET")
    return {
        "enabled": bool(server_name or hub_url),
        "server_name": server_name or "not configured",
        "internal_url": hub_url or "not configured",
        "public_url": public_url or "",
        "reg_secret": reg_secret or "",
        "has_secret": bool(reg_secret),
    }


@router.post("/api/settings/nextcloud/test")
async def test_nextcloud():
    """Live test of Nextcloud connectivity."""
    import httpx
    nc_url = env("NEXTCLOUD_URL")
    nc_user = env("NEXTCLOUD_USER") or env("NEXTCLOUD_ADMIN_USER")
    nc_pass = env("NEXTCLOUD_PASSWORD") or env("NEXTCLOUD_ADMIN_PASSWORD")
    if not (nc_url and nc_user and nc_pass):
        return {"ok": False, "error": "Nextcloud not configured"}
    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=8) as client:
            r = await client.get(f"{nc_url}/remote.php/dav/files/{nc_user}/")
        if r.status_code in (200, 207):
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Model registry endpoints
# =============================================================================

@router.get("/api/models")
async def get_models():
    """Return the full models registry for UI dropdowns."""
    models = load_models_registry()
    return {"models": models}


@router.post("/api/models")
async def save_models(data: dict):
    """Save updated models registry from Settings textarea.

    Expects: {"yaml_content": "..."} with raw YAML string,
    or {"models": [...]} with parsed model list.
    """
    import yaml as _yaml
    try:
        if "yaml_content" in data:
            parsed = _yaml.safe_load(data["yaml_content"]) or {}
            models = parsed.get("models", [])
        elif "models" in data:
            models = data["models"]
        else:
            return JSONResponse({"error": "Expected 'yaml_content' or 'models' field"}, status_code=400)

        # Validate: each model needs at minimum an id and provider
        for m in models:
            if not m.get("id") or not m.get("provider"):
                return JSONResponse(
                    {"error": f"Model entry missing 'id' or 'provider': {m}"},
                    status_code=400,
                )

        save_models_registry(models)
        return {"ok": True, "count": len(models)}
    except _yaml.YAMLError as e:
        return JSONResponse({"error": f"Invalid YAML: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/system/health")
async def system_health():
    """Model chain + DB health check."""
    results = {}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute("SELECT 1")
        results["db"] = "ok"
    except Exception as e:
        results["db"] = f"error: {e}"

    try:
        import httpx
        nc_url = env("NEXTCLOUD_URL")
        nc_user = env("NEXTCLOUD_USER")
        nc_pass = env("NEXTCLOUD_PASSWORD")
        if nc_url and nc_pass:
            async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=5) as client:
                r = await client.get(f"{nc_url}/remote.php/dav/files/{nc_user}/")
            results["nextcloud"] = "ok" if r.status_code in (200, 207) else f"http {r.status_code}"
        else:
            results["nextcloud"] = "not configured"
    except Exception as e:
        results["nextcloud"] = f"error: {e}"

    return results
