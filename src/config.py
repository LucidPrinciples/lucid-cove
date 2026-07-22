"""
Central config loader — reads agent.yaml once, provides to all modules.

agent.yaml is THE single config file that defines an agent instance:
identity, channels, tools, dashboard tabs, accent color, operator name.

All shared modules read from here instead of hardcoding values.
"""

import os
from src.env import env
import yaml
from pathlib import Path
from functools import lru_cache
from typing import Optional

CONFIG_DIR = Path(__file__).parent.parent / "config"
# Fallback: cove-core's config dir (shared base, read-only mount in container).
# Models registry lives here — overlay config may not include it.
CORE_CONFIG_DIR = Path("/cove-core/config")

# Cascade config paths (Haven → Cove → Presence)
# In container: /vault/ for haven/cove config, /app/config/ for agent-level
# These are populated if the config files exist.
_HAVEN_CONFIG_PATH = Path("/vault/haven.yaml")  # Syncthing-synced from operator
_COVE_CONFIG_PATH = CONFIG_DIR / "cove.yaml"     # Cove-level config alongside agent.yaml


@lru_cache()
def load_config() -> dict:
    """Load and cache the full agent.yaml config."""
    config_path = CONFIG_DIR / "agent.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"agent.yaml not found at {config_path}. "
            "Each agent instance must have a config/agent.yaml."
        )
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_instance() -> dict:
    """Instance metadata: name, type, port, operator, timezone, accent_color."""
    return load_config().get("instance", {})


def get_operator_name() -> str:
    """The operator's display name (e.g., 'Alex')."""
    return get_instance().get("operator", "Operator")


def get_channels() -> dict:
    """Channel definitions keyed by name (e.g., {'day': {...}, 'deep': {...}}).
    Handles configs where channels is a list (shared containers) by returning empty dict."""
    raw = load_config().get("channels", {})
    if isinstance(raw, list):
        return {}
    return raw


def get_default_channel() -> str:
    """First channel listed = default (Day loads first)."""
    channels = get_channels()
    return next(iter(channels)) if channels else "day"


def get_channel_names() -> list[str]:
    """List of channel names in config order."""
    return list(get_channels().keys())


def get_tabs() -> list[dict]:
    """Dashboard tab definitions from config."""
    return load_config().get("tabs", [])


# ── Nextcloud config (centralized) ────────────────────────────────
# All routes import from here instead of calling os.getenv() independently.
# Env var naming: NEXTCLOUD_USER/PASSWORD are set on every container.
# On the steward (Stuart), this user IS the NC admin for the Cove.
# NEXTCLOUD_ADMIN_USER/PASSWORD are aliases used by some routes —
# fall back to NEXTCLOUD_USER/PASSWORD if the ADMIN variants aren't set.

def get_nc_url() -> str:
    """Internal NC URL for API/WebDAV calls."""
    return env("NEXTCLOUD_URL")


def get_nc_public_url() -> str:
    """Public-facing NC URL for browser links."""
    return env("NEXTCLOUD_PUBLIC_URL")


def get_nc_admin_user() -> str:
    """NC admin username — for operations on other users' files.
    Falls back to NEXTCLOUD_USER (which IS admin on the steward)."""
    return env("NEXTCLOUD_ADMIN_USER", env("NEXTCLOUD_USER"))


def get_nc_admin_password() -> str:
    """NC admin password. Falls back to NEXTCLOUD_PASSWORD."""
    return env("NEXTCLOUD_ADMIN_PASSWORD", env("NEXTCLOUD_PASSWORD"))


def get_nc_user() -> str:
    """This container's own NC username."""
    return env("NEXTCLOUD_USER")


def get_nc_password() -> str:
    """This container's own NC password."""
    return env("NEXTCLOUD_PASSWORD")


def get_agents() -> list[dict]:
    """Agent definitions (single agent for personal, team for admin).

    Merges agents from config (read-only mount) with any provisioned
    agents in /app/data/provisioned/agents.yaml (writable volume).
    Provisioned agents are available immediately after creation.
    """
    agents = list(load_config().get("agents", []))
    # Merge provisioned agents (if any)
    try:
        prov_path = Path("/app/data/provisioned/agents.yaml")
        if prov_path.exists():
            with open(prov_path) as f:
                prov = yaml.safe_load(f) or {}
            existing_ids = {a.get("id") for a in agents}
            for agent in prov.get("agents", []):
                if agent.get("id") not in existing_ids:
                    agents.append(agent)
    except Exception:
        pass
    return agents


def get_primary_agent_id() -> str:
    """The first (or only) agent's ID."""
    agents = get_agents()
    return agents[0]["id"] if agents else "unknown"


