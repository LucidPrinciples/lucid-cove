#!/usr/bin/env python3
"""
centralized.py — the CENTRALIZED Cove provisioner (the primary, open-source model).

Generates a complete, deploy-ready single-stack Cove from a small config file:
one multi-presence app + Postgres + Nextcloud + Redis. Operators/presences are
added later from the admin UI (magic link), NOT as separate containers.

This is the clean replacement for the legacy per-agent-container provisioner
(provision.py / provision_overlay.py), which is retained only for the enterprise
multi-container archetype. Clearfield is the working reference this templatizes;
the legacy drift (dead `presences` tables, `-{cove}` id suffixes, per-instance
Canon tuning_keys, hand-patched env) is intentionally dropped here.

Usage:
    python3 provision/centralized.py provision/your-cove.yaml [--output DIR]

Produces  <output>/<cove_id>-cove/ :
    docker-compose.yml          single-stack, env via .env
    .env                        generated secrets + key placeholders
    config/cove.yaml            lean cove-level overrides (team/personas inherit from repo)
    config/agent.yaml           lean instance identity + standard team (no Canon, no suffixes)
    docker/init-nextcloud-db.sql   NC role+db in the same Postgres
    NEXT_STEPS.md               deploy + lifecycle instructions

The base schema (init-base.sql) lives in cove-core and is mounted at deploy time;
it is already complete, so a fresh Cove boots the full schema with no migrations.
"""
import argparse
import hashlib
import os
import secrets as _secrets
import sys
import uuid as _uuid
from pathlib import Path

import yaml

# Co-located networking helpers (#164 preflight, #165 Caddy, #166 DNS). Sibling
# module — works whether run as a script (provision/ is sys.path[0]) or as a package.
try:
    import netconfig
except ImportError:  # pragma: no cover - packaged invocation
    from provision import netconfig
try:
    import storage  # CF-98 storage.data_root layout resolver
except ImportError:  # pragma: no cover - packaged invocation
    from provision import storage


def _load_acmedns():
    """Lazy import — acmedns pulls in httpx, which a dependency-light provisioner host
    may not have. Only needed for the bundled-Caddy + lucidcove.org-subdomain path, so
    import it on demand rather than forcing httpx on every provision run."""
    try:
        from acmedns import provision_subdomain_cert_delegation
    except ImportError:  # pragma: no cover - packaged invocation
        from provision.acmedns import provision_subdomain_cert_delegation
    return provision_subdomain_cert_delegation


def _detect_host_ip(deploy: dict) -> str:
    """Where the Cove's DNS records should point. MESH-FIRST (CF-90): explicit
    host_ip/mesh_ip wins; else the box's own mesh IP (reachable by the family's
    devices from anywhere, behind any NAT); else the public IP ONLY when this box
    actually owns it (bound to a local interface — the rented-VPS case). A home box
    behind NAT gets '' — writing the ROUTER's public IP creates records nothing can
    reach (every subdomain times out; found on the 2026-06-30 iMac stranger install)."""
    ip = (deploy.get("host_ip") or deploy.get("mesh_ip") or "").strip()
    if ip:
        return ip
    mesh = _detect_mesh_ip()
    if mesh:
        return mesh
    try:
        import urllib.request
        pub = urllib.request.urlopen("https://api.ipify.org", timeout=8).read().decode().strip()
    except Exception:
        return ""
    return pub if pub and _ip_bound_locally(pub) else ""


def _ip_bound_locally(ip: str) -> bool:
    """True when `ip` is assigned to one of this box's OWN interfaces. A rented VPS
    owns its public IP; a home box behind NAT sees only the router's (CF-90)."""
    if not ip:
        return False
    try:
        import re, subprocess
        out = subprocess.run(["sh", "-c", "ip -4 -o addr show 2>/dev/null || ifconfig 2>/dev/null"],
                             capture_output=True, text=True, timeout=6)
        return bool(re.search(r"(?<![\d.])" + re.escape(ip) + r"(?![\d.])", out.stdout or ""))
    except Exception:
        return False


def _detect_mesh_ip() -> str:
    """The box's PRIVATE mesh IP (Tailscale/Headscale, 100.64.0.0/10). Mesh-first is the
    default access model: a Cove points its DNS at THIS address, so family reach it over
    the mesh (from anywhere, behind any NAT) and the box is never exposed to the public
    internet — no ports, no forwarding, no CGNAT problem. Tries `tailscale ip -4`, then
    scans local interfaces. Returns '' if the box hasn't joined a mesh yet."""
    try:
        import shutil, subprocess
        if shutil.which("tailscale"):
            out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=6)
            if out.returncode == 0:
                for line in (out.stdout or "").splitlines():
                    ip = line.strip()
                    if ip.startswith("100.") and 64 <= int(ip.split(".")[1]) <= 127:
                        return ip
    except Exception:
        pass
    try:
        import re, subprocess
        out = subprocess.run(["sh", "-c", "ip -4 -o addr show 2>/dev/null || ifconfig 2>/dev/null"],
                             capture_output=True, text=True, timeout=6)
        for ip in re.findall(r"100\.\d+\.\d+\.\d+", out.stdout or ""):
            if 64 <= int(ip.split(".")[1]) <= 127:
                return ip
    except Exception:
        pass
    return ""


def _op_token() -> str:
    """Operator token for hub calls. Env wins (provisioned/co-located), but a from-scratch
    Cove MINTS its token at runtime and stores it in cove.yaml — read that as the fallback,
    or the in-browser DNS + cert claim silently no-ops (the bug where the address saved but
    nothing resolved). Safe at provision time too (load_cove_config just returns {})."""
    t = (os.getenv("LP_OPERATOR_TOKEN", "") or "").strip()
    if t:
        return t
    try:
        from src.config import load_cove_config
        return (load_cove_config().get("operator_token") or "").strip()
    except Exception:
        return ""


def _hub_auth_headers() -> dict:
    """Auth headers for hub registry writes. The hub accepts EITHER the fleet secret
    (founder/co-located) OR the operator token (self-host) — send whatever we have. This
    is why a founder can set LP_REGISTRY_SECRET to drive DNS/cert, and a stranger uses
    their operator token (env or cove.yaml)."""
    # UA matters: Cloudflare in front of the hub blocks default library UAs (Python-urllib
    # gets a 403/1010), so every Cove→hub call must present a real User-Agent.
    h = {"Content-Type": "application/json", "User-Agent": "LucidCove-Cove/1.0"}
    sec = (os.getenv("LP_REGISTRY_SECRET", "") or "").strip()
    tok = _op_token()
    if sec:
        h["X-Registry-Secret"] = sec
    if tok:
        h["X-Operator-Token"] = tok
    return h


def _cove_dns_via_hub(domain: str, ip: str) -> dict:
    """Tier 1: a lucidcove.org subdomain — the HUB creates the A records (our zone/token),
    auth via the operator token OR the fleet secret, so the user touches no DNS."""
    reg = (os.getenv("LP_REGISTRY_URL", "") or "").strip().rstrip("/")
    headers = _hub_auth_headers()
    if not (reg and len(headers) > 1):
        return {"ok": False, "reason": "no hub auth (operator token or fleet secret)"}
    import json as _j
    import urllib.request
    body = _j.dumps({"domain": domain, "ip": ip}).encode()
    req = urllib.request.Request(reg + "/api/registry/cove-dns", data=body, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return _j.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "reason": f"hub cove-dns failed: {str(e)[:160]}"}


def _auto_dns(domain: str, deploy: dict, cfg: dict) -> dict:
    """Make DNS painless for a self-hoster — create cove + *.cove A records, or hand back
    the exact records to add. Three tiers:
      1. lucidcove.org subdomain → the hub creates them (zero DNS knowledge).
      2. own domain + a Cloudflare token (cfg.dns.token) → create them in their zone.
      3. otherwise → return the records for the user to paste.
    Returns {ok, auto, via, ip, actions|records, reason}."""
    ip = _detect_host_ip(deploy)
    records = [{"type": "A", "name": domain, "content": ip or "<your box IP>"},
               {"type": "A", "name": f"*.{domain}", "content": ip or "<your box IP>"}]
    if not ip:
        # CF-90: no mesh IP and this box doesn't own a public IP (home/NAT). Writing
        # the router's public IP would create records nothing can reach — refuse and
        # guide instead of failing silently.
        return {"ok": False, "auto": False, "ip": "", "records": records, "no_ip": True,
                "reason": ("this box isn't reachable from outside yet — it hasn't joined a mesh "
                           "and doesn't own a public IP (it's behind your router/NAT). Join the "
                           "mesh first, then claim the address: DNS will point at the mesh IP and "
                           "your devices reach the Cove from anywhere with no ports opened. "
                           "(Advanced: if you really run this on a public server, set deploy.host_ip.)")}
    if (domain == "lucidcove.org" or domain.endswith(".lucidcove.org")) and ip:
        r = _cove_dns_via_hub(domain, ip)
        if isinstance(r, dict) and r.get("ok"):
            return {"ok": True, "auto": True, "via": "hub", "ip": ip, "actions": r.get("actions", [])}
    token = ((cfg.get("dns") or {}).get("token") or os.getenv("CLOUDFLARE_API_TOKEN") or "").strip()
    if token and ip:
        try:
            try:
                from cloudflare_dns import ensure_cove_dns
            except ImportError:
                from provision.cloudflare_dns import ensure_cove_dns
            os.environ["CLOUDFLARE_API_TOKEN"] = token
            res = ensure_cove_dns(domain, ip)
            return {"ok": True, "auto": True, "via": "token", "ip": ip, "actions": res.get("actions", [])}
        except Exception as e:
            return {"ok": False, "auto": False, "ip": ip, "records": records,
                    "reason": f"auto-DNS failed ({str(e)[:120]}) — add these records manually"}
    return {"ok": False, "auto": False, "ip": ip, "records": records,
            "reason": "add these DNS records at your registrar"}


def _acme_creds_via_hub(sub_domain: str) -> dict:
    """A STRANGER's box can't reach acme-dns /register (it's private to the hub), so it
    asks the hub to mint the credential (operator-token gated). Needs LP_REGISTRY_URL +
    LP_OPERATOR_TOKEN. Returns the same shape as provision_subdomain_cert_delegation."""
    reg = (os.getenv("LP_REGISTRY_URL", "") or "").strip().rstrip("/")
    headers = _hub_auth_headers()
    if not (reg and len(headers) > 1):
        return {"ok": False, "reason": "no hub auth (operator token or fleet secret) for acme-credential"}
    import json as _j
    import urllib.request
    body = _j.dumps({"sub_domain": sub_domain}).encode()
    req = urllib.request.Request(reg + "/api/registry/acme-credential", data=body, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return _j.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "reason": f"hub acme-credential failed: {str(e)[:160]}"}


