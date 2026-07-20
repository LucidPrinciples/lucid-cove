"""Centralized environment configuration (#65).

ONE place that knows every environment variable cove-core reads, its canonical
default, its type, and which deployment group it belongs to. Two jobs:

  1. Runtime source of truth — call sites read env vars through `env()` /
     `env_bool()` / `env_int()` / `env_float()` / `env_csv()` so a variable has
     exactly ONE default everywhere (no more "OLLAMA_BASE_URL defaults to
     localhost here but host.docker.internal there").

  2. Self-hoster contract — `render_env_example()` generates `.env.example`
     straight from this registry, so a new family gets the full, documented
     list of knobs in one file. The registry IS the inventory.

Adding a new env var? Add an `EnvVar(...)` to REGISTRY here, then read it with
`env("MY_VAR")`. Never call os.getenv directly in feature code.

Secrets (api keys, passwords, tokens) are marked `secret=True` — they render in
.env.example as an empty placeholder and are NEVER given a real default here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvVar:
    name: str
    default: str = ""
    type: str = "str"          # str | bool | int | float | csv | path
    group: str = "General"
    secret: bool = False
    required: bool = False
    desc: str = ""


# ── Registry ─────────────────────────────────────────────────────────────────
# Canonical default per variable. Where call sites historically disagreed, the
# chosen value is noted in `desc` as "(was: ...)" so the reconciliation is
# auditable.

REGISTRY: list[EnvVar] = [
    # ── Runtime / identity ──
    EnvVar("COVE_MODE", "single", "str", "Runtime",
           desc="single = one agent (Cove steward); multi = shared app (operators)."),
    EnvVar("COVE_ID", "", "str", "Runtime", desc="Stable id for this Cove."),
    EnvVar("COVE_NAME", "", "str", "Runtime",
           desc="Family/Cove display name. (was: '' vs 'name' — canonical '')."),
    EnvVar("AGENT_ID", "agent", "str", "Runtime",
           desc="Primary agent id; code may override via get_primary_agent_id()."),
    EnvVar("ENVIRONMENT", "production", "str", "Runtime", desc="production | development."),
    EnvVar("PORT", "8200", "int", "Runtime", desc="App listen port."),
    EnvVar("APP_TIMEZONE", "America/New_York", "str", "Runtime", desc="IANA tz for the Cove."),
    EnvVar("HOME", "/root", "path", "Runtime", desc="Process home dir."),
    EnvVar("OPERATOR_ACCOUNT_ID", "", "str", "Runtime", desc="Bound operator account (single-op Coves)."),
    EnvVar("OWNER_EMAIL", "", "str", "Runtime", desc="Cove owner email."),
    EnvVar("SKIP_AGENTS", "", "csv", "Runtime", desc="Agent ids to skip in team dispatch."),

    # ── Data / paths ──
    EnvVar("DATA_DIR", "/app/data", "path", "Paths", desc="Writable app data dir."),
    EnvVar("STUART_DATA_DIR", "/app/data", "path", "Paths",
           desc="Steward data dir. (was: '/app/data' vs '/data' — canonical '/app/data')."),
    EnvVar("VAULT_DIR", "/vault", "path", "Paths",
           desc="Mounted vault root. (was: '/vault' vs '/vault/LP-Vault' — canonical '/vault')."),
    EnvVar("FRAMEWORK_DIR", "/cove-core/data/knowledge-base", "path", "Paths", desc="Framework KB dir."),
    EnvVar("LP_AVATAR_DIR", "/app/data/avatars", "path", "Paths", desc="Generated avatar store."),
    EnvVar("RUNBOOKS_DIR", "/app/data/runbooks", "path", "Paths", desc="Live runbook JSON dir."),
    EnvVar("RUNBOOKS_SEED_DIR", "/cove-core/runbooks", "path", "Paths", desc="Seed runbook JSON dir."),
    EnvVar("VIDEO_BASE_PATH", "/vault/AgentSkills/Content/video", "path", "Paths", desc="Video pipeline root."),
    EnvVar(
        "TO_DELETE_NOTIFY_BYTES",
        str(100 * 1024 ** 3),
        "int",
        "Paths",
        desc=(
            "Daily Attention when AgentSkills/To-Delete + video/to-delete "
            "exceed this many bytes (default 100 GiB). Video originals are "
            "multi-GiB; keep well above one file so the guard is offload cue, "
            "not noise."
        ),
    ),
    EnvVar("SITES_NC_PATH", "", "path", "Paths", desc="Nextcloud-synced Sites path (optional)."),
    EnvVar("TUNING_PACKAGES_DIR", "/app/data/tuning-packages", "path", "Paths", desc="LTP package cache dir."),
    EnvVar("LT_REFERENCE_PATH", "", "path", "Paths", desc="Runtime tuning-key reference json (optional)."),
    EnvVar("SQLITE_PATH", "./data/checkpoints.db", "path", "Paths", desc="LangGraph checkpoint db."),

    # ── Database ──
    EnvVar("DATABASE_URL", "", "str", "Database", secret=True, desc="Primary Postgres DSN."),
    EnvVar("STEWARD_DATABASE_URL", "", "str", "Database", secret=True,
           desc="Steward DB DSN (team agents point here)."),
    EnvVar("MERCER_DATABASE_URL", "", "str", "Database", secret=True, desc="Mercer DB DSN."),

    # ── Inter-service auth / secrets ──
    EnvVar("SHARED_CONTAINER_SECRET", "", "str", "Secrets", secret=True,
           desc="Service-to-service + admin endpoint secret."),
    EnvVar("SHARED_CONTAINER_URL", "", "str", "Secrets", desc="URL of the shared container."),
    EnvVar("LP_OPERATOR_TOKEN", "", "str", "Secrets", secret=True, desc="Operator's app-account token (registry self-join)."),
    EnvVar("LP_REFERRED_BY", "", "str", "Secrets", desc="Affiliate handle that referred this install."),

    # ── Registry / Hub ──
    EnvVar("LP_REGISTRY_MASTER", "", "bool", "Registry",
           desc="Truthy ONLY on the hub/public app (the registrar). Empty on Coves."),
    EnvVar("LP_REGISTRY_URL", "", "str", "Registry", desc="Hub registrar base URL (Coves point here)."),
    EnvVar("LP_REGISTRY_SECRET", "", "str", "Registry", secret=True, desc="Fleet registry write secret."),
    EnvVar("LP_PUBLIC_BASE", "https://app.lucidcove.org", "str", "Registry", desc="Public app base URL."),
    EnvVar("MARKETPLACE_API_URL", "", "str", "Registry", desc="Marketplace/commerce API base (Socrates)."),
    EnvVar("LP_EXTRA_RESERVED_COVE_NAMES", "", "csv", "Registry",
           desc="Extra Cove/Haven names to reserve (anti-squat), comma-separated. "
                "Deployment-specific so no operator value is hardcoded in the core "
                "(e.g. a hub sets its own Haven name here)."),

    # ── Models / LLM ──
    EnvVar("OPENROUTER_API_KEY", "", "str", "Models", secret=True),
    EnvVar("LP_GUIDED_OPENROUTER_KEY", "", "str", "Models", secret=True,
           desc="LP-provided OpenRouter key scoped to the GUIDED cove-creation tour ONLY "
                "(flow_chat). Does NOT power an operator's normal agent — that needs their "
                "own key or Ollama. Lets a keyless self-hoster try Guided on LP's dime."),
    EnvVar("OPENROUTER_BASE_URL", "", "str", "Models", desc="Override OpenRouter base (optional)."),
    EnvVar("MOONSHOT_API_KEY", "", "str", "Models", secret=True),
    EnvVar("FEATHERLESS_API_KEY", "", "str", "Models", secret=True, desc="Featherless flat-rate subscription (GLM 5.2 etc.)."),
    EnvVar("GOOGLE_API_KEY", "", "str", "Models", secret=True),
    EnvVar("GROQ_API_KEY", "", "str", "Models", secret=True),
    EnvVar("OPENAI_API_KEY", "", "str", "Models", secret=True),
    EnvVar("DEEPGRAM_API_KEY", "", "str", "Models", secret=True, desc="Cloud ASR (Deepgram) for video transcription."),
    EnvVar("OLLAMA_BASE_URL", "http://host.docker.internal:11434", "str", "Models",
           desc="Local Ollama. (was: host.docker.internal vs localhost — canonical host.docker.internal)."),
    EnvVar("PRIMARY_MODEL", "unknown", "str", "Models", desc="Display label for the primary model."),
    EnvVar("FALLBACK_MODEL", "unknown", "str", "Models", desc="Display label for the fallback model."),

    # ── Nextcloud ──
    EnvVar("NEXTCLOUD_URL", "http://nextcloud:80", "str", "Nextcloud",
           desc="NC base URL. Canonical: http://nextcloud:80 (bundled NC service, internal port 80). Compose overrides per deploy."),
    EnvVar("NEXTCLOUD_USER", "", "str", "Nextcloud", desc="Per-presence NC user."),
    EnvVar("NEXTCLOUD_PASSWORD", "", "str", "Nextcloud", secret=True, desc="Per-presence NC app password."),
    EnvVar("NEXTCLOUD_ADMIN_USER", "admin", "str", "Nextcloud",
           desc="NC admin user. (was: '' vs 'admin' vs NEXTCLOUD_USER fallback — canonical 'admin')."),
    EnvVar("NEXTCLOUD_ADMIN_PASSWORD", "", "str", "Nextcloud", secret=True, desc="NC admin password."),
    EnvVar("NEXTCLOUD_PUBLIC_URL", "", "str", "Nextcloud", desc="Public NC URL (for share links)."),
    EnvVar("NC_PIPECAT_URL", "", "str", "Nextcloud", desc="NC URL pipecat uses for WebDAV."),

    # ── Matrix / Connect ──
    EnvVar("MATRIX_HOMESERVER", "", "str", "Matrix", desc="This Cove's homeserver URL."),
    EnvVar("MATRIX_SERVER_NAME", "", "str", "Matrix", desc="Matrix server_name (handle domain)."),
    EnvVar("MATRIX_PUBLIC_URL", "", "str", "Matrix", desc="Public client URL."),
    EnvVar("MATRIX_HUB_URL", "", "str", "Matrix", desc="Hub homeserver for cross-Cove resolution."),
    EnvVar("MATRIX_REG_SECRET", "", "str", "Matrix", secret=True, desc="Shared registration secret."),
    EnvVar("MATRIX_OPERATOR_USER", "", "str", "Matrix", desc="Operator's matrix localpart."),
    EnvVar("MATRIX_OPERATOR_PASSWORD", "", "str", "Matrix", secret=True, desc="Operator's matrix password."),
    EnvVar("MATRIX_STEWARD_LOCALPART", "steward", "str", "Matrix", desc="Steward matrix localpart."),

    # ── Voice / Video compute ──
    # PIPECAT_URL default is EMPTY on purpose: it's an explicit operator override. The
    # callers chain `env("PIPECAT_URL") or env("VOICE_INTERNAL_URL") or <legacy default>`,
    # and a non-empty registry default here short-circuits that chain — every repo-
    # provisioned Cove ended up dialing host:8300 (dead port; the provisioner publishes
    # voice per-Cove, e.g. 8301) instead of its own VOICE_INTERNAL_URL container URL.
    # Hit live on the nottington A5 transcribe test 2026-07-02 (founder-shape default).
    EnvVar("PIPECAT_URL", "", "str", "Voice", desc="Pipecat voice service (explicit override only)."),
    EnvVar("VOICE_INTERNAL_URL", "http://host.docker.internal:8300", "str", "Voice", desc="Internal voice URL."),
    EnvVar("VOICE_PUBLIC_URL", "", "str", "Voice", desc="Public wss voice URL (derived if empty)."),

    # ── Email / Brevo ──
    EnvVar("BREVO_API_KEY", "", "str", "Email", secret=True),
    EnvVar("BREVO_SENDER_EMAIL", "signin@lucidprinciples.com", "str", "Email", desc="Verified sender."),
    EnvVar("BREVO_SENDER_NAME", "Lucid Principles", "str", "Email", desc="Sender display name."),
    EnvVar("BREVO_LIST_ID", "4", "int", "Email", desc="Onboarding contact list id."),
    EnvVar("EMAIL_PRODUCT_NAME", "Lucid Principles", "str", "Email", desc="Product name in email copy."),

    # ── Commerce / affiliate ──
    EnvVar("LP_PLATFORM_FEE_RATE", "0.10", "float", "Commerce", desc="Marketplace platform fee."),
    EnvVar("LP_AFFILIATE_L1_RATE", "0.30", "float", "Commerce", desc="L1 affiliate share of net fee."),
    EnvVar("LP_AFFILIATE_L2_RATE", "0.10", "float", "Commerce", desc="L2 affiliate share of net fee."),

    # ── LTP / tuning ──
    EnvVar("LTP_DRY_RUN", "false", "bool", "LTP",
           desc="If true, tuning side effects are skipped. (was: 'false' vs 'true' — canonical 'false')."),
    EnvVar("LTP_SOURCE", "unknown", "str", "LTP", desc="Provenance label for tunings."),
    EnvVar("LTP_DROP_ENABLED", "true", "bool", "LTP", desc="Subscribe to the public LTP Drop."),
    EnvVar("LTP_DROP_URL", "https://drop.lucidprinciples.com", "str", "LTP", desc="LTP Drop base URL."),
    EnvVar("LTP_DROP_PUBKEY", "", "str", "LTP", desc="Drop signature public key."),
    EnvVar("TUNING_DELIVERY", "git", "str", "LTP", desc="git | http delivery of packages."),
    EnvVar("TUNING_FAMILY", "default", "str", "LTP", desc="Tuning family key."),
    EnvVar("TUNING_HTTP_URL", "", "str", "LTP", desc="HTTP package source (if delivery=http)."),
    EnvVar("TUNING_REPO_URL", "", "str", "LTP", desc="Git package source (if delivery=git)."),
    EnvVar("ACTIVE_MIRROR", "scripture-tpt", "str", "LTP", desc="Default mirror source key."),
    EnvVar("NOTIFY_CALENDAR", "personal", "str", "LTP", desc="Which calendar drives notifications."),

    # ── Knowledge base ──
    EnvVar("LP_KB_MANIFEST_URL", "https://drop.lucidprinciples.com/kb/manifest.json", "str", "KB",
           desc="Signed KB manifest."),
    # CF-6: this is the PUBLIC verify-key (safe to ship) for the hub's KB Drop, which
    # shares ONE signing identity with the LTP tuning Drop. Bake the real PEM as this
    # default so every fresh Cove can verify the KB manifest without per-Cove config.
    # Get the value from the hub: on the VPS, `cd /docker/ltp-drop && python3 -m
    # src.tools.kb_publisher --pubkey` (single-line PEM body or full PEM both accepted
    # by kb_sync). An operator may still override per-Cove via ltp.kb_public_key.
    EnvVar("LP_KB_PUBLIC_KEY",
           "MCowBQYDK2VwAyEAMtpeQ7Gae3YUwzUVjh2fyToF/oGi2OBWUWipXia0BeM=",
           "str", "KB", desc="KB Drop verify-key (public). Baked default; consume KB."),

    # ── Hosting / provisioner ──
    EnvVar("HOSTING_BASE_DOMAIN", "lucidcove.org", "str", "Hosting", desc="Base domain for provisioned Coves."),
    EnvVar("HOSTING_OUTPUT_DIR", "/hosting/coves", "path", "Hosting", desc="Where generated overlays land."),
    EnvVar("HOSTING_COVE_CORE_PATH", "/docker/cove-core", "path", "Hosting", desc="cove-core path on the host."),
    EnvVar("HOSTING_CADDY_DIR", "", "path", "Hosting", desc="Caddy config dir (optional auto-deploy)."),
    EnvVar("HOSTING_MESH_IP", "", "str", "Hosting", desc="Mesh IP for the provisioned Cove."),
    EnvVar("HOSTING_AUTO_DEPLOY", "", "bool", "Hosting", desc="Auto docker-compose after provision."),

    # ── Social: X / YouTube ──
    EnvVar("X_API_KEY", "", "str", "Social", secret=True),
    EnvVar("X_API_SECRET", "", "str", "Social", secret=True),
    EnvVar("X_ACCESS_TOKEN", "", "str", "Social", secret=True),
    EnvVar("X_ACCESS_TOKEN_SECRET", "", "str", "Social", secret=True),
    EnvVar("X_DRY_RUN", "false", "bool", "Social", desc="If true, X posts are logged not sent."),
    EnvVar("YOUTUBE_CLIENT_ID", "", "str", "Social", secret=True),
    EnvVar("YOUTUBE_CLIENT_SECRET", "", "str", "Social", secret=True),
    EnvVar("YOUTUBE_REDIRECT_URI", "", "str", "Social", desc="OAuth redirect URI."),

    # ── Backups / misc infra ──
    EnvVar("BACKUP_REPO_DIR", "/backup/agent", "path", "Infra", desc="Backup git repo dir."),
    EnvVar("BACKUP_GIT_NAME", "MC Backup", "str", "Infra", desc="Backup commit author name."),
    EnvVar("BACKUP_GIT_EMAIL", "backup@mc.internal", "str", "Infra", desc="Backup commit author email."),
    EnvVar("SEARXNG_URL", "http://localhost:8888", "str", "Infra", desc="Optional self-hosted search."),
]

_BY_NAME: dict[str, EnvVar] = {v.name: v for v in REGISTRY}

# Group display order for .env.example
_GROUP_ORDER = [
    "Runtime", "Paths", "Database", "Secrets", "Registry", "Models", "Nextcloud",
    "Matrix", "Voice", "Email", "Commerce", "LTP", "KB", "Hosting", "Social", "Infra",
]

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_UNSET = object()


# ── Accessors ────────────────────────────────────────────────────────────────

def _raw(name: str, override_default=_UNSET) -> str:
    spec = _BY_NAME.get(name)
    default = spec.default if (spec and override_default is _UNSET) else (
        "" if override_default is _UNSET else override_default)
    val = os.getenv(name)
    return val if val is not None and val != "" else default


def env(name: str, default=_UNSET) -> str:
    """Canonical string read. Registry default unless a default is passed."""
    return _raw(name, default)


def env_bool(name: str, default=_UNSET) -> bool:
    return str(_raw(name, default)).strip().lower() in _TRUE


def env_int(name: str, default=_UNSET) -> int:
    try:
        return int(str(_raw(name, default)).strip())
    except (TypeError, ValueError):
        spec = _BY_NAME.get(name)
        try:
            return int(spec.default) if spec and spec.default else 0
        except ValueError:
            return 0


def env_float(name: str, default=_UNSET) -> float:
    try:
        return float(str(_raw(name, default)).strip())
    except (TypeError, ValueError):
        spec = _BY_NAME.get(name)
        try:
            return float(spec.default) if spec and spec.default else 0.0
        except ValueError:
            return 0.0


def env_csv(name: str, default=_UNSET) -> list[str]:
    return [s.strip() for s in str(_raw(name, default)).split(",") if s.strip()]


# ── .env.example generation ──────────────────────────────────────────────────

def render_env_example() -> str:
    lines = [
        "# Lucid Cove — environment contract",
        "# Generated from src/env.py (the single source of truth). Do not hand-edit;",
        "# run:  python3 -m src.env > .env.example",
        "#",
        "# Secrets are blank placeholders — fill them in your private .env, never here.",
        "",
    ]
    by_group: dict[str, list[EnvVar]] = {}
    for v in REGISTRY:
        by_group.setdefault(v.group, []).append(v)
    ordered = _GROUP_ORDER + [g for g in by_group if g not in _GROUP_ORDER]
    for g in ordered:
        if g not in by_group:
            continue
        lines.append(f"# ── {g} " + "─" * max(2, 60 - len(g)))
        for v in by_group[g]:
            if v.desc:
                lines.append(f"# {v.desc}")
            if v.secret:
                lines.append(f"{v.name}=")
            elif v.required:
                lines.append(f"# REQUIRED\n{v.name}={v.default}")
            else:
                lines.append(f"{v.name}={v.default}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_env_example())