# Modules that every multi-presence Cove app default must expose to presences.
# Provision writes these into NEW agent.yaml files; existing Coves keep their
# pre-upgrade list on disk. Append here so a git pull + restart is enough —
# same upgrade class as universal skill/image tools (no hand-edit of agent.yaml).
_PRESENCE_DEFAULT_MODULES = (
    "tools.project_tools",  # #PRJ1 — personal projects/tasks for every presence
    "tools.links_tools",    # #LNK2 — Action Board Links create/edit for every presence
)


def get_tool_modules() -> list[str]:
    """Tool module paths to import (e.g., ['tools.calendar_tools']).

    Starts from agent.yaml tools.modules, then ensures presence-default modules
    that shipped after this Cove was provisioned are still bound on upgrade.
    """
    tools_config = load_config().get("tools", {})
    modules = list(tools_config.get("modules") or [])
    for m in _PRESENCE_DEFAULT_MODULES:
        if m not in modules:
            modules.append(m)
    return modules


def get_approval_tiers() -> Optional[dict]:
    """Approval tier config, or None if disabled.

    Returns: {'auto': [...], 'notify': [...], 'block': [...]} or None
    """
    tools_config = load_config().get("tools", {})
    return tools_config.get("approval_tiers")


def get_format_rules() -> str:
    """Format rules template (injected into all channels)."""
    return load_config().get("format_rules", "")


def get_sites_path() -> str:
    """NextCloud-relative path to THIS acting scope's sites folder.

    Same default folder name on admin and presence NC (#TIER1): isolation is
    which NC user / credentials, not a different path string. Resolution order:
      1. env SITES_NC_PATH       (set per Presence by the provisioner)
      2. agent.yaml  sites.nc_path
      3. default "AgentSkills/Sites"
    """
    nc_path = (env("SITES_NC_PATH") or "").strip().strip("/")
    if nc_path:
        return nc_path
    return load_config().get("sites", {}).get("nc_path", "AgentSkills/Sites")


def get_routes() -> list[dict]:
    """Agent-specific dashboard route modules to register."""
    return load_config().get("routes", [])


def get_model_defaults() -> dict:
    """Model configuration defaults."""
    return load_config().get("defaults", {})


def get_ltp_config() -> dict:
    """LTP tuning configuration."""
    return load_config().get("ltp", {})


def get_agent_config(agent_id: str) -> Optional[dict]:
    """Get a specific agent's config by ID."""
    for agent in get_agents():
        if agent.get("id") == agent_id:
            return agent
    return None


def get_agent_model_assignment(agent_id: str, slot: str = None) -> dict:
    """Resolve an agent's model assignment (primary + fallback registry IDs).

    Cascade, most specific wins:
      1. Cove-level team_models[agent_id] (the Stuart-managed model layer), incl.
         per-task `slots` (forward-compatible — e.g. Arthur's R&D tool requesting a
         specialized fine-tune via `slot`).
      2. agent.yaml per-agent model_primary / model_fallback.
      3. instance-level defaults (agent.yaml defaults.model).
    The agent's identity + memory never change with the model — only the substrate.
    """
    # 0. DB override — the Team-page model manager (highest priority, served from a
    #    boot-loaded cache so this stays sync + hot-path-cheap). No row → falls through
    #    to the YAML cascade below, so an empty table = unchanged behavior.
    try:
        from src.models.assignments import cached_assignment
        _db = cached_assignment(agent_id, slot)
        if _db and (_db.get("primary") or _db.get("fallback")):
            return {"primary": _db.get("primary"), "fallback": _db.get("fallback")}
    except Exception:
        pass
    # 1. Cove team_models — the Stuart-level model management layer.
    try:
        tm = (load_cove_config().get("team_models") or {}).get(agent_id) or {}
        if slot and isinstance(tm.get("slots"), dict) and tm["slots"].get(slot):
            s = tm["slots"][slot]
            if s.get("primary") or s.get("fallback"):
                return {"primary": s.get("primary"), "fallback": s.get("fallback")}
        if tm.get("primary") or tm.get("fallback"):
            return {"primary": tm.get("primary"), "fallback": tm.get("fallback")}
    except Exception:
        pass
    # 2. agent.yaml per-agent override
    agent = get_agent_config(agent_id)
    if agent and (agent.get("model_primary") or agent.get("model_fallback")):
        return {
            "primary": agent.get("model_primary"),
            "fallback": agent.get("model_fallback"),
        }
    # 3. instance-level defaults
    defaults = get_model_defaults()
    model = defaults.get("model", {}) if isinstance(defaults.get("model"), dict) else {}
    return {
        "primary": model.get("primary"),
        "fallback": model.get("fallback"),
    }