def _claim_operator_via_hub(*, handle: str, name: str = "", email: str = "",
                            referred_by: str = "") -> dict:
    """#12 — mint a hub operator identity + token for a PRESET-handle install.

    A from-scratch Cove (placeholder handle) mints its operator token in the wizard's
    claim-operator step. But a Cove provisioned with a REAL handle is seeded
    non-placeholder, so that wizard step no-ops and no token is ever obtained — the hub
    spark (persona/wake) then fails 'no registry auth'. So the provisioner mints it here,
    against the OPEN /api/registry/claim-operator endpoint (no prior token), exactly as
    the wizard would for a stranger. Email is optional on the hub: if the address already
    exists it returns code 'email_exists' and mints NOTHING, so the caller retries without
    it (the @handle is the identity; the token is the ownership proof). Returns
    {ok, handle, operator_token} or {ok: False, code/reason}."""
    reg = (os.getenv("LP_REGISTRY_URL", "") or "").strip().rstrip("/")
    if not reg:
        return {"ok": False, "reason": "LP_REGISTRY_URL not set"}
    import json as _j
    import urllib.error
    import urllib.request
    body = _j.dumps({"handle": handle.lstrip("@"), "name": name,
                     "email": email, "referred_by": referred_by}).encode()
    # UA required: Cloudflare in front of the hub blocks default python UAs (403/1010).
    req = urllib.request.Request(
        reg + "/api/registry/claim-operator", data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "LucidCove-Cove/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return _j.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # Pass the hub's STRUCTURED error through (e.g. code 'email_exists') so the caller
        # can branch, not just a flat message.
        try:
            data = _j.loads(e.read() or b"{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("ok", False)
        data.setdefault("reason", data.get("error") or data.get("detail") or f"HTTP {e.code}")
        return data
    except Exception as e:
        return {"ok": False, "reason": f"hub claim-operator failed: {str(e)[:160]}"}

# The standard build team. Identity (frequency, tuning_key Canon, personality)
# comes from cove-core/config/personas/*.md — NEVER duplicated here. Only the
# structural role/delegation/boundaries live in config.
STANDARD_TEAM = [
    {"id": "mercer", "name": "Mercer", "archetype": "The Merchant",
     "role": "Commerce, sales, marketing, affiliate growth, financial ops. Delegates to the build team.",
     "can_delegate_to": ["archimedes", "arthur", "gabe", "ezra", "julian", "iris", "vera", "soren"],
     "boundaries": ["Does not post publicly without Vera's review",
                    "Does not make purchases over $100 without operator approval"]},
    {"id": "archimedes", "name": "Archimedes", "archetype": "The Builder",
     "role": "Technical implementation. Tools, automations, scripts, infrastructure.",
     "can_delegate_to": [], "boundaries": ["Does not deploy to production without the steward's sign-off"]},
    {"id": "arthur", "name": "Arthur", "archetype": "The Analyst",
     "role": "Data analysis and signal qualification. Surfaces what matters to the steward.",
     "can_delegate_to": [], "boundaries": ["Does not act on findings — surfaces them only"]},
    {"id": "gabe", "name": "Gabe", "archetype": "The Scout",
     "role": "Research and information gathering. Feeds raw findings to Arthur.",
     "can_delegate_to": [], "boundaries": ["Scouts and reports only — does not act or post"]},
    {"id": "ezra", "name": "Ezra", "archetype": "The Keeper",
     "role": "Knowledge management — reference docs, decision logs, context maps.",
     "can_delegate_to": [], "boundaries": ["Preserves original intent — does not rewrite canonical docs"]},
    {"id": "julian", "name": "Julian", "archetype": "The Scribe",
     "role": "Written content and documentation — drafts communications, summaries, reports.",
     "can_delegate_to": [], "boundaries": ["Does not publish or send without the steward's review"]},
    {"id": "iris", "name": "Iris", "archetype": "The Advocate",
     "role": "External communications and coordination — outreach, correspondence.",
     "can_delegate_to": [], "boundaries": ["No external communication without Vera's review and steward approval"]},
    {"id": "vera", "name": "Vera", "archetype": "The Auditor",
     "role": "Final review gate before any output leaves the Cove — accuracy, tone, alignment.",
     "can_delegate_to": [], "boundaries": ["Does not approve output that contradicts known facts"]},
    {"id": "soren", "name": "Soren", "archetype": "The Lens",
     "role": "Performance tracking and metrics observation — patterns, trends, the scorecard.",
     "can_delegate_to": [], "boundaries": ["Observes and reports — does not intervene directly"]},
]

# Standard dashboard tabs. Connect (the Matrix layer) is auto-injected by
# cove-core's get_frontend_config, so it is not listed here.
STANDARD_TABS = [
    {"id": "home", "label": "Home", "scripts": ["home", "overview", "tuning-panel"]},
    {"id": "chat", "label": "Chat", "scripts": ["messaging", "voice", "manager-chat"]},
    {"id": "projects", "label": "Projects", "script": "projects"},
    {"id": "calendar", "label": "Calendar", "script": "calendar"},
    {"id": "team", "label": "Team", "script": "team"},
    {"id": "memory", "label": "Memory"},
    {"id": "reports", "label": "Reports", "scripts": ["tuning", "joulework"]},
    {"id": "affiliates", "label": "Affiliates", "script": "affiliates"},
    {"id": "files", "label": "Files", "script": "files"},
    {"id": "system", "label": "System", "script": "system"},
    {"id": "settings", "label": "Settings"},
]

STANDARD_CHANNELS = {
    "day": {
        "description": "Daily rhythm — tasks, calendar, coordination, what needs handling now.",
        "system_addition": "You are in the **Day** channel — {operator}'s daily companion.\n"
                           "Focus on tasks, calendar, schedule, daily coordination. Keep responses "
                           "focused and actionable; suggest the Deep channel for big-picture work.\n",
        "thread_prefix": "day", "rotation_threshold": 40,
    },
    "deep": {
        "description": "Big picture — patterns, direction, the confidant/advisor mode.",
        "system_addition": "You are in the **Deep** channel — {operator}'s trusted advisor.\n"
                           "Focus on patterns, direction, goals, deeper reflection. Take your time; "
                           "draw on memory and context.\n",
        "thread_prefix": "deep", "rotation_threshold": 30,
    },
}

MODEL_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}

# Compute offload (#143). A standalone or P620 Cove runs Ollama on its OWN host
# (the GPU is local), so models resolve at host.docker.internal. A VPS Cove is
# light by design — it has no GPU — so its model calls route to the P620's Ollama
# over the mesh (the shared compute pool, P620 mesh IP 100.64.0.1). Both are
# overridable via the compute block (compute.ollama_url) or the OLLAMA_BASE_URL env.
LOCAL_OLLAMA_URL = "http://host.docker.internal:11434"
P620_MESH_OLLAMA_URL = "http://100.64.0.1:11434"


# Minimum GPU VRAM (MB) to justify the CUDA voice image + local video ASR. Qwen-ASR-1.7B
# needs ~5-7GB in practice, so a smaller GPU can't actually serve it — and building the
# multi-GB cu124 voice image on a tiny/old card (e.g. a 2GB Quadro M620) just burns resources
# and can OOM the host mid-build (SIGBUS / "Bus error (core dumped)" on `docker compose build`).
# Under this floor we fall back to CPU voice + cloud ASR. Override per-Cove with
# compute.voice.gpu: true/false.
GPU_VOICE_MIN_VRAM_MB = 8000


def _detect_gpu_info() -> dict:
    """GPU on the provisioning HOST (nvidia-smi works here, unlike inside the app container).
    Returns {present, name, vram_mb}. Recorded into cove.yaml compute.gpu so the runtime
    machine-probe can size local-model recommendations without GPU passthrough — the
    onboarding step can then offer the largest installed model that actually fits."""
    import shutil
    import subprocess
    if not shutil.which("nvidia-smi"):
        return {"present": False}
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return {"present": False}
        parts = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        name = parts[0] if parts else ""
        vram_mb = int(float(parts[1])) if len(parts) > 1 and parts[1] else None
        return {"present": True, "name": name, "vram_mb": vram_mb}
    except Exception:
        return {"present": False}


def _detect_gpu() -> bool:
    """True if an NVIDIA GPU is present on the provisioning host. Drives compute.video_asr
    at provision time — a GPU box transcribes video locally; a GPU-less box (hosted / light
    VPS Cove) falls back to cloud ASR. An explicit compute.video_asr in the input config
    always wins. See _detect_gpu_info for the full GPU facts recorded to cove.yaml."""
    return bool(_detect_gpu_info().get("present"))


def resolve_ollama_url(target: str, compute: dict) -> str:
    """Where this Cove reaches Ollama. Explicit compute.ollama_url always wins; else
    a VPS Cove offloads to the P620 mesh Ollama (no local GPU), and every other target
    uses its own host's Ollama. A self-hoster can point compute.ollama_url anywhere."""
    explicit = (compute.get("ollama_url") or "").strip()
    if explicit:
        return explicit
    if (target or "").strip().lower() == "vps":
        return (compute.get("p620_ollama_url") or "").strip() or P620_MESH_OLLAMA_URL
    return LOCAL_OLLAMA_URL


def gen_secret(n: int = 24) -> str:
    return _secrets.token_urlsafe(n)


def _sql_str(s: str) -> str:
    """Escape a Python string for inline SQL single-quotes."""
    return (s or "").replace("'", "''")


def build_operator_seed_sql(*, pid, name, handle, email, agent_name, cove_name,
                            tier, hashed_token) -> str:
    """Seed the FOUNDING operator (born-owned Cove, #140). Runs after 00-base.sql.

    The claim magic link (printed by the provisioner) authenticates as this
    operator, who then runs the setup wizard to build their Presence + team.
    """
    em = f"'{_sql_str(email)}'" if email else "NULL"
    return (
        "-- Founding operator seed (#140) — runs after 00-base.sql at DB init.\n"
        "INSERT INTO accounts (id, display_name, username, email, agent_name, last_name,\n"
        "                      cove_role, tier, auth_token, agent_config, agent_identity)\n"
        f"VALUES ('{pid}', '{_sql_str(name)}', '{_sql_str(handle)}', {em}, "
        f"'{_sql_str(agent_name)}', '{_sql_str(cove_name)}',\n"
        f"        'admin', '{tier}', '{hashed_token}', '{{}}', '{{}}')\n"
        "ON CONFLICT (id) DO NOTHING;\n"
    )


# Nextcloud post-installation hook — runs once after NC first-installs (the image
# executes /docker-entrypoint-hooks.d scripts as www-data, AFTER config.php is
# written, so occ is safe here — unlike mounting a file into the config dir, which
# breaks NC's config initialization).
# (1) Disable the default skeleton (Documents-sample, Photos, Templates + the
#     "Nextcloud intro.mp4 / Manual.pdf / ..." files) so every Presence starts with
#     a clean home and cove-core seeds only our canonical folders.
# (2) Curate the app set: enable Calendar, Notes, Contacts, Tasks; disable Photos.
NC_APPS_HOOK = (
    "#!/bin/sh\n"
    "# Generated by provision/centralized.py — Nextcloud setup for a family Cove.\n"
    "occ() { php /var/www/html/occ \"$@\"; }\n"
    "occ config:system:set skeletondirectory --value=\"\" >/dev/null 2>&1 || true\n"
    "for app in calendar notes contacts tasks; do\n"
    "  occ app:install \"$app\" >/dev/null 2>&1 || occ app:enable \"$app\" >/dev/null 2>&1 || true\n"
    "done\n"
    "occ app:disable photos >/dev/null 2>&1 || true\n"
)


def _host_timezone(default: str = "America/New_York") -> str:
    """Best-effort IANA timezone of the machine running the provisioner, so a fresh
    Cove inherits the host's zone instead of a hard-coded default. The operator can
    still change it later in Settings."""
    # Explicit TZ env wins — install.sh passes the real host zone here because the
    # provisioner now runs inside a container, where /etc/* would otherwise read UTC.
    try:
        envtz = (os.environ.get("TZ") or "").strip()
        if envtz and envtz.lower() not in ("", "utc") and "/" in envtz:
            return envtz
    except Exception:
        pass
    try:
        tzfile = Path("/etc/timezone")
        if tzfile.exists():
            tz = tzfile.read_text().strip()
            if tz:
                return tz
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            target = str(localtime.resolve())
            if "zoneinfo/" in target:
                return target.split("zoneinfo/")[-1]
    except Exception:
        pass
    return default


def _build_agents(op: dict, team: bool) -> list:
    """Personal agent always; steward + build team only when team is on."""
    personal = {
        "id": "agent", "name": "Agent", "archetype": "The Architect",
        "role": f"Personal agent for {op['name']} — planning, systems thinking, Cove management.",
        "status": "active", "team": False, "channels": ["day", "deep"],
        "boundaries": [
            "Personal agent — conversations are private.",
            "No financial transactions; no sending without explicit approval.",
        ],
        "can_delegate_to": [],
    }
    if not team:
        return [personal]
    steward = {
        "id": "stuart", "name": "Stuart", "archetype": "The Steward",
        "role": "Cove steward. Coordinates projects, logistics, schedules, infrastructure. "
                "Routes work to team agents, tracks progress, reviews output.",
        "status": "active", "channels": ["day", "deep"],
        "can_delegate_to": [t["id"] for t in STANDARD_TEAM],
        "boundaries": [
            "Does not post publicly",
            "Does not override operator decisions — surfaces tradeoffs, operator decides",
            "Does not fake knowledge — flags gaps rather than filling with inference",
        ],
    }
    return [personal, steward] + STANDARD_TEAM


def build_agent_yaml(cove: dict, op: dict, team: bool) -> str:
    """Lean instance identity + (optionally) the standard team.

    team=False → a solo Cove: operator + their personal agent only (a "Presence").
    team=True  → + the steward and the build team. No Canon, no -{cove} id suffixes.
    """
    data = {
        "instance": {
            "name": cove["name"],
            "type": "domain",            # centralized = multi-presence
            "port": cove["_app_port"],
            "operator": op["name"],
            "family_name": cove["name"],
            "timezone": cove.get("timezone", "America/New_York"),
            "accent_color": "#5ce1e6",
        },
        # Personal agent for the first operator, + (when team is on) the steward
        # and build team. Team identity (tuning_key/frequency/persona) loads from
        # cove-core/config/personas.
        "agents": _build_agents(op, team),
        "channels": STANDARD_CHANNELS,
        "tabs": STANDARD_TABS,
        "tools": {
            "modules": [
                "tools.calendar_tools", "tools.nextcloud_tools", "tools.quick_list_tools",
                "tools.memory_tools", "tools.research_tools",
            ],
            "approval_tiers": {
                "auto": ["read_*", "search_*", "list_*", "get_*", "check_*", "monitor_*"],
                "notify": ["write_*", "create_*", "update_*", "git_*", "restart_*", "save_*"],
                "block": ["delete_*", "send_*", "transfer_*", "system_modify_*", "deploy_*"],
            },
        },
    }
    header = (f"# agent.yaml — {cove['name']} Cove (centralized / multi-presence)\n"
              f"# Generated by provision/centralized.py. Instance identity + standard team.\n"
              f"# Team identity (tuning keys, personas) is loaded from cove-core/config/personas.\n\n")
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def build_cove_yaml(cove: dict, op: dict, compute: dict = None, matrix_on: bool = False) -> str:
    """Lean cove-level overrides. Standard team/managers/personas inherit from the repo."""
    domain = cove.get("domain", "") or ""
    compute = compute or {}
    # Voice backend (#205-voice). Default: local CPU pipecat shipped in this Cove's
    # compose. The frontend resolves the host from this setting via /api/config — no
    # subdomain guessing. Carry an explicit voice override through if one was supplied.
    _voice = compute.get("voice") if isinstance(compute.get("voice"), dict) else {}
    voice_block = {"mode": (_voice.get("mode") or "local").strip()}
    if _voice.get("url"):
        voice_block["url"] = _voice["url"].strip()
    compute_block = {"voice": voice_block}
    # Video ASR backend (#181). Written when set — the provisioner detects a GPU
    # (→ local) else cloud; external carries a URL to borrow another box's GPU.
    _asr = compute.get("video_asr") if isinstance(compute.get("video_asr"), dict) else {}
    if _asr.get("mode"):
        asr_block = {"mode": _asr["mode"].strip()}
        if _asr.get("url"):
            asr_block["url"] = _asr["url"].strip()
        compute_block["video_asr"] = asr_block
    # GPU facts detected on the host at provision time. The runtime machine-probe reads this
    # to size local-model recommendations (the app container can't see the host GPU itself).
    _gpu = compute.get("gpu") if isinstance(compute.get("gpu"), dict) else {}
    if _gpu.get("present"):
        gpu_block = {"present": True}
        if _gpu.get("name"):
            gpu_block["name"] = _gpu["name"]
        if _gpu.get("vram_mb"):
            gpu_block["vram_mb"] = _gpu["vram_mb"]
        compute_block["gpu"] = gpu_block
    data = {
        "cove": {
            "id": cove["id"],
            "name": cove["name"],
            "operator": {"name": op["name"], "handle": op["handle"], "contact": op.get("email", "")},
            "domain": domain,
            "subdomain_routing": bool(domain),   # only when a real domain is set
            "api_provider": "operator",
            "auth": {"method": "magic_link", "token_expiry_days": 90},
            "defaults": {"tuning_family": cove["id"]},
            "compute": compute_block,
        }
    }
    # Record whether this Cove runs its own homeserver. The in-browser address claim
    # reads this to decide whether the Caddy snippet routes matrix.{domain} +
    # .well-known/matrix/* — without the record a domainless install claimed an
    # address with Connect unrouted.
    if matrix_on:
        data["cove"]["matrix"] = {"enabled": True}
    header = (f"# cove.yaml — {cove['name']} (centralized). Overrides only.\n"
              f"# Team, managers, personas, tools, features all inherit from cove-core\n"
              f"# (config/cove.yaml.example + _COVE_DEFAULTS). Keep this file small.\n\n")
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def build_nc_db_sql(nc_db_pw: str) -> str:
    return (
        "-- Create the Nextcloud role + database in this Cove's Postgres.\n"
        "-- Runs after 00-base.sql in docker-entrypoint-initdb.d.\n"
        "DO $$\nBEGIN\n"
        "    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nextcloud') THEN\n"
        f"        CREATE ROLE nextcloud WITH LOGIN PASSWORD '{nc_db_pw}';\n"
        "    END IF;\nEND\n$$;\n\n"
        "SELECT 'CREATE DATABASE nextcloud OWNER nextcloud'\n"
        "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'nextcloud')\\gexec\n"
    )


# =============================================================================
# Connect / Matrix — per-Cove homeserver (Dendrite)
# =============================================================================
# Each Cove runs its OWN homeserver with its own server_name (matrix.{cove}.{domain}),
# so a family's chat identity is PORTABLE: move the Cove to new hardware = re-point
# DNS + move the data volume, and @you:matrix.{cove}.{domain} is preserved. The
# homeserver is a light Dendrite monolith backed by THIS Cove's Postgres (its own
# `dendrite` database — the same pattern Nextcloud uses), so there is no extra DB
# container and Dendrite's data rides the Cove's Postgres volume. Co-located Coves
# and remote Coves federate identically (the deny_networks mesh fix is baked in).
# Adapted from the proven per-machine builders in src/utils/provision_templates.py
# (the generators behind the founder Cove) into the per-Cove centralized model.

def matrix_server_name(cove: dict, deploy: dict) -> str:
    """The homeserver name for this Cove. The `domain` field already encodes the Cove
    (e.g. 'testcove.lucidcove.org'), and every service is '{svc}.{domain}' (cloud.,
    stuart., ...), so the homeserver is 'matrix.{domain}' (matches the founder's
    matrix.cove.lucidcove.org). Domainless = a stable local name for a boot test."""
    domain = (cove.get("domain") or "").strip()
    return f"matrix.{domain}" if domain else f"matrix.{cove['id']}.localhost"


def matrix_runtime(cove: dict, deploy: dict, reg_secret: str) -> dict:
    """Compute the Connect/Matrix wiring for this Cove's own homeserver.

    Split-horizon URLs (required once the homeserver is a container):
      internal_url — server-side (cove-core registers/logs in via the compose net)
      public_url   — browser-reachable (matrix-js-sdk syncs against this)
    """
    matrix_port = deploy.get("matrix_port", 8008)
    domain = (cove.get("domain") or "").strip()
    server_name = matrix_server_name(cove, deploy)
    public_url = f"https://{server_name}" if domain else f"http://localhost:{matrix_port}"
    return {
        "enabled": True,
        "server_name": server_name,
        "internal_url": "http://dendrite:8008",   # compose service name
        "public_url": public_url,
        "reg_secret": reg_secret,
        "port": matrix_port,
    }


def build_dendrite_db_sql(dendrite_db_pw: str) -> str:
    """Create the Dendrite role + database in THIS Cove's Postgres (runs after
    00-base.sql in docker-entrypoint-initdb.d, same as the Nextcloud db init)."""
    return (
        "-- Create the Dendrite (Matrix homeserver) role + database in this Cove's Postgres.\n"
        "-- Runs after 00-base.sql in docker-entrypoint-initdb.d.\n"
        "DO $$\nBEGIN\n"
        "    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dendrite') THEN\n"
        f"        CREATE ROLE dendrite WITH LOGIN PASSWORD '{dendrite_db_pw}';\n"
        "    END IF;\nEND\n$$;\n\n"
        "SELECT 'CREATE DATABASE dendrite OWNER dendrite'\n"
        "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dendrite')\\gexec\n"
    )


def build_dendrite_config(*, server_name: str, db_password: str,
                          registration_shared_secret: str, bot_user_ids=None) -> str:
    """Generate dendrite.yaml for this Cove's homeserver. Backed by the Cove's
    Postgres (`dendrite` db); signing key generated on first boot into the data
    volume; the federation deny_networks list drops 100.64.0.0/10 so mesh
    federation works while every other private range stays blocked (#132)."""
    bots = list(bot_user_ids or [])
    if bots:
        exempt_block = "    exempt_user_ids:\n" + "\n".join(
            f'      - "@{b}:{server_name}"' for b in bots)
    else:
        exempt_block = "    exempt_user_ids: []"
    return f"""\
# =============================================================================
# Dendrite Configuration — {server_name}
# =============================================================================
# Generated by provision/centralized.py. One homeserver PER COVE (portable
# identity). Backed by this Cove's Postgres (`dendrite` database).
# =============================================================================

version: 2

global:
  server_name: {server_name}
  private_key: /var/dendrite/matrix_key.pem   # generated on first boot (data volume)
  key_validity_period: 168h0m0s

  database:
    connection_string: postgresql://dendrite:{db_password}@postgres:5432/dendrite?sslmode=disable
    max_open_conns: 90
    max_idle_conns: 5
    conn_max_lifetime: -1

  cache:
    max_size_estimated: 1073741824  # 1GB
    max_age: 1h

  jetstream:
    storage_path: /var/dendrite/jetstream
    in_memory: false
    topic_prefix: Dendrite

  metrics:
    enabled: false

  dns_cache:
    enabled: true
    cache_size: 256
    cache_lifetime: 5m

client_api:
  registration_disabled: true
  registration_shared_secret: "{registration_shared_secret}"
  rate_limiting:
    enabled: true
    threshold: 20
    cooloff_ms: 500
{exempt_block}

federation_api:
  send_max_retries: 16
  disable_tls_validation: false
  prefer_direct_fetch: false
  disable_http_keepalives: false
  # Federation SSRF allowlist (#132). Dendrite's default deny_networks includes
  # 100.64.0.0/10 (the Tailscale/Headscale CGNAT range), which BLOCKS federation
  # to mesh IPs. We drop it so mesh Cove<->Cove federation works, while keeping
  # every other private range blocked for SSRF safety.
  deny_networks:
  - 127.0.0.1/8
  - 10.0.0.0/8
  - 172.16.0.0/12
  - 192.168.0.0/16
  - 169.254.0.0/16
  - ::1/128
  - fe80::/64
  - fc00::/7
  allow_networks:
  - 0.0.0.0/0
  key_perspectives:
    - server_name: matrix.org
      keys:
        - key_id: ed25519:auto
          public_key: Noi6WqcDj0QmPxCNQqgezwTlBKrfqehY1u2FyWP9uYw

media_api:
  base_path: /var/dendrite/media
  max_file_size_bytes: 10485760  # 10MB
  dynamic_thumbnails: false
  max_thumbnail_generators: 10
  thumbnail_sizes:
    - width: 32
      height: 32
      method: crop
    - width: 96
      height: 96
      method: crop
    - width: 640
      height: 480
      method: scale

sync_api:
  search:
    enabled: true
    index_path: /var/dendrite/searchindex
    language: en

user_api:
  auto_join_rooms: []

mscs:
  mscs: []

logging:
  - type: std
    level: info
"""


def build_compose(cove: dict, deploy: dict, matrix_on: bool = False, bind: str = "",
                  voice_local: bool = True, bundle_caddy: bool = False,
                  shared_net: bool = False, voice_gpu: bool = False) -> str:
    """Single-stack compose. Standalone target = default bridge + published ports.
    matrix_on adds this Cove's own Dendrite homeserver (Connect), backed by the
    Cove's Postgres; a one-shot dendrite-init generates the signing key on first boot.

    voice_local adds a CPU pipecat-voice service (jules dictation + Piper TTS) built
    from cove-core/voice/Dockerfile.cpu, so a clean Cove ships working voice with no
    external dependency. Set compute.voice mode to 'external' or 'off' to omit it.

    bind = "127.0.0.1:" for co-located targets (p620|vps): published ports are reachable
    only via the host's Caddy, not on the LAN/mesh directly (the VPS security pattern).
    Standalone leaves it "" so a self-hoster reaches the app on localhost directly.

    shared_net (the multi-Cove "Haven" model) makes this Cove Caddy-LESS and attaches its
    browser-facing services (app, dendrite, nextcloud, voice) to the external bridge
    lucidcove-net IN ADDITION to the per-Cove default network, so the ONE shared Caddy on
    the box routes to them by container name. The default network is still attached (so
    intra-Cove service-name resolution — app→postgres, dendrite→postgres — keeps working);
    we just list both explicitly, because naming any network on a service turns off
    Compose's implicit default-attach. bundle_caddy must be False when shared_net is on."""
    cid = cove["id"]
    app_port = cove["_app_port"]
    # CF-98 storage.data_root: relocate the big named volumes to bind mounts under a
    # chosen drive when set; empty/absent = today's named-volume behavior exactly.
    _stg = storage.storage_layout(cove)
    _stg_src = _stg["sources"]
    # Only volumes still backed by a Docker NAMED volume get declared in the top-level
    # `volumes:` block; the ones relocated to bind paths must NOT be declared there.
    _stg_named_decl = "".join(
        f"\n  {n}:" for n in ("postgres_data", "nextcloud_data", "app_data")
        if not _stg_src[n].startswith("/"))
    nc_port = deploy.get("nextcloud_port", 8080)
    matrix_port = deploy.get("matrix_port", 8008)
    voice_port = deploy.get("voice_port", 8301)
    core = deploy.get("lucid_cove_path") or deploy.get("cove_core_path") or "../cove-core"
    # Shared-Caddy (Haven) per-service network attach. Defined here (early) because the
    # dendrite + voice service blocks below interpolate it. Lists BOTH default and the
    # external lucidcove-net (naming any network disables Compose's implicit default).
    svc_nets = ("\n    networks:\n      - default\n      - " + netconfig.SHARED_NET) if shared_net else ""
    dendrite_db_mount = (
        "\n      - ./docker/init-dendrite-db.sql:/docker-entrypoint-initdb.d/03-dendrite.sql:ro"
        if matrix_on else "")
    dendrite_services = (f"""
  dendrite-init:
    image: matrixdotorg/dendrite-monolith@sha256:7dafe6edfc8cfab758a68a4cf20414df1ade4a36b45b1852554d81fb70b1272c   # pinned by DIGEST (Dendrite 0.15.2, immutable) — supply-chain safe, avoids latest-drift
    container_name: {cid}-dendrite-init
    # Whole script as ONE arg to sh -c (a string `command:` here gets tokenized by
    # Compose and the shell would run bare `test` → exit 1). Generates the ed25519
    # signing key on first boot; no-op once it exists (key persists in the volume).
    entrypoint: ["/bin/sh", "-c", "test -f /var/dendrite/matrix_key.pem || /usr/bin/generate-keys --private-key /var/dendrite/matrix_key.pem"]
    restart: "no"
    volumes:
      - dendrite_data:/var/dendrite

  dendrite:
    image: matrixdotorg/dendrite-monolith@sha256:7dafe6edfc8cfab758a68a4cf20414df1ade4a36b45b1852554d81fb70b1272c   # pinned by DIGEST (Dendrite 0.15.2, immutable) — supply-chain safe, avoids latest-drift
    container_name: {cid}-dendrite
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      dendrite-init:
        condition: service_completed_successfully
    volumes:
      - ./docker/dendrite.yaml:/etc/dendrite/dendrite.yaml:ro
      - dendrite_data:/var/dendrite
    ports:
      - "{bind}{matrix_port}:8008"{svc_nets}
""" if matrix_on else "")
    dendrite_volume = "\n  dendrite_data:" if matrix_on else ""
    # Pre-flip testing: mount ltp-core source so `import lucid_tuner_protocol`
    # resolves without PyPI (its deps httpx+cryptography are already in the image).
    # Leave ltp_core_path unset in production — there cove-core deps on the
    # published lucid-tuner-protocol package.
    _ltp = (deploy.get("ltp_core_path") or "").strip()
    ltp_mount = f"\n      - {_ltp}/src:/opt/ltp-core-src:ro" if _ltp else ""
    ltp_env = "\n      PYTHONPATH: /opt/ltp-core-src" if _ltp else ""
    # Sites: canonical durable workspace for website repos (GitHub → Cloudflare Pages).
    # Mounts the sites subdir of app_data to /sites so container recreates don't lose repos.
    sites_mount = f"\n      - {_stg_src['app_data']}/sites:/sites"
    # Local CPU voice (jules dictation + Piper TTS). faster-whisper downloads its STT
    # model on first boot; voice_cache persists it across recreates. The browser reaches
    # voice on this published port (same host as the app); the app's transcribe proxy
    # reaches it in-network at http://{cid}-voice:8300.
    # GPU voice variant (#206): when the host has a GPU, build the CUDA image with Qwen3-ASR
    # and pass the GPU through, so batch video transcription runs on THIS Cove's own repo
    # container (retires the hand-built host pipecat). Models self-download into voice_cache
    # on first use — no host model mount, stays replicable. CPU otherwise (Whisper/cloud).
    if voice_gpu:
        _v_dockerfile, _v_image, _v_asr = "Dockerfile.gpu", "lucid-cove-voice:gpu", "qwen"
        _v_asr_env = "\n      ASR_ENGINE: qwen"
        _v_nvidia_env = ("\n      NVIDIA_VISIBLE_DEVICES: all"
                         "\n      NVIDIA_DRIVER_CAPABILITIES: compute,utility")
        _v_gpu_runtime = ("\n    runtime: nvidia"
                          "\n    deploy:"
                          "\n      resources:"
                          "\n        reservations:"
                          "\n          devices:"
                          "\n            - driver: nvidia"
                          "\n              count: all"
                          "\n              capabilities: [gpu]")
    else:
        _v_dockerfile, _v_image = "Dockerfile.cpu", "lucid-cove-voice:cpu"
        _v_asr_env = "\n      ASR_ENGINE: whisper\n      WHISPER_MODEL: small"
        _v_nvidia_env = ""
        _v_gpu_runtime = ""
    voice_services = (f"""
  voice:
    build:
      context: {core}/voice
      dockerfile: {_v_dockerfile}
    image: {_v_image}
    container_name: {cid}-voice
    restart: unless-stopped{_v_gpu_runtime}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      NEXTCLOUD_URL: http://{cid}-nextcloud:80
      NEXTCLOUD_USER: ${{NC_ADMIN_USER}}
      NEXTCLOUD_PASSWORD: ${{NC_ADMIN_PASSWORD}}
      # GPU-share gate (CF-87/CF-78): batch transcription requires the Cove's own
      # app secret OR a verified renter grant — fresh installs no longer ship open.
      PIPECAT_INTERNAL_SECRET: ${{PIPECAT_INTERNAL_SECRET:-}}
      GPU_GRANT_VERIFY_URL: http://{cid}-app:{app_port}{_v_asr_env}{_v_nvidia_env}
    volumes:
      - voice_cache:/root/.cache
    ports:
      - "{bind}{voice_port}:8300"{svc_nets}
""" if voice_local else "")
    voice_volume = "\n  voice_cache:" if voice_local else ""
    # App-side voice wiring: internal transcribe-proxy target + the published port the
    # browser uses to build the same-host voice URL when the Cove has no domain.
    voice_env = (f"\n      VOICE_INTERNAL_URL: http://{cid}-voice:8300"
                 f"\n      VOICE_PORT: \"{voice_port}\"") if voice_local else ""
    # Bundled Caddy (self-host with a domain): the Cove ships its own HTTPS terminator
    # so a self-hoster gets automatic Let's Encrypt without running/configuring Caddy
    # or holding our Cloudflare token. It routes to the compose service names and gets
    # the cert via acme-dns DNS-01 (Caddyfile written by generate_cove). #208/acme-dns.
    caddy_services = (f"""
  caddy:
    build:
      context: {core}/docker/caddy
      dockerfile: Dockerfile
    image: lucid-cove-caddy:latest
    container_name: {cid}-caddy
    restart: unless-stopped
    depends_on:
      - app
    environment:
      # #D35: same token that gates the admin proxy — the app authenticates its
      # /load with it. Empty default = gate off (the #D32 bridge admin is used).
      LP_CADDY_ADMIN_TOKEN: ${{LP_CADDY_ADMIN_TOKEN:-}}
    volumes:
      - ./docker/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    ports:
      - "80:80"
      - "443:443"
""" if bundle_caddy else "")
    caddy_volume = "\n  caddy_data:\n  caddy_config:" if bundle_caddy else ""
    # When the Cove ships its own Caddy, EVERYTHING the browser reaches goes through it
    # (the only published ports are Caddy's 80/443; the inner services bind to 127.0.0.1
    # via `bind`). So: (a) Nextcloud must trust whatever cloud.{domain} the operator later
    # claims in-browser — since the domain is unknown at provision (domainless first-run),
    # trust `*`; this is safe because NC is unreachable except via Caddy, which only routes
    # known hosts. (b) the app gets the hooks to drive Caddy live when the address is set:
    # the admin API URL, an rw mount of ./docker so it can persist the rendered Caddyfile
    # (inode-safe truncate-write), and a flag that switches domain.py to the live path.
    nc_trusted = (f'"{cid}-nextcloud localhost * ${{NEXTCLOUD_TRUSTED_DOMAIN}}"'
                  if bundle_caddy
                  else f'"{cid}-nextcloud localhost ${{NEXTCLOUD_TRUSTED_DOMAIN}}"')
    _mesh_ip = (deploy.get("mesh_ip") or "").strip()
    caddy_app_env = ("\n      COVE_BUNDLED_CADDY: \"1\""
                     "\n      COVE_CADDY_ADMIN: http://caddy:2019"
                     "\n      COVE_DOCKER_DIR: /app/cove-docker"
                     # Mesh-first: the in-browser claim points DNS at this mesh IP so the
                     # Cove is reached over the mesh, not the public internet (no ports).
                     + (f"\n      COVE_MESH_IP: \"{_mesh_ip}\"" if _mesh_ip else "")
                     ) if bundle_caddy else ""
    caddy_app_vol = "\n      - ./docker:/app/cove-docker   # rw: app rewrites Caddyfile on address change" if bundle_caddy else ""
    # Shared-Caddy (Haven) wiring. When shared_net is on, the browser-facing services join
    # the external lucidcove-net so the ONE shared Caddy routes to them by container name.
    # The per-service block lists BOTH the default network and lucidcove-net (naming any
    # network disables Compose's implicit default-attach, so we keep `default` explicit).
    # The app additionally learns it's on a shared box so its in-browser address claim
    # writes to the shared Caddy (admin API over the bridge) instead of a bundled one.
    shared_net_decl = (f"\n  {netconfig.SHARED_NET}:\n    external: true" if shared_net else "")
    # The app writes its own conf.d/{cid}.caddy into the SHARED conf.d (bind-mounted rw from
    # the host's ~/.lucidcove/caddy/conf.d) then triggers a live reload by POSTing the base
    # Caddyfile (which `import`s conf.d/*.caddy) to the shared Caddy admin API over the
    # bridge — so the claim runs entirely in-app, no docker socket, no host command.
    # NOTE: COVE_ID is already emitted in the main app env block below — do NOT repeat it
    # here (a duplicate mapping key makes `docker compose` reject the file).
    shared_app_env = (("\n      COVE_SHARED_CADDY: \"1\""
                       f"\n      COVE_CADDY_ADMIN: {netconfig.SHARED_CADDY_ADMIN_IN_CONTAINER}"
                       "\n      COVE_SHARED_CONFD: /app/shared-caddy-confd"
                       "\n      LP_TUNE_LOCK_DIR: /tune-lock"
                       + (f"\n      COVE_MESH_IP: \"{_mesh_ip}\"" if _mesh_ip else ""))
                      if shared_net else "")
    # Cross-Cove tuning lock (multi-Cove-per-machine / Haven-on-one-box): a shared host
    # dir mounted into every co-located Cove so they serialize their tuning against the one
    # local Ollama instead of thrashing it. Same ${HOME}/.lucidcove/ pattern as the shared
    # Caddy; a lone Cove (no shared_net) doesn't get it and doesn't need it.
    shared_app_vol = ("\n      - ${HOME}/.lucidcove/caddy/conf.d:/app/shared-caddy-confd   # rw: app writes its routing snippet"
                      "\n      - ${HOME}/.lucidcove/tune-lock:/tune-lock   # rw: cross-Cove tuning lock"
                      if shared_net else "")
    if shared_net:
        caddy_app_env = ""
        caddy_app_vol = ""
        # Nextcloud must trust whatever cloud.{domain} is claimed later (reachable only via
        # the shared Caddy, which routes known hosts) — same reasoning as the bundled case.
        nc_trusted = f'"{cid}-nextcloud localhost * ${{NEXTCLOUD_TRUSTED_DOMAIN}}"'
    # #211 — NC behind Caddy's TLS termination: tell Nextcloud its PUBLIC scheme/host so the
    # desktop Login Flow hands back an https:// callback. Without this NC sees plain http
    # internally and returns an http:// server URL, which the client rejects ("returned
    # server URL does not start with HTTPS"). Only emit when a domain (=https via Caddy) is
    # in front; a domainless localhost install is reached over plain http and must NOT force
    # https. (Bundled/shared-Caddy domains claimed in-browser later are handled in set_domain.)
    nc_overwrite = (
        "\n      OVERWRITEPROTOCOL: https"
        "\n      OVERWRITEHOST: ${NEXTCLOUD_TRUSTED_DOMAIN}"
        "\n      OVERWRITECLIURL: ${NEXTCLOUD_PUBLIC_URL}"
        "\n      TRUSTED_PROXIES: ${NEXTCLOUD_TRUSTED_PROXIES}"
    ) if (cove.get("domain") or "").strip() else ""
    return f'''# Generated by provision/centralized.py — {cove["name"]} (centralized single-stack).
# Copy ../.env.example handling is automatic: secrets are in .env (this folder).
name: {cid}

services:
  postgres:
    image: pgvector/pgvector:pg16@sha256:7d400e340efb42f4d8c9c12c6427adb253f726881a9985d2a471bf0eed824dff
    container_name: {cid}-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${{POSTGRES_USER}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
      POSTGRES_DB: ${{POSTGRES_DB}}
    volumes:
      - {_stg_src["postgres_data"]}:/var/lib/postgresql/data
      - {core}/docker/init-base.sql:/docker-entrypoint-initdb.d/00-base.sql:ro
      - ./docker/init-nextcloud-db.sql:/docker-entrypoint-initdb.d/01-nextcloud.sql:ro
      - ./docker/operator-seed.sql:/docker-entrypoint-initdb.d/02-operator-seed.sql:ro{dendrite_db_mount}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${{POSTGRES_USER}} -d ${{POSTGRES_DB}}"]
      interval: 10s
      timeout: 5s
      retries: 5

  nextcloud:
    image: nextcloud:29-apache@sha256:a7fbfcd4759bdd19b8fb8b1044b47ee3a9471d2e2c8bc68d56a2e671f86cebd2
    container_name: {cid}-nextcloud
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_DB: nextcloud
      POSTGRES_USER: nextcloud
      POSTGRES_PASSWORD: ${{NC_DB_PASSWORD}}
      NEXTCLOUD_ADMIN_USER: ${{NC_ADMIN_USER}}
      NEXTCLOUD_ADMIN_PASSWORD: ${{NC_ADMIN_PASSWORD}}
      NEXTCLOUD_TRUSTED_DOMAINS: {nc_trusted}{nc_overwrite}
      REDIS_HOST: redis
      PHP_MEMORY_LIMIT: 512M
      PHP_UPLOAD_LIMIT: 512M
    volumes:
      - {_stg_src["nextcloud_data"]}:/var/www/html
      - ./docker/nc-hooks:/docker-entrypoint-hooks.d:ro
    ports:
      - "{bind}{nc_port}:80"{svc_nets}

  redis:
    image: redis:7-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
    container_name: {cid}-redis
    restart: unless-stopped
    volumes:
      - redis_data:/data

  app:
    build:
      context: {core}
      dockerfile: docker/Dockerfile
    image: lucid-cove:latest
    container_name: {cid}-app
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    user: "1000:1000"
    env_file:
      - .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      DATABASE_URL: postgresql://${{POSTGRES_USER}}:${{POSTGRES_PASSWORD}}@postgres:5432/${{POSTGRES_DB}}
      COVE_MODE: multi
      COVE_ID: {cid}
      COVE_NAME: {cove["name"]}
      NEXTCLOUD_URL: http://{cid}-nextcloud:80
      NEXTCLOUD_ADMIN_USER: ${{NC_ADMIN_USER}}
      NEXTCLOUD_ADMIN_PASSWORD: ${{NC_ADMIN_PASSWORD}}
      OLLAMA_BASE_URL: ${{OLLAMA_BASE_URL:-http://host.docker.internal:11434}}
      LP_CADDY_ADMIN_TOKEN: ${{LP_CADDY_ADMIN_TOKEN:-}}
      PORT: {app_port}{voice_env}{ltp_env}{caddy_app_env}{shared_app_env}
    volumes:
      - {core}:/cove-core:ro
      - ./config:/app/config   # rw: runtime settings (domain, compute) persist to cove.yaml
      - {_stg_src["app_data"]}:/app/data{sites_mount}{ltp_mount}{caddy_app_vol}{shared_app_vol}
    ports:
      - "{bind}{app_port}:{app_port}"{svc_nets}
{voice_services}{dendrite_services}{caddy_services}
volumes:{_stg_named_decl}
  redis_data:{voice_volume}{dendrite_volume}{caddy_volume}
networks:
  default:{shared_net_decl}
'''


def build_env(cove: dict, op: dict, providers: list, ltp: dict, mx: dict, deploy: dict,
              ollama_url: str = LOCAL_OLLAMA_URL, referred_by: str = "",
              operator_token: str = "") -> str:
    cid = cove["id"]
    domain = cove.get("domain", "") or ""
    # A domainless (localhost) self-host install still needs a browser-reachable NC
    # URL or Files/Calendar have nowhere to point. Fall back to the published port.
    nc_port = deploy.get("nextcloud_port", 8080)
    nc_trusted = ("cloud." + domain) if domain else "localhost"
    nc_public = ("https://cloud." + domain) if domain else f"http://localhost:{nc_port}"
    lines = [
        f"# .env — {cove['name']} Cove secrets + config. NEVER commit this file.",
        "",
        "# ── Core ──",
        f"COVE_ID={cid}",
        f"COVE_NAME={cove['name']}",
        "COVE_MODE=multi",
        # Cove timezone for readers outside the config cascade (entrypoint log
        # day-splitting; legacy env readers). Config (agent.yaml instance.timezone)
        # stays authoritative for app code via time_utils.app_tz().
        f"APP_TIMEZONE={cove.get('timezone') or 'America/New_York'}",
        # Published host ports, so runtime surfaces (e.g. the co-located host-Caddy
        # command in domain.py) report the REAL ports when preflight bumped them
        # (second Cove on a box) instead of falling back to 8080/8008.
        f"NEXTCLOUD_PORT={deploy.get('nextcloud_port', 8080)}",
        f"MATRIX_PORT={deploy.get('matrix_port', 8008)}",
        # Host path of this Cove's folder (mesh-step UI copy-paste; empty = unknown).
        f"COVE_HOST_DIR={deploy.get('_host_dir', '')}",
        f"POSTGRES_USER={cid}",
        f"POSTGRES_PASSWORD={gen_secret()}",
        f"POSTGRES_DB={cid}_cove",
        "",
        "# ── Nextcloud ──",
        f"NC_ADMIN_USER=admin{cid}",   # instance admin, namespaced per Cove (operators use their @handle)
        f"NC_ADMIN_PASSWORD={gen_secret()}",
        f"NC_DB_PASSWORD={gen_secret()}",
        f"NEXTCLOUD_TRUSTED_DOMAIN={nc_trusted}",
        f"NEXTCLOUD_PUBLIC_URL={nc_public}",
        "# Proxy NC trusts for X-Forwarded-* so overwriteprotocol/host apply (Docker bridge",
        "# range covers Caddy->NC). Only consumed when a domain (https) is in front.",
        f"NEXTCLOUD_TRUSTED_PROXIES={'172.16.0.0/12' if domain else ''}",
        "# Pipecat (the stateless WebDAV video processor / GPU ASR) reaches THIS Cove's",
        "# NC here. When the GPU pipecat is OFF this Cove's docker network (the founder",
        "# borrowing the host pipecat; a VPS Cove borrowing the P620), the in-network",
        "# container name won't resolve — it needs the public / host-reachable URL.",
        f"NC_PIPECAT_URL={nc_public if domain else f'http://host.docker.internal:{nc_port}'}",
        "",
        "# ── GPU-share / voice gate (CF-87/CF-78) ──",
        "# The app sends this as X-Pipecat-Secret on its own batch-transcription jobs;",
        "# the voice container requires it (or a verified renter grant token) once set.",
        "# Minted per install so fresh GPU installs never ship an OPEN endpoint.",
        f"PIPECAT_INTERNAL_SECRET={gen_secret(32)}",
        "",
        "# ── Inter-service / network (#172) ──",
        "# SHARED_CONTAINER_SECRET inherits the NETWORK secret from the provisioner env",
        "# when present (so a fleet Cove can call the hub + Socrates marketplace); a",
        "# standalone self-host with none set gets a fresh random one. (Scoped per-call",
        "# secrets are the later hardening — #96.)",
        f"SHARED_CONTAINER_SECRET={os.getenv('SHARED_CONTAINER_SECRET') or gen_secret(32)}",
        "# Marketplace API (Socrates) the Cove's Market proxies to. PUBLIC URL so a",
        "# remote/co-located Cove reaches it (never a VPS-internal container name).",
        f"MARKETPLACE_API_URL={os.getenv('LP_MARKETPLACE_PUBLIC_URL') or 'https://api.lucidcove.org'}",
        "",
        "# ── Hub registrar (#133) / canonical identity (#163) ──",
        "# Set these on fleet Coves so they register (global name + @handle uniqueness)",
        "# and can join a Haven. Leave blank on a standalone self-host not joining one.",
        "# The hub itself additionally sets LP_REGISTRY_MASTER=true.",
        "# Inherited from the provisioner's environment so a fleet Cove is registry-",
        "# connected at runtime (not just at provision time).",
        f"LP_REGISTRY_URL={os.getenv('LP_REGISTRY_URL') or 'https://app.lucidcove.org'}",
        f"LP_REGISTRY_SECRET={os.getenv('LP_REGISTRY_SECRET', '')}",
        "# Self-host network identity: the operator's app-account token authenticates",
        "# registry writes (you can only claim your OWN @handle) — used when there's no",
        "# fleet secret, i.e. a stranger self-host. Plus the affiliate edge carried from",
        "# signup/install so the operator who referred you gets credit (set-once on the hub).",
        f"LP_OPERATOR_TOKEN={operator_token or os.getenv('LP_OPERATOR_TOKEN', '')}",
        f"LP_REFERRED_BY={referred_by or os.getenv('LP_REFERRED_BY', '')}",
        "",
        "# ── Dev loop / GitHub (Companion D) ──",
        "# The team's PR loop (create_github_pr, git_push, and the PR-review diff",
        "# card / ops-visibility GITHUB column) authenticates with this PAT. Seeded",
        "# from the provisioner env so every Cove is BORN with the loop wired; blank",
        "# on a standalone self-host until the operator adds a scoped push PAT. Use a",
        "# non-admin, branch-protected-main PAT — agents can only open PRs, never merge.",
        f"GH_TOKEN={os.getenv('GH_TOKEN', '')}",
        "",
        "# ── Models (fill in the keys for the providers you wired) ──",
        "# OLLAMA_BASE_URL: a VPS Cove offloads to the P620 GPU over the mesh (#143);",
        "# a P620/standalone Cove uses its own host's Ollama. Override in compute.ollama_url.",
        f"OLLAMA_BASE_URL={ollama_url}",
        "# The GUIDED cove-creation tour (naming, wake, discovery) runs on the SPARK, which",
        "# the hub serves (POST /api/registry/spark) using LP's key + this Cove's operator",
        "# token. The key lives ONLY on the hub — never baked into a Cove, never in the repo.",
        "# (Optional local override for dev: set LP_GUIDED_OPENROUTER_KEY in this Cove's env",
        "# by hand; src/models/spark.py prefers it over the hub when present.)",
    ]
    for p in providers:
        env = MODEL_KEY_ENV.get(p)
        if env:
            lines.append(f"{env}=")   # blank — the operator adds their own in the MC
    lines += [
        "",
        "# ── LTP ──",
        # A new Cove TUNES FOR REAL by default: LTP_DRY_RUN=false means echoes are
        # stored (false = "not a dry run"). Tuning only stops persisting if the
        # operator explicitly sets ltp.dry_run: true in their config.
        f"LTP_DRY_RUN={'true' if ltp.get('dry_run', False) else 'false'}",
        f"LP_KB_MANIFEST_URL=https://drop.lucidprinciples.com/kb/manifest.json",
        # CF-6: the KB Drop verify-key ships as a baked default in env.py
        # (LP_KB_PUBLIC_KEY). Don't emit an empty override here — an explicit
        # blank in .env would clobber that default and refuse KB sync forever.
        # An operator-supplied ltp.kb_public_key is appended below when present.
        f"TUNING_FAMILY={cid}",
        "# Public Drop — the universal daily tuning every Cove subscribes to (human",
        "# side always shows it; team/Presence agents derive via archetype). Slider off.",
        f"LTP_DROP_ENABLED={'true' if ltp.get('drop_enabled', True) else 'false'}",
        "LTP_DROP_URL=https://drop.lucidprinciples.com",
    ]
    # CF-6: only override the baked KB verify-key when the operator explicitly set one.
    _kb_key = (ltp.get("kb_public_key") or "").strip()
    if _kb_key:
        lines.append(f"LP_KB_PUBLIC_KEY={_kb_key}")
    if mx and mx.get("enabled"):
        lines += [
            "",
            "# ── Connect / Matrix (this Cove's own homeserver) ──",
            "# Split-horizon: HUB_URL is server-side (cove-core → homeserver over the",
            "# compose net); PUBLIC_URL is browser-reachable (the matrix-js-sdk client).",
            f"MATRIX_SERVER_NAME={mx['server_name']}",
            f"MATRIX_HUB_URL={mx['internal_url']}",
            f"MATRIX_PUBLIC_URL={mx['public_url']}",
            f"MATRIX_REG_SECRET={mx['reg_secret']}",
        ]
    return "\n".join(lines) + "\n"


CONNECT_MESH_SH = r'''#!/usr/bin/env bash
# Connect THIS box to your Lucid Cove mesh, then point your address at it.
# Get your one-time join key in the Cove:  Start Here -> Connect -> Get join code.
# Then run:   bash connect-mesh.sh <join-key>
set -euo pipefail
KEY="${1:-}"
if [ -z "$KEY" ]; then
  echo "Usage: bash connect-mesh.sh <join-key>"
  echo "Get the key in your Cove: Start Here -> Connect -> Get join code."
  exit 1
fi
if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale isn't installed. Install it, then re-run this:"
  echo "  Mac/Windows: https://tailscale.com/download"
  echo "  Linux:       curl -fsSL https://tailscale.com/install.sh | sh"
  exit 1
fi
echo "Joining the mesh..."
tailscale up --login-server https://headscale.lucidcove.org --authkey "$KEY" \
  || sudo tailscale up --login-server https://headscale.lucidcove.org --authkey "$KEY"
MESH_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
if [ -z "$MESH_IP" ]; then
  echo "Joined, but the mesh IP isn't ready yet — wait a few seconds and re-run."
  exit 1
fi
echo "Mesh IP: $MESH_IP"
cd "$(dirname "$0")"
if grep -q '^COVE_MESH_IP=' .env 2>/dev/null; then
  sed -i.bak "s|^COVE_MESH_IP=.*|COVE_MESH_IP=$MESH_IP|" .env && rm -f .env.bak
else
  printf '\nCOVE_MESH_IP=%s\n' "$MESH_IP" >> .env
fi
echo "Restarting your Cove so it uses the mesh address..."
docker compose up -d
# Self-heal DNS at the fresh mesh IP: if an address was already claimed before the
# box joined the mesh, re-point its A records here (no-op when no address is set).
APP_PORT="$(grep -m1 -E '^[[:space:]]+PORT:' docker-compose.yml | grep -oE '[0-9]+' | head -1 || true)"
APP_PORT="${APP_PORT:-8200}"
echo "Reconciling your address at the new mesh IP..."
for i in $(seq 1 12); do
  sleep 5
  RES="$(curl -s -m 10 -X POST "http://127.0.0.1:${APP_PORT}/api/domain/reconcile-dns" 2>/dev/null || true)"
  if [ -n "$RES" ]; then
    echo "$RES" | grep -q '"ok"[: ]*true' && { echo "Address now points at the mesh."; break; }
    echo "$RES" | grep -q 'no address set' && { echo "No address claimed yet — claim it in the Cove next."; break; }
    [ "$i" = "12" ] && echo "Could not reconcile automatically — open the Cove and re-run Set Address."
  fi
done
echo ""
echo "Done — your box is on the mesh ($MESH_IP)."
echo "Now open your Cove and Claim your address; it will point at the mesh."
echo "Family devices join the same way — see MESH.md."
'''


def build_next_steps(cove: dict, deploy: dict, claim_url: str, team_on: bool, mx: dict = None) -> str:
    cid = cove["id"]
    domain = (cove.get("domain") or "").strip()
    matrix_port = deploy.get("matrix_port", 8008)
    target = (deploy.get("target") or "standalone").strip().lower()
    co_located = target in ("p620", "vps")
    if mx and mx.get("enabled"):
        if domain and co_located:
            connect_section = f'''
## Connect (this Cove's own Matrix homeserver — `{mx["server_name"]}`)
Co-located target **{target}** — networking is automatic: the provisioner installed
`docker/cove.caddy` (apex + cloud. + matrix. + *.wildcard, all → 127.0.0.1 ports) into
the host Caddy import dir and reloaded Caddy, and created the Cloudflare wildcard DNS.
If either step reported NOT done above (e.g. you generated this on the Mac, not the
host), copy `docker/cove.caddy` into the host Caddy import dir and run the DNS step.
The signing key is generated on first boot (`dendrite-init`); operators' Matrix
accounts auto-provision the first time they open Connect.
'''
        elif domain:
            connect_section = f'''
## Connect (this Cove's own Matrix homeserver — `{mx["server_name"]}`)
Self-host with a domain on the SHARED Caddy. The provisioner wrote this Cove's routing
snippet into the shared Caddy's conf.d (`~/.lucidcove/caddy/conf.d/{cid}.caddy` — apex,
cloud., matrix. + `.well-known`, voice., *.wildcard, all → this Cove's containers over
the `lucidcove-net` bridge) and best-effort reloaded it. If the reload reported NOT done
above (e.g. the shared Caddy wasn't up yet), it goes live the moment install.sh brings
the shared Caddy up. The signing key is generated on first boot (`dendrite-init`);
operators' Matrix accounts auto-provision the first time they open Connect.
'''
        else:
            connect_section = f'''
## Connect (this Cove's own Matrix homeserver — `{mx["server_name"]}`)
Domainless boot test: the homeserver is reachable at http://localhost:{matrix_port}
(federation is off without a domain — local Connect only). The signing key is
generated on first boot; operators' Matrix accounts auto-provision on first Connect.
'''
    else:
        connect_section = ""
    if not co_located:
        shared_section = f'''
## Shared Caddy (one per box — owns 80/443, routes EVERY Cove)
This box uses a SINGLE shared Caddy so MULTIPLE Coves can run side by side (each Cove is
Caddy-less and routed by container name over the `lucidcove-net` bridge) — which is what
lets Coves on the same box federate Matrix to each other. The provisioner generated the
shared-Caddy stack into `~/.lucidcove/caddy/` (compose + base `Caddyfile` that imports
`conf.d/*.caddy` + an empty `conf.d/`). `install.sh` creates the bridge and brings the
shared Caddy up. If you deploy by hand, do this ONCE per box BEFORE the Cove:
   ```
   docker network create lucidcove-net 2>/dev/null || true
   ( cd ~/.lucidcove/caddy && COVE_CORE={deploy.get("lucid_cove_path") or deploy.get("cove_core_path") or "../cove-core"} docker compose up -d --build )
   ```
Each Cove's routing snippet lands at `~/.lucidcove/caddy/conf.d/{cid}.caddy` — written by
the in-browser "Claim your address" step (live-reloaded over the bridge) or, as a fallback,
by `provision/set_domain.py --shared`.

## Join the mesh (headless / CLI fallback)
The Cove walks you through this in the browser (Set Address → step 1 mints the join
code). Working headless? Mint the join code from the Cove UI on any device, then run
on this box:
   ```
   bash ./connect-mesh.sh <join-key>
   ```
It joins the mesh AND self-heals this Cove's DNS records afterwards.
'''
    else:
        shared_section = ""
    return f'''# {cove["name"]} Cove — Deploy & Lifecycle

Generated centralized (single-stack) Cove. Target: **{deploy.get("target", "standalone")}**.
Team: **{"on (steward + build team)" if team_on else "off (solo — operator + personal agent)"}**.
{shared_section}
## Deploy
1. Make sure your lucid-cove clone is at `{deploy.get("lucid_cove_path") or deploy.get("cove_core_path") or "../cove-core"}` (the app mounts it).
2. Fill in model API keys in `.env` (the providers you wired).
3. From this folder:
   ```
   docker compose up -d --build
   ```
4. App: http://localhost:{cove["_app_port"]}  ·  Nextcloud: http://localhost:{deploy.get("nextcloud_port", 8080)}

The Postgres init runs cove-core's complete `init-base.sql` + the NC database — no
migrations needed; a fresh Cove boots the full schema.
{connect_section}
## Claim your Cove (founding operator)
The provisioner seeded you as the founding operator (born-owned Cove). Open your
claim link to sign in and run the setup wizard (create your Presence{", build your team" if team_on else ""}):

    {claim_url}

(If you reach the Cove on a different host/port, swap the host in that URL.)
Additional operators/presences are then added from the admin UI (copy-link invites).

## Lifecycle (debug → delete → repeat)
- Tear everything down INCLUDING data (for a clean re-test):
  ```
  docker compose down -v
  ```
- Rebuild from scratch: `docker compose up -d --build`

## Notes
- This is the centralized model (the product). Operators/presences live in ONE app,
  not separate containers.
- A real domain enables subdomain routing + Connect; leave `domain` blank for a
  local-only boot test.
'''


def generate_cove(cfg: dict, out_root: Path) -> dict:
    """Generate a complete, deploy-ready centralized Cove from a config dict, into
    out_root/<cove_id>-cove/. PURE of stdout — returns a result dict the caller
    formats (CLI prints it; the hosting trigger uses it programmatically). Raises
    ValueError on missing required fields.

    This is the single provisioning entry point shared by the CLI (main) and the
    host-with-us flow (provision_api), so a Cove ordered through Stripe is built by
    exactly the same engine as one a self-hoster runs from the command line (#143/#167).
    """
    cove = dict(cfg.get("cove", {}))
    op = dict(cfg.get("operator", {}))
    deploy = dict(cfg.get("deploy", {}))
    compute = dict(cfg.get("compute", {}))
    providers = cfg.get("model_providers", ["openrouter"])
    ltp = dict(cfg.get("ltp", {}))
    matrix = dict(cfg.get("matrix", {}))
    matrix_external = (matrix.get("external_homeserver") or "").strip()
    matrix_on = bool(matrix.get("enabled", True)) and not matrix_external
    team_on = str(cfg.get("team", "on")).strip().lower() in ("on", "true", "yes", "1")

    # From-scratch / "open wizard" install: the operator makes EVERYTHING up in the
    # browser (Cove name + @handle + agent), checked live against the hub — nothing is
    # hardcoded from a config or an account. We detect it from an explicit flag or simply
    # the absence of an operator handle, then seed PLACEHOLDERS so the claim link opens the
    # OPEN wizard: the handle placeholder matches the needs_username pattern
    # (^.+-[0-9a-f]{4}$) so agent-setup leaves the field editable (not readOnly), and the
    # person name is blank. The wizard already asks for the Cove name and finalize commits
    # + registers it. (Upgrade-via-app keeps a real seeded handle → prefilled + locked.)
    from_scratch = bool(cfg.get("from_scratch")) or not op.get("handle")
    if from_scratch:
        op["handle"] = (op.get("handle") or f"setup-{_secrets.token_hex(2)}")  # 4 hex → needs_username
        op["name"] = op.get("name") or ""                                       # open person field
        op.setdefault("email", "")
        cove["id"] = cove.get("id") or f"lucidcove-{_secrets.token_hex(8)}"       # 16 hex (1.8e19) — collision/brute-force safe at millions of Coves
        cove["name"] = cove.get("name") or "New Cove"                            # placeholder; wizard sets it
    if not cove.get("id") or not cove.get("name"):
        raise ValueError("cove.id and cove.name are required")
    if not op.get("handle"):
        raise ValueError("operator.handle is required (or set from_scratch: true to pick it in the wizard)")

    # #164 — port preflight. For co-located targets, bump any port already taken on
    # the machine to the next free one (a second Cove on one box, the founder Dendrite
    # on 8008, etc.). Self-host single-Cove keeps its static defaults. Write the
    # resolved ports back into deploy so every builder downstream uses them.
    target = (deploy.get("target") or "standalone").strip().lower()
    _ports = netconfig.preflight_ports(
        {"app": deploy.get("app_port", 8200),
         "nextcloud": deploy.get("nextcloud_port", 8080),
         "matrix": deploy.get("matrix_port", 8008),
         "voice": deploy.get("voice_port", 8301)},
        target)
    deploy["app_port"] = _ports["app"]
    deploy["nextcloud_port"] = _ports["nextcloud"]
    deploy["matrix_port"] = _ports["matrix"]
    deploy["voice_port"] = _ports["voice"]
    # SHARED-Caddy (Haven) model for self-host: a standalone box runs ONE shared Caddy that
    # owns 80/443 and routes EVERY Cove (by container name over the external lucidcove-net
    # bridge). So a self-host Cove is now Caddy-LESS (no per-Cove 80/443 bind → multiple
    # Coves co-exist on one box → Matrix can federate between them). The shared Caddy is the
    # uniform path even for a single Cove (no special-casing the count). Domainless it serves
    # the MC on :80; the in-browser "claim address" step writes the Cove's conf.d snippet to
    # the shared Caddy and live-reloads it (admin API over the bridge) to full HTTPS. The only
    # address tier that ever needs a hand is "own domain, no DNS token" (records to paste).
    # Co-located (p620|vps) is UNCHANGED: it keeps the founder host Caddy (no shared net).
    shared_net = target not in netconfig.CO_LOCATED_TARGETS
    bundle_caddy = False   # retired for self-host — the shared Caddy replaces the bundled one
    # Publish inner ports on 127.0.0.1 only whenever a Caddy fronts everything (co-located
    # host Caddy OR the shared Caddy) — the public ports are the shared Caddy's 80/443. The
    # app port stays published on localhost so the first-run claim is reachable before any
    # domain (and the shared Caddy reaches the app over the bridge, not the host port).
    bind = "127.0.0.1:" if (target in netconfig.CO_LOCATED_TARGETS or shared_net) else ""
    # Mesh-first (default for a self-host): if this box is on the mesh and no IP was given,
    # auto-detect its mesh IP so the in-browser address claim points DNS at the mesh (not
    # the public IP). Family then reach the Cove over the mesh — no ports, no forwarding.
    if shared_net and not deploy.get("mesh_ip") and not deploy.get("host_ip"):
        _mip = _detect_mesh_ip()
        if _mip:
            deploy["mesh_ip"] = _mip
    # #143 — compute offload. A VPS Cove has no GPU; route its model calls to the
    # P620 Ollama over the mesh. P620/standalone use their own host's Ollama.
    ollama_url = resolve_ollama_url(target, compute)

    # GPU on the host (nvidia-smi). An explicit compute.gpu in the input config wins:
    # install.sh preflights the GPU on the HOST and passes the facts in, because this
    # provisioner usually runs inside a throwaway container where nvidia-smi can never
    # exist (same container-blindness as the port/mesh/timezone preflights). Only when
    # no host record was passed do we detect here (co-located host runs). The result
    # drives video_asr (local vs cloud) AND is recorded into cove.yaml compute.gpu so
    # the runtime machine-probe can size local model recommendations.
    _cfg_gpu = compute.get("gpu") if isinstance(compute.get("gpu"), dict) else {}
    _gpu_info = _cfg_gpu if _cfg_gpu.get("present") else _detect_gpu_info()
    compute["gpu"] = _gpu_info
    # A GPU must have real VRAM to serve local ASR / the CUDA voice image. A tiny old card
    # (e.g. a 2GB M620) is "present" but NOT capable — building the cu124 image on it OOMs the
    # host (SIGBUS). Capability, not mere presence, drives both video_asr and the voice image.
    _gpu_capable = bool(_gpu_info.get("present")
                        and int(_gpu_info.get("vram_mb") or 0) >= GPU_VOICE_MIN_VRAM_MB)
    # #181 — video ASR backend, detected from the machine at provision time
    # (onboarding checks the box). A GPU host transcribes video locally on its own
    # GPU; a light VPS Cove (no GPU by design) or a GPU-less host uses cloud ASR.
    # An explicit compute.video_asr in the input config always wins.
    if not (isinstance(compute.get("video_asr"), dict) and compute["video_asr"].get("mode")):
        if target == "vps":
            _asr_mode = "cloud"
        else:
            _asr_mode = "local" if _gpu_capable else "cloud"
        compute["video_asr"] = {"mode": _asr_mode}

    cove["_app_port"] = deploy["app_port"]
    cove["timezone"] = deploy.get("timezone") or _host_timezone()

    root = out_root / f"{cove['id']}-cove"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "docker").mkdir(parents=True, exist_ok=True)
    # (no presences/ dir — leftover from the per-agent-container model; nothing
    # in src reads the /presences mount, so it's no longer seeded or mounted)

    nc_db_pw = gen_secret()
    dendrite_db_pw = gen_secret()
    matrix_reg_secret = gen_secret(32)
    # Connect/Matrix runtime: own homeserver (default), an external one (advanced),
    # or none. mx feeds the .env split-horizon URLs; dendrite files are written below.
    if matrix_on:
        mx = matrix_runtime(cove, deploy, matrix_reg_secret)
    elif matrix_external:
        from urllib.parse import urlparse
        _host = urlparse(matrix_external).netloc or matrix_external
        mx = {"enabled": True, "server_name": _host, "internal_url": matrix_external,
              "public_url": matrix_external, "reg_secret": (matrix.get("reg_secret") or "")}
    else:
        mx = None
    # Founding operator (born-owned Cove) + claim link. tier drives has_team.
    pid = _uuid.uuid4()
    raw_token = _secrets.token_urlsafe(32)
    hashed_token = hashlib.sha256(raw_token.encode()).hexdigest()
    # #167 — tier ladder cleanup. A provisioned Cove always has an agent, so its tier
    # is "cove" whether or not the build team is on (team on/off is a Cove attribute,
    # not a separate tier). "presence" as a tier value is retired — Presence is the
    # internal portable operator+agent unit, not a tier.
    tier = "cove"
    # Voice backend mode (#205-voice): local CPU pipecat (default, ships in the compose),
    # external (point at another box's voice, e.g. P620), or off.
    _voice_cfg = compute.get("voice") if isinstance(compute.get("voice"), dict) else {}
    voice_mode = (_voice_cfg.get("mode") or "local").strip()
    voice_local = voice_mode == "local"
    # GPU voice container (#206): auto-on when a GPU was detected, so video transcription
    # runs on this Cove's own repo container. `compute.voice.gpu: true|false` forces it
    # (e.g. reserve the GPU for Ollama -> false = CPU voice even on a GPU host).
    _voice_gpu_pref = _voice_cfg.get("gpu")
    voice_gpu = bool(voice_local and (_voice_gpu_pref if isinstance(_voice_gpu_pref, bool)
                                      else _gpu_capable))
    # shared_net / bundle_caddy were computed up top (self-host now uses the shared Caddy).
    _domain = (cove.get("domain") or "").strip()
    (root / "docker-compose.yml").write_text(
        build_compose(cove, deploy, matrix_on, bind, voice_local,
                      bundle_caddy=bundle_caddy, shared_net=shared_net, voice_gpu=voice_gpu))
    (root / "config" / "agent.yaml").write_text(build_agent_yaml(cove, op, team_on))
    (root / "config" / "cove.yaml").write_text(build_cove_yaml(cove, op, compute, matrix_on=matrix_on))
    (root / "docker" / "init-nextcloud-db.sql").write_text(build_nc_db_sql(nc_db_pw))
    if matrix_on:
        # This Cove's own homeserver: dendrite.yaml + its Postgres db init. Caddy
        # snippet only when a real domain is set (federation/HTTPS); a domainless
        # boot test reaches Dendrite on the published matrix_port.
        _bot_ids = ["stuart", "agent", "lt"] + [t["id"] for t in STANDARD_TEAM]
        (root / "docker" / "dendrite.yaml").write_text(build_dendrite_config(
            server_name=mx["server_name"], db_password=dendrite_db_pw,
            registration_shared_secret=matrix_reg_secret, bot_user_ids=_bot_ids))
        (root / "docker" / "init-dendrite-db.sql").write_text(build_dendrite_db_sql(dendrite_db_pw))
    _nc_hook = root / "docker" / "nc-hooks" / "post-installation" / "20-apps.sh"
    _nc_hook.parent.mkdir(parents=True, exist_ok=True)
    _nc_hook.write_text(NC_APPS_HOOK)
    _nc_hook.chmod(0o755)
    (root / "docker" / "operator-seed.sql").write_text(build_operator_seed_sql(
        # Seed the operator's KNOWN display name (the config's operator.name — required,
        # so it's the operator's real chosen name, not a fake placeholder) so the
        # upgrade/self-host wizard PREFILLS + LOCKS it instead of re-asking (#202/#209a).
        # agent_name stays blank — the agent identity is set in the agent-setup step.
        # cove_name: NEVER seed the "New Cove" placeholder into accounts.last_name — it
        # leaks into every display resolver until the wizard finalizes (the recurring
        # "Stuart New Cove" wizard-preview bug, CF-89's DB-side sibling). Blank = the
        # readers fall through to their guarded sources; finalize writes the real name.
        pid=pid, name=op.get("name", ""), handle=op["handle"], email=op.get("email", ""),
        agent_name="",
        cove_name=("" if (cove["name"] or "").strip().lower() == "new cove" else cove["name"]),
        tier=tier, hashed_token=hashed_token))
    # Affiliate edge + the operator's network token flow into the Cove's .env so it can
    # register itself at runtime (finalize) via the operator-token path — the stranger
    # self-host case, no fleet secret needed.
    _referred_by = (cfg.get("affiliate", {}).get("referred_by")
                    or cfg.get("referred_by") or "").strip()
    # #12 — operator token for the hub (spark/persona + registry writes). A from-scratch
    # install mints it in the wizard's claim-operator step (placeholder handle). A PRESET
    # handle is seeded non-placeholder, so that step no-ops → no token → the hub spark 500s.
    # Mint it here so the preset case works untouched. An explicit operator.token (a
    # connecting upgrader who already has a hub account) always wins; from_scratch stays the
    # wizard's job; no hub configured → skip (a fully-private Cove has no shared namespace).
    _op_tok = (op.get("token") or "").strip()
    _op_mint = None
    if not _op_tok and not from_scratch and (os.getenv("LP_REGISTRY_URL", "") or "").strip():
        _op_mint = _claim_operator_via_hub(
            handle=op["handle"], name=op.get("name", ""),
            email=op.get("email", ""), referred_by=_referred_by)
        # Email already on the hub → it minted nothing. Retry without it: the @handle is the
        # identity and the token is the ownership proof (the operator can attach email later).
        if (not _op_mint.get("ok")) and _op_mint.get("code") == "email_exists" and op.get("email"):
            _op_mint = _claim_operator_via_hub(
                handle=op["handle"], name=op.get("name", ""),
                email="", referred_by=_referred_by)
        if _op_mint.get("ok"):
            _op_tok = (_op_mint.get("operator_token") or "").strip()
            if _op_tok:
                # Make it visible to the rest of THIS provision run — the DNS/cert hub calls
                # read LP_OPERATOR_TOKEN via _op_token().
                os.environ["LP_OPERATOR_TOKEN"] = _op_tok
    # Host path of this Cove's folder (where connect-mesh.sh lives), for the
    # mesh-step UI's copy-paste join one-liner (run-2 4.1/4.2 — no folder digging).
    # Only derivable on the install.sh path: cove_core_path is the HOST clone dir
    # (this provisioner usually runs in a container, so root.resolve() would lie).
    _core_host = (deploy.get("lucid_cove_path") or deploy.get("cove_core_path") or "").strip()
    if _core_host.startswith("/") and out_root.name == "out":
        deploy = dict(deploy)
        deploy["_host_dir"] = f"{_core_host}/out/{cove['id']}-cove"
    env_text = build_env(cove, op, providers, ltp, mx, deploy, ollama_url,
                         referred_by=_referred_by, operator_token=_op_tok)
    # keep the generated NC db password consistent between .env and the init sql
    env_text = env_text.replace("NC_DB_PASSWORD=" + env_text.split("NC_DB_PASSWORD=")[1].split("\n")[0],
                                "NC_DB_PASSWORD=" + nc_db_pw)
    (root / ".env").write_text(env_text)
    if _domain:
        claim_url = f"https://{op['handle']}.{_domain}/p/{raw_token}"
    else:
        # Domainless first-run: the shared Caddy has no route for this Cove yet (its conf.d
        # snippet is written when the operator claims an address), so the claim link uses the
        # Cove's own published app port on localhost. The claim then wires the shared Caddy.
        claim_url = f"http://localhost:{cove['_app_port']}/p/{raw_token}"

    # Caddy wiring. Three models:
    #   - co-located (p620|vps): per-Cove snippet → the FOUNDER host Caddy (host-net,
    #     127.0.0.1 ports). UNCHANGED.
    #   - shared (standalone, the new default): generate the ONE shared-Caddy stack into
    #     SHARED_CADDY_DIR (install.sh brings it up), and — when a domain is set — write
    #     this Cove's conf.d/{cid}.caddy snippet routing by container name over the bridge.
    _caddy_res = _dns_res = _acme_res = _dns_auto = _shared_caddy = None
    if shared_net:
        # Always emit the shared-Caddy stack files so install.sh can `docker compose up`
        # the one shared Caddy on the box. Idempotent: the base Caddyfile + empty conf.d
        # are only written if absent, so a SECOND Cove generated on this box doesn't clobber
        # the first Cove's already-installed routing snippet.
        # Write the bootstrap stack into the OUTPUT (it persists on the host via the
        # mounted clone). We must NOT write to ~/.lucidcove here: the provisioner runs in
        # a throwaway container where HOME=/tmp, so SHARED_CADDY_DIR would resolve inside
        # the container and be lost. install.sh copies this bootstrap to the host's real
        # ~/.lucidcove/caddy (once, never clobbering an existing conf.d).
        scd = root.parent / "_shared-caddy"
        confd = scd / "conf.d"
        confd.mkdir(parents=True, exist_ok=True)
        (scd / "docker-compose.yml").write_text(netconfig.build_shared_caddy_compose())
        base_caddyfile = scd / "Caddyfile"
        if not base_caddyfile.exists():
            base_caddyfile.write_text(netconfig.build_shared_caddy_base_caddyfile())
        _shared_caddy = {"dir": str(scd), "conf_d": str(confd), "snippet": None,
                         "host_dir": netconfig.SHARED_CADDY_DIR}
    if _domain and shared_net:
        # Domain + shared box: cert credentials (acme-dns for our subdomain; own token for
        # the operator's own domain) then the per-Cove conf.d snippet (container-name routes
        # over the bridge, per-site TLS). DNS auto-create runs (3-tier).
        _dns_auto = _auto_dns(_domain, deploy, cfg)
        _acme = {}
        if _domain == "lucidcove.org" or _domain.endswith(".lucidcove.org"):
            try:
                _acme_res = _load_acmedns()(_domain)
            except Exception as e:
                _acme_res = {"ok": False, "reason": f"acme-dns unavailable locally: {e}"}
            if not (isinstance(_acme_res, dict) and _acme_res.get("ok")):
                _acme_res = _acme_creds_via_hub(_domain)
            if isinstance(_acme_res, dict) and _acme_res.get("ok"):
                _acme = _acme_res.get("acmedns") or {}
        _dns = cfg.get("dns") or {}
        _snippet = netconfig.build_haven_cove_snippet(
            cove_id=cove["id"], domain=_domain, app_port=deploy["app_port"],
            matrix_server_name=(mx["server_name"] if mx else ""), matrix_on=bool(matrix_on),
            voice_on=voice_local, acmedns=_acme,
            own_dns_provider=(_dns.get("provider") or "").strip(),
            own_dns_token=(_dns.get("token") or "").strip())
        # Keep a copy in the Cove folder (reference / portability) and install into the
        # shared conf.d. Best-effort reload (the shared Caddy may not be up at provision).
        (root / "docker" / "cove.caddy").write_text(_snippet)
        _caddy_res = netconfig.install_haven_cove_snippet(_snippet, cove["id"])
        if _shared_caddy is not None:
            _shared_caddy["snippet"] = _caddy_res.get("path")
    elif _domain and target in netconfig.CO_LOCATED_TARGETS:
        # Co-located (founder host Caddy) — UNCHANGED.
        _snippet = netconfig.build_cove_caddy_snippet(
            cove_id=cove["id"], domain=_domain,
            app_port=deploy["app_port"], nextcloud_port=deploy["nextcloud_port"],
            matrix_port=deploy["matrix_port"],
            voice_port=(deploy.get("voice_port", 0) if voice_local else 0),
            matrix_server_name=(mx["server_name"] if mx else ""),
            matrix_on=bool(matrix_on))
        (root / "docker" / "cove.caddy").write_text(_snippet)
        _caddy_res = netconfig.install_caddy_snippet(
            _snippet, cove["id"],
            caddy_dir=deploy.get("caddy_dir", netconfig.DEFAULT_CADDY_DIR),
            caddy_container=deploy.get("caddy_container", netconfig.DEFAULT_CADDY_CONTAINER))
        _dns_res = netconfig.ensure_dns(_domain, deploy.get("mesh_ip", ""))

    # #133 — register this Cove with the Hub registrar (global name + @handle
    # uniqueness + federation facts). No-op unless LP_REGISTRY_URL/SECRET are set.
    # #169 — the affiliate referral edge (_referred_by, computed above) is carried to the
    # registrar so the operator who recruited this one gets credit. Validated + set-once.
    _reg_res = netconfig.register_cove_with_hub(
        cove_id=cove["id"], name=cove["name"], owner_handle=op["handle"],
        domain=_domain, homeserver=(mx["server_name"] if mx else ""),
        mesh_ip=deploy.get("mesh_ip", "") or (_dns_res.get("ip", "") if _dns_res else ""),
        referred_by=_referred_by)

    (root / "NEXT_STEPS.md").write_text(build_next_steps(cove, deploy, claim_url, team_on, mx))
    _cm = root / "connect-mesh.sh"
    _cm.write_text(CONNECT_MESH_SH)
    _cm.chmod(0o755)
    (root / ".gitignore").write_text(".env\ndata/\n*.sqlite\n")

    return {
        "root": root, "cove": cove, "deploy": deploy, "op": op,
        "team_on": team_on, "matrix_on": matrix_on, "mx": mx,
        "target": target, "bind": bind, "ollama_url": ollama_url,
        "claim_url": claim_url, "raw_token": raw_token, "operator_id": str(pid),
        "tier": tier, "domain": _domain,
        "ports": {"app": deploy["app_port"], "nextcloud": deploy["nextcloud_port"],
                  "matrix": deploy["matrix_port"]},
        "caddy": _caddy_res, "dns": _dns_res, "registry": _reg_res,
        "bundle_caddy": bundle_caddy, "acmedns": _acme_res, "dns_auto": _dns_auto,
        "shared_net": shared_net, "shared_caddy": _shared_caddy,
        "operator_mint": _op_mint,
    }