def list_build_team_agents() -> list[dict]:
    """The Cove's build-team agents for model management. Returns [{id, name, role}].

    Handles both cove.yaml `team` shapes:
      - LIST of {name, id, role, shared}  (instance format — richest)
      - DICT {admin_agent, shared_agents: [ids]}  (example/default format)
    """
    out, seen = [], set()

    def add(aid, name, role):
        if aid and aid not in seen:
            seen.add(aid)
            clean = name or aid.rsplit("-", 1)[0].title()
            out.append({"id": aid, "name": clean, "role": role or "team"})

    try:
        team = (load_cove_config() or {}).get("team")
        if isinstance(team, list):
            for t in team:
                if isinstance(t, dict):
                    add(t.get("id"), t.get("name"), t.get("role"))
        elif isinstance(team, dict):
            sc = get_steward_channel_config() or {}
            add(sc.get("agent_id") or team.get("admin_agent"), sc.get("name"), "steward")
            mc = get_merchant_channel_config() or {}
            add(mc.get("agent_id"), mc.get("name"), "merchant")
            for aid in (team.get("shared_agents") or []):
                add(aid, None, "team")
    except Exception:
        pass

    if not out:
        # Last resort — at least the two managers.
        sc = get_steward_channel_config() or {}
        add(sc.get("agent_id"), sc.get("name"), "steward")
        mc = get_merchant_channel_config() or {}
        add(mc.get("agent_id"), mc.get("name"), "merchant")
    return out


def set_team_model(agent_id: str, primary: str = None, fallback: str = None) -> bool:
    """Persist a build-team agent's model assignment into cove.yaml `team_models`
    (the Stuart-level layer). Merges, writes via save_cove_config, clears cache."""
    cove = load_cove_config()
    team_models = dict(cove.get("team_models") or {})
    entry = dict(team_models.get(agent_id) or {})
    if primary is not None:
        entry["primary"] = primary
    if fallback is not None:
        entry["fallback"] = fallback
    team_models[agent_id] = entry
    return save_cove_config({"team_models": team_models})


# ── Compute backends (where heavy work runs) ─────────────────────────────────
# Pluggable offramps, set by the Admin Presence, with light defaults (no bloat):
#   llm:       cloud (BYOK API, default) | local (host Ollama) | external (URL)
#   voice:     local (CPU whisper in-Cove, default) | external (URL) | off
#   video_asr: cloud (BYOK API, default) | external (URL) | local (local GPU)
# An `external` URL is how a GPU-less Cove borrows another box's GPU — e.g. the P620
# mesh, or the "schedule a sim" offramp. Resolves cove.yaml `compute:` over the default.
COMPUTE_DEFAULTS = {
    "llm":       {"mode": "cloud", "url": ""},
    "voice":     {"mode": "local", "url": ""},
    "video_asr": {"mode": "cloud", "url": ""},
}
COMPUTE_MODES = {
    "llm":       ("cloud", "local", "external"),
    "voice":     ("local", "external", "off"),
    "video_asr": ("cloud", "local", "external"),
}


def get_compute_config() -> dict:
    """Effective compute-backend config: cove.yaml `compute:` merged over the defaults.
    Every section returns {'mode': ..., 'url': ...}."""
    cfg = load_cove_config().get("compute") or {}
    out = {}
    for k, d in COMPUTE_DEFAULTS.items():
        v = cfg.get(k) if isinstance(cfg.get(k), dict) else {}
        out[k] = {"mode": (v.get("mode") or d["mode"]), "url": (v.get("url") or d["url"]),
                  # token = a GPU-rent grant token (video_asr external). Internal use only
                  # (the transcribe call) — the read API redacts it. Empty when unset.
                  "token": (v.get("token") or "")}
    return out


def set_compute_config(section: str, mode: str = None, url: str = None, token: str = None) -> bool:
    """Persist one compute section (llm|voice|video_asr) into cove.yaml `compute:`.
    Validates mode against COMPUTE_MODES. Admin Presence only (enforced at the route).
    `token` (video_asr external) is the GPU-rent grant token; "" clears it."""
    if section not in COMPUTE_DEFAULTS:
        return False
    if mode is not None and mode not in COMPUTE_MODES[section]:
        return False
    cove = load_cove_config()
    compute = dict(cove.get("compute") or {})
    entry = dict(compute.get(section) or {})
    if mode is not None:
        entry["mode"] = mode
        # A rent token only applies to the 'external' backend (a borrowed GPU).
        # Switching to any other mode must drop a stale token, otherwise the config
        # lingers in a confusing mode:cloud + has_token:true state (the token is
        # ignored but reads as if a GPU is still wired). An explicit token in the
        # same call (external) is re-applied below and wins.
        if mode != "external":
            entry.pop("token", None)
    if url is not None:
        entry["url"] = url.strip()
    if token is not None:
        t = token.strip()
        if t:
            entry["token"] = t
        else:
            entry.pop("token", None)
    compute[section] = entry
    return save_cove_config({"compute": compute})


def resolve_voice_urls() -> dict:
    """Single source of truth for the voice (jules) backend URLs.

    Used by jules.py (serve page + transcribe proxy) AND the frontend /api/config
    so the browser never has to guess the voice host by swapping a subdomain (which
    breaks on mesh/standalone Coves that have no voice.{domain}). Resolves the Admin
    Presence `compute.voice` setting:
      - mode 'off'       -> disabled (both empty).
      - mode 'external'  -> the configured URL (borrow another box's voice/GPU, e.g. P620).
      - mode 'local'     -> a pipecat on this Cove's host; public derived from the Cove
                            domain (https/wss voice.{domain}) when a domain is set.
    Explicit VOICE_PUBLIC_URL / VOICE_INTERNAL_URL envs still win (back-compat).

    Returns {enabled, http, ws, internal, same_host_port}:
      http           — browser-facing https origin for TTS (e.g. https://voice.{domain})
      ws             — browser-facing wss origin for realtime STT
      internal       — server-side http origin for the transcribe proxy
      same_host_port — set for a domainless local Cove (mesh/standalone): the browser
                       builds the voice URL from its own hostname + this published port,
                       since the public hostname isn't known server-side.
    """
    try:
        voice = get_compute_config().get("voice", {})
    except Exception:
        voice = {}
    mode = (voice.get("mode") or "local").strip()
    url = (voice.get("url") or "").strip()
    internal_default = env("VOICE_INTERNAL_URL", "http://host.docker.internal:8300")
    voice_port = env("VOICE_PORT", "").strip()

    def _split(u: str):
        """Given any http(s)/ws(s) origin, return (https/http origin, wss/ws origin)."""
        u = u.rstrip("/")
        if u.startswith("https://"):
            return u, "wss://" + u[len("https://"):]
        if u.startswith("http://"):
            return u, "ws://" + u[len("http://"):]
        if u.startswith("wss://"):
            return "https://" + u[len("wss://"):], u
        if u.startswith("ws://"):
            return "http://" + u[len("ws://"):], u
        return u, u

    if mode == "off":
        return {"enabled": False, "http": "", "ws": "", "internal": "", "same_host_port": ""}

    if mode == "external" and url:
        http, ws = _split(url)
        return {"enabled": True, "http": http, "ws": ws,
                "internal": url.rstrip("/"), "same_host_port": ""}

    # local (default)
    explicit_pub = env("VOICE_PUBLIC_URL", "").strip()
    if explicit_pub:
        http, ws = _split(explicit_pub)
        return {"enabled": True, "http": http, "ws": ws,
                "internal": internal_default, "same_host_port": ""}
    try:
        domain = (load_cove_config().get("domain") or "").strip()
    except Exception:
        domain = ""
    if domain:
        # Carry the local published port too, even with a domain set: when the operator
        # views the Cove on the box itself (http://localhost) the browser uses the local
        # voice port, so setting a public address never breaks voice locally. On the real
        # domain (https) the frontend uses voice.{domain}.
        return {"enabled": True, "http": f"https://voice.{domain}",
                "ws": f"wss://voice.{domain}", "internal": internal_default,
                "same_host_port": voice_port}
    if voice_port:
        # Domainless local Cove (mesh/standalone): voice runs on this host's published
        # port. The browser fills in its own hostname; we only know the port here.
        return {"enabled": True, "http": "", "ws": "", "internal": internal_default,
                "same_host_port": voice_port}
    # No domain and no published port known -> voice disabled (set compute.voice.url).
    return {"enabled": False, "http": "", "ws": "", "internal": "", "same_host_port": ""}