def _print_result(res: dict):
    """CLI formatting for a generate_cove() result."""
    root, cove, deploy, op = res["root"], res["cove"], res["deploy"], res["op"]
    target, mx = res["target"], res["mx"]
    _domain = res["domain"]
    print(f"\n  Generated centralized Cove: {root}  (team {'ON' if res['team_on'] else 'OFF'})")
    print(f"    docker-compose.yml, .env, config/cove.yaml, config/agent.yaml,")
    print(f"    docker/init-nextcloud-db.sql, docker/operator-seed.sql, NEXT_STEPS.md")
    if res["matrix_on"]:
        print(f"    docker/dendrite.yaml, docker/init-dendrite-db.sql  (Connect homeserver: {mx['server_name']})")
    print(f"    Models → Ollama at {res['ollama_url']}"
          + ("  (P620 GPU over the mesh — VPS compute offload, #143)" if target == "vps" else ""))
    if target in netconfig.CO_LOCATED_TARGETS:
        print(f"\n  Co-located target '{target}': ports bound to 127.0.0.1 "
              f"(app {deploy['app_port']} · nc {deploy['nextcloud_port']} · matrix {deploy['matrix_port']}).")
        _caddy_res, _dns_res = res["caddy"], res["dns"]
        if _caddy_res is not None:
            if _caddy_res.get("installed"):
                rl = "reloaded" if _caddy_res.get("reloaded") else f"NOT reloaded ({_caddy_res.get('reason')})"
                print(f"    Caddy snippet installed → {_caddy_res['path']}  ({rl})")
            else:
                print(f"    Caddy snippet NOT installed ({_caddy_res.get('reason')}). "
                      f"It's saved at docker/cove.caddy — drop it into the host Caddy import dir.")
        if _dns_res is not None:
            if _dns_res.get("ok"):
                print(f"    DNS ready ({_dns_res.get('ip')}): " + "; ".join(_dns_res.get("actions", [])))
            else:
                print(f"    DNS not auto-set ({_dns_res.get('reason')}). Point *.{_domain} at the mesh IP.")
    else:
        _sc = res.get("shared_caddy")
        if _sc is not None:
            print(f"\n  Shared Caddy (one per box, owns 80/443): {_sc.get('dir')}")
            print(f"    install.sh creates the {netconfig.SHARED_NET} bridge + brings this up.")
        if _domain:
            _da = res.get("dns_auto")
            if _da and _da.get("ok") and _da.get("auto"):
                print(f"  DNS auto-created ({_da.get('via')} → {_da.get('ip')}): "
                      + "; ".join(_da.get("actions", [])))
            elif _da and _da.get("records"):
                print(f"  ⚠ Add these DNS records at your registrar ({_da.get('reason')}):")
                for r in _da["records"]:
                    print(f"      {r['type']}   {r['name']}   →   {r['content']}")
            _cr = res.get("caddy")
            if _cr is not None:
                rl = "reloaded" if _cr.get("reloaded") else f"not reloaded ({_cr.get('reason')})"
                print(f"    Routing snippet → {_cr.get('path')}  ({rl})")
            print(f"  HTTPS is automatic via the shared Caddy once DNS resolves.")
    _reg_res = res["registry"]
    if _reg_res is not None and _reg_res.get("ok"):
        print(f"\n  Registered with the Hub registrar: {cove['name']} (@{op['handle']}).")
    elif _reg_res is not None and "skipping registry" not in (_reg_res.get("reason") or ""):
        print(f"\n  Hub registry NOT updated ({_reg_res.get('reason')}). Cove still boots; "
              f"register it later so it can join a Haven.")
    _mint = res.get("operator_mint")
    if _mint is not None:
        if _mint.get("ok"):
            print(f"\n  Operator token minted on the hub for @{op['handle']} → LP_OPERATOR_TOKEN "
                  f"in .env (the spark/persona authenticates with it).")
        else:
            print(f"\n  Operator token NOT minted ({_mint.get('reason')}). Cove still boots, but "
                  f"the hub spark (persona/wake) won't work until @{op['handle']} has a token — "
                  f"set operator.token in the config (an existing account's connect key) or "
                  f"free the handle.")
    print(f"\n  Next: fill model keys in .env, then `cd {root} && docker compose up -d --build`")
    print(f"\n  ★ CLAIM YOUR LUCID COVE (opens the setup wizard as the founding operator):")
    print(f"    {res['claim_url']}")
    print(f"    (replace localhost with the host you reach it on, if remote)\n")
    print(f"  Tear down for a clean re-test: docker compose down -v\n")


def main():
    ap = argparse.ArgumentParser(description="Generate a centralized (single-stack) Cove.")
    ap.add_argument("config", help="Path to your cove config yaml (see cove.config.example.yaml)")
    ap.add_argument("--output", help="Output dir (default: alongside the config)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}"); sys.exit(1)
    cfg = yaml.safe_load(cfg_path.read_text()) or {}

    out_root = Path(args.output) if args.output else cfg_path.parent
    try:
        res = generate_cove(cfg, out_root)
    except ValueError as e:
        print(f"ERROR: {e}"); sys.exit(1)
    _print_result(res)


if __name__ == "__main__":
    main()