@lru_cache()
def load_models_registry() -> list[dict]:
    """Load the models registry from config/models.yaml.

    Checks overlay config first (/app/config/), then falls back to
    cove-core's shared config (/cove-core/config/). This is
    necessary because /app/config is a bind mount to the agent's overlay
    config dir, which may not include models.yaml.
    """
    models_path = CONFIG_DIR / "models.yaml"
    if not models_path.exists() and CORE_CONFIG_DIR.exists():
        models_path = CORE_CONFIG_DIR / "models.yaml"
    if not models_path.exists():
        return []
    with open(models_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("models", [])


def get_model_from_registry(model_id: str) -> Optional[dict]:
    """Look up a model definition by its registry ID."""
    for model in load_models_registry():
        if model.get("id") == model_id:
            return model
    return None


def save_models_registry(models: list[dict]) -> None:
    """Write updated models registry to config/models.yaml."""
    models_path = CONFIG_DIR / "models.yaml"
    with open(models_path, "w") as f:
        yaml.dump({"models": models}, f, default_flow_style=False, sort_keys=False)
    # Clear the cache so next read picks up changes
    load_models_registry.cache_clear()


def _cloud_public_url() -> str:
    """Browser URL for this Cove's Nextcloud — derived from the LIVE Cove domain
    (cloud.{domain}) so it tracks a domain set at runtime, exactly like admin_url and
    voice. Falls back to the baked NEXTCLOUD_PUBLIC_URL env (localhost for a domainless
    Cove) when no domain is set."""
    try:
        dom = (load_cove_config().get("domain") or "").strip().lstrip("*").lstrip(".")
    except Exception:
        dom = ""
    return f"https://cloud.{dom}" if dom else env("NEXTCLOUD_PUBLIC_URL")


def get_frontend_config() -> dict:
    """Config subset safe to expose to the frontend via /api/config.

    No secrets, no internal paths — just what the UI needs to build itself.
    """
    instance = get_instance()
    channels = get_channels()
    tabs = get_tabs()
    agents = get_agents()

    # ── Guaranteed cove-core tabs ──────────────────────────────────────
    # Tune and Playlists are standard at every tier — part of the original
    # Tuner free experience. Overlays don't need to declare them.
    # If an overlay includes them, its placement is respected.
    # If not, cove-core appends them so panels/scripts always exist.
    _CORE_TABS = [
        {"id": "tune", "label": "Tune", "script": "tune-flow"},
        {"id": "playlists", "label": "Playlists", "script": "playlists"},
    ]
    existing_tab_ids = {t.get("id") or t for t in tabs}
    for ct in _CORE_TABS:
        if ct["id"] not in existing_tab_ids:
            tabs.append(ct)

    # Connect (the Matrix layer + Market, #137) is standard in every Cove's Chat
    # tab. Guarantee its script loads even for instances whose stored tabs predate
    # it — cove-core injects it so the button appears in every Cove, not only the
    # configs that happen to list it (this is why Clearfield showed no Connect).
    for _t in tabs:
        if isinstance(_t, dict) and _t.get("id") == "chat":
            _scr = _t.get("scripts") or ([_t["script"]] if _t.get("script") else [])
            if "connect" not in _scr:
                _scr.append("connect")
                _t["scripts"] = _scr
                _t.pop("script", None)
            break

    return {
        "instance": {
            "name": instance.get("name", "Agent"),
            "type": instance.get("type", "personal"),
            "operator": instance.get("operator", "Operator"),
            "operator_handle": instance.get("operator_handle", ""),
            "family_name": instance.get("family_name", ""),
            "accent_color": instance.get("accent_color", "#4a9eff"),
            "timezone": instance.get("timezone", "America/New_York"),
        },
        "channels": {
            name: {
                "description": ch.get("description", ""),
            }
            for name, ch in channels.items()
        },
        "default_channel": get_default_channel(),
        "tabs": tabs,
        "nextcloud_public_url": _cloud_public_url(),
        "features": get_feature_flags(),
        "agents": [
            {
                "id": a["id"],
                "name": a.get("name", ""),
                "archetype": a.get("archetype", ""),
                "emoji": a.get("emoji", ""),
                "symbol_svg": a.get("symbol_svg", ""),
            }
            for a in agents
        ],
    }


# ── Cascade Config Resolution ────────────────────────────────────────────────
# Haven → Cove → Presence inheritance for settings like mirror_id.
# Each level can override or inherit from the level above.


def _load_yaml_safe(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing or invalid."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_haven_defaults() -> dict:
    """Load Haven-level defaults from haven.yaml."""
    data = _load_yaml_safe(_HAVEN_CONFIG_PATH)
    haven = data.get("haven", data)  # handle both {haven: {defaults: ...}} and flat
    return haven.get("defaults", {})


def _get_cove_defaults() -> dict:
    """Load Cove-level defaults from cove.yaml."""
    data = _load_yaml_safe(_COVE_CONFIG_PATH)
    cove = data.get("cove", data)
    return cove.get("defaults", {})


def _get_presence_tuning(presence_id: Optional[str] = None) -> dict:
    """Load Presence-level tuning config.

    In single-Presence mode, reads from agent.yaml's tuning section.
    In multi-Presence mode, would read from the Presence's config.
    """
    # For now: single-Presence mode reads from agent.yaml
    config = load_config()
    return config.get("tuning", {})


def resolve_cascade(key: str, presence_id: Optional[str] = None, default=None):
    """Resolve a setting through the Haven → Cove → Presence cascade.

    Checks Presence first (most specific), then Cove, then Haven.
    Returns the first non-None value found, or the default.

    Args:
        key: Setting name (e.g., "mirror_id")
        presence_id: Presence to check (optional, for multi-Presence)
        default: Fallback if no level sets this key
    """
    # Presence level (most specific)
    presence_tuning = _get_presence_tuning(presence_id)
    val = presence_tuning.get(key)
    if val is not None:
        return val

    # Cove level
    cove_defaults = _get_cove_defaults()
    val = cove_defaults.get(key)
    if val is not None:
        return val

    # Haven level
    haven_defaults = _get_haven_defaults()
    val = haven_defaults.get(key)
    if val is not None:
        return val

    return default


def resolve_mirror_id(presence_id: Optional[str] = None) -> str:
    """Resolve the active mirror through the cascade.

    Presence > Cove > Haven > ACTIVE_MIRROR env > "scripture-tpt"
    """
    import os
    env_default = env("ACTIVE_MIRROR", "scripture-tpt")
    return resolve_cascade("mirror_id", presence_id, default=env_default)


# ── Cove Config (Family Settings Layer) ─────────────────────────────────────
# Full structured config for the Cove. Loaded from config/cove.yaml.
# Provides typed accessors and the full dict for the Settings API.
#
# Fallback chain: overlay cove.yaml → cove-core cove.yaml.example


# Defaults matching cove.yaml.example — used when no cove.yaml exists at all.
_COVE_DEFAULTS = {
    "id": "default-cove",
    "name": "My Cove",
    "type": "family",
    "description": "",
    "operator": {"name": "Operator", "id": "operator", "contact": "", "aliases": []},
    "max_presences": 8,
    "recommended_presences": 6,
    "presence_cap_warning": 6,
    "api_provider": "operator",
    "billing": {"plan": "cove", "stripe_customer_id": "", "affiliate_code": ""},
    "infrastructure": "vps",
    "host": "",
    "domain": "",
    "subdomain_routing": False,  # per-operator MC subdomains; True once *.{cove} wildcard is live
    "timezone": "America/New_York",
    "team": {"admin_agent": "stuart", "shared_agents": []},
    "features": {
        "team_tab": True,
        "action_board": True,
        "creation_flows": True,
        "files": True,
        "calendar": True,
        "tuning": True,
        "voice": True,
        "messaging": True,
        "marketplace": False,
        "premium_workflows": False,
        # Mirrors OFF by default (#156). A Cove shows a dismissible prompt to
        # connect a music service; the operator opts in rather than finding it
        # pre-checked with nothing behind it. Enable per-Cove in the instance config.
        "mirror": False,
        "mirror_sources": "music-mirror",
    },
    "naming": {"last_name": "", "name_locked": True},
    "auth": {"method": "signin_link", "token_expiry_days": 90, "session_timeout_hours": 0},
    "defaults": {"mirror_id": "music-mirror", "tuning_family": "default"},
    # Storage: open-source default is unlimited (disk-bounded). The hosted layer
    # overrides default_quota per tier via the instance cove.yaml. A presence may
    # override its own quota; otherwise it inherits this cove-wide default.
    "storage": {"default_quota": "none"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override values win."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _read_cove_section(path) -> dict:
    """Read a cove.yaml(.example) file and return its 'cove:' section (or {})."""
    data = _load_yaml_safe(path)
    if not data:
        return {}
    return data.get("cove", data)


@lru_cache()
def load_cove_config() -> dict:
    """Load the full Cove config as a LAYERED cascade:

        _COVE_DEFAULTS (code)  <  cove.yaml.example (repo standard)  <  instance cove.yaml

    The repo's example is ALWAYS the base layer — it carries the standard team,
    manager channels (Stuart/Mercer), tools, and feature defaults that ship with
    every Cove. The instance's own cove.yaml overrides ONLY its custom values
    (id, operator, domain, naming, per-Cove agent bindings). That is what makes
    an install "nothing but its overrides": everything standard is inherited from
    the repo, so improving the example flows to every Cove with no drift.

    Previously this was either/or (instance OR example). Layering means an
    instance that omits, say, merchant_channel still inherits it from the repo.
    Manager-channel *visibility* stays gated on COVE_MODE/operator role in
    core.py + channels.py, so single-mode Coves are unaffected at the UI.
    """
    merged = dict(_COVE_DEFAULTS)
    # Base layer: the repo's standard config (team, managers, tools, features).
    if CORE_CONFIG_DIR.exists():
        merged = _deep_merge(merged, _read_cove_section(CORE_CONFIG_DIR / "cove.yaml.example"))
    # Override layer: this instance's own values (install + any per-Cove tweaks).
    if _COVE_CONFIG_PATH.exists():
        merged = _deep_merge(merged, _read_cove_section(_COVE_CONFIG_PATH))
    return merged


def get_default_quota() -> str:
    """Cove-wide default Nextcloud quota for newly provisioned presences.

    Open-source default is "none" (unlimited — bounded only by the host disk).
    The hosted layer overrides this per tier via the instance cove.yaml
    `storage.default_quota`. A presence-specific quota (passed by the caller)
    wins over this cove-wide default.
    """
    c = load_cove_config()
    return (c.get("storage") or {}).get("default_quota") or "none"


def get_cove_identity() -> dict:
    """Cove identity: id, name, type, description."""
    c = load_cove_config()
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "type": c.get("type"),
        "description": c.get("description"),
    }


def get_cove_operator() -> dict:
    """Operator info: name, id, contact, aliases."""
    return load_cove_config().get("operator", _COVE_DEFAULTS["operator"])


def _derive_admin_url(cove: dict, channel: dict, default_sub: str) -> str:
    """Admin MC URL for a manager channel — ALWAYS derived from THIS Cove's own
    domain, never a stored literal (which would leak another Cove, e.g. the
    founder's). Blank when the Cove has no domain (so the UI shows no Admin link).
    """
    dom = (cove.get("domain") or "").strip().lstrip("*").lstrip(".")
    if not dom:
        return ""
    sub = (channel.get("agent_id") or channel.get("name") or default_sub).split("-")[0].lower()
    return f"https://{sub}.{dom}"


def get_steward_channel_config() -> Optional[dict]:
    """Steward channel config from cove.yaml. Returns None if disabled or missing."""
    c = load_cove_config()
    sc = c.get("steward_channel")
    if not sc or not sc.get("enabled", True):
        return None
    # admin_url is derived from this Cove's domain — never inherited from a default.
    return {**sc, "admin_url": _derive_admin_url(c, sc, "stuart")}


def _is_steward_channel(channel: str) -> bool:
    """Check if the given channel is a steward channel (e.g. stuart-day, stuart-deep).

    Steward channels are prefixed: stuart-day, stuart-deep — NOT bare day/deep.
    The cove.yaml channels dict uses bare keys (day, deep) so we must
    reconstruct the full prefixed name before matching.
    """
    sc = get_steward_channel_config()
    if not sc:
        return False
    steward_name = sc.get("name", "stuart").lower()
    channels = sc.get("channels", {})
    for ch_key in channels:
        if channel == f"{steward_name}-{ch_key}":
            return True
    return False


def get_merchant_channel_config() -> Optional[dict]:
    """Merchant channel config from cove.yaml. Returns None if disabled or missing."""
    c = load_cove_config()
    mc = c.get("merchant_channel")
    if not mc or not mc.get("enabled", True):
        return None
    # admin_url derived from this Cove's domain — never inherited from a default.
    return {**mc, "admin_url": _derive_admin_url(c, mc, "mercer")}


def _is_merchant_channel(channel: str) -> bool:
    """Check if the given channel is a merchant channel (e.g. mercer-day, mercer-deep).

    Same pattern as _is_steward_channel but for the merchant manager.
    """
    mc = get_merchant_channel_config()
    if not mc:
        return False
    merchant_name = mc.get("name", "mercer").lower()
    channels = mc.get("channels", {})
    for ch_key in channels:
        if channel == f"{merchant_name}-{ch_key}":
            return True
    return False


def _get_manager_for_channel(channel: str) -> Optional[str]:
    """Return the manager type for a channel, or None if it's a regular channel.

    Returns 'steward', 'merchant', or None.
    """
    if _is_steward_channel(channel):
        return 'steward'
    if _is_merchant_channel(channel):
        return 'merchant'
    return None


def get_capacity_config() -> dict:
    """Capacity settings: max_presences, recommended, warning threshold."""
    c = load_cove_config()
    return {
        "max_presences": c.get("max_presences", 8),
        "recommended_presences": c.get("recommended_presences", 6),
        "presence_cap_warning": c.get("presence_cap_warning", 6),
    }


def get_feature_flags() -> dict:
    """Feature flags dict. Keys are feature names, values are bool.

    In single mode, merges runtime overrides from /app/data/feature-overrides.yaml
    on top of cove.yaml defaults. This allows the Settings toggle to work even
    though /app/config/ is mounted read-only.
    """
    base = load_cove_config().get("features", _COVE_DEFAULTS["features"])
    overrides = _load_feature_overrides()
    if overrides:
        return {**base, **overrides}
    return base


# ── Feature overrides (writable, survives container restart) ──────────────
_FEATURE_OVERRIDES_PATH = Path("/app/data/feature-overrides.yaml")


def _load_feature_overrides() -> dict:
    """Load runtime feature overrides from the writable data volume."""
    if not _FEATURE_OVERRIDES_PATH.exists():
        return {}
    try:
        with open(_FEATURE_OVERRIDES_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_feature_overrides(updates: dict) -> bool:
    """Save feature flag overrides to the writable data volume.

    Used in single mode where /app/config/cove.yaml is read-only.
    Merges updates into existing overrides.
    """
    try:
        current = _load_feature_overrides()
        current.update(updates)
        _FEATURE_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_FEATURE_OVERRIDES_PATH, "w") as f:
            yaml.dump(current, f, default_flow_style=False)
        # Clear the cove config cache so get_feature_flags picks up changes
        load_cove_config.cache_clear()
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to save feature overrides: {e}")
        return False


def is_feature_enabled(feature: str) -> bool:
    """Check if a specific feature is enabled."""
    return get_feature_flags().get(feature, False)


def get_billing_config() -> dict:
    """Billing config: api_provider, plan, stripe IDs, affiliate code."""
    c = load_cove_config()
    billing = c.get("billing", _COVE_DEFAULTS["billing"])
    return {
        "api_provider": c.get("api_provider", "operator"),
        **billing,
    }


def get_auth_config() -> dict:
    """Auth config: method, token expiry, session timeout."""
    return load_cove_config().get("auth", _COVE_DEFAULTS["auth"])


def get_naming_config() -> dict:
    """Naming rules: last_name, name_locked."""
    return load_cove_config().get("naming", _COVE_DEFAULTS["naming"])


def get_team_config() -> dict:
    """Team config: admin_agent ID and list of shared agent IDs."""
    return load_cove_config().get("team", _COVE_DEFAULTS["team"])


def get_admin_ids() -> list:
    """Presence IDs with Cove admin access (manage Presences, invite, etc.)."""
    return load_cove_config().get("admin_ids", [])


def get_cove_settings() -> dict:
    """Full Cove settings dict for the Settings API.

    Returns the complete config — safe for the MC Settings tab.
    No secrets (API keys, passwords) are stored in cove.yaml.
    """
    return load_cove_config()


def save_cove_config(updates: dict) -> bool:
    """Write updated Cove settings to cove.yaml.

    Merges updates into the current config and writes back.
    Clears the load cache so the next read picks up changes.

    Args:
        updates: Partial dict of cove settings to merge.
    Returns:
        True if write succeeded, False on error.
    """
    try:
        current = load_cove_config()
        merged = _deep_merge(current, updates)
        output = {"cove": merged}

        with open(_COVE_CONFIG_PATH, "w") as f:
            yaml.dump(output, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        # Clear cached config
        load_cove_config.cache_clear()
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to save cove config: {e}")
        return False


def get_model_override() -> str | None:
    """Emergency admin override — when set, all chat routes to this model.
    
    Bypasses the pressure-based router entirely. Used for:
    - Locking to a known-good model during family events
    - Debugging model issues without config changes  
    - Emergency fallback when router misbehaves
    
    Reads from /app/data/feature-overrides.yaml (runtime settings storage)
    """
    import yaml
    from pathlib import Path
    
    overrides_path = Path("/app/data/feature-overrides.yaml")
    if not overrides_path.exists():
        return None
    
    try:
        with open(overrides_path) as f:
            cfg = yaml.safe_load(f) or {}
        override = cfg.get("model_override")
        if override and isinstance(override, str) and override.strip():
            return override.strip()
    except Exception:
        pass
    return None


def get_model_override_fallback() -> str | None:
    """Manual-mode fallback for the model override (#MDL1).

    When the forced model fails (timeout, provider outage), the hop chain
    goes HERE before the local floor. Without it an override outage fell
    straight to the best-installed local model (grok-timeout incident,
    2026-07-17). Same storage as model_override:
    /app/data/feature-overrides.yaml, key model_override_fallback.
    Meaningless without an active override — callers only consult it when
    get_model_override() returns a model.
    """
    import yaml
    from pathlib import Path

    overrides_path = Path("/app/data/feature-overrides.yaml")
    if not overrides_path.exists():
        return None

    try:
        with open(overrides_path) as f:
            cfg = yaml.safe_load(f) or {}
        fb = cfg.get("model_override_fallback")
        if fb and isinstance(fb, str) and fb.strip():
            return fb.strip()
    except Exception:
        pass
    return None
