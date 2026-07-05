"""Shared provisioning templates (#99) — single source of truth.

The two provisioning flows used to each carry their own copy of these template
strings:
  - src/utils/provision_overlay.py  (dashboard, single personal agent)
  - provision/provision.py          (CLI, full family bootstrap)

The bits that legitimately differ between the flows are passed as parameters,
so the generated output stays byte-for-byte identical — only the template text
moves to one place. Verified by tests/test_provisioner*.py.
"""
import textwrap

import yaml

# Base schema (00-base.sql) already owns projects/tasks/project_comments/
# task_history. overlay_tables=True additionally creates the tables the
# dashboard flow has historically written per-agent (kept for output parity;
# see backlog #99 — memory_entries belongs in the base schema, handled
# separately as its own migration).


def build_init_sql(*, title: str, description: str, agent_id: str,
                   agent_name: str, archetype: str, operator_id: str = "",
                   overlay_tables: bool = False) -> str:
    """Generate a personal agent's init.sql (base-schema include + agent seed)."""
    head = textwrap.dedent(f"""\
        -- =============================================================================
        -- {title} — Database Initialization
        -- =============================================================================
        -- {description}
        -- =============================================================================

        -- Base schema (shared tables)
        \\i /docker-entrypoint-initdb.d/00-base.sql

        -- ─── Agent seed ─────────────────────────────────────────────────────────────

        INSERT INTO agent_state (agent_id, display_name, archetype, status) VALUES
        ('{agent_id}', '{agent_name}', '{archetype}', 'active')
        ON CONFLICT (agent_id) DO NOTHING;
        """)

    if not overlay_tables:
        return head + textwrap.dedent("""
            -- Personal agents use simpler project/task tables from cove-core base.
            -- No project_comments or task_history needed for solo agents.
            """)

    return head + textwrap.dedent(f"""
        -- Projects table
        CREATE TABLE IF NOT EXISTS projects (
            id          SERIAL PRIMARY KEY,
            slug        TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            status      TEXT DEFAULT 'active',
            owner       TEXT DEFAULT '{operator_id}',
            team        TEXT[] DEFAULT '{{}}',
            goals       TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id               SERIAL PRIMARY KEY,
            project_id       INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            parent_task_id   INTEGER REFERENCES tasks(id),
            title            TEXT NOT NULL,
            description      TEXT,
            status           TEXT DEFAULT 'pending',
            priority         TEXT DEFAULT 'normal',
            assignee         TEXT DEFAULT '{agent_id}',
            due_date         DATE,
            completed_at     TIMESTAMPTZ,
            created_by       TEXT DEFAULT '{operator_id}',
            notes            TEXT,
            source           TEXT DEFAULT 'manual',
            nc_task_id       TEXT,
            workflow_pattern TEXT,
            workflow_state   TEXT,
            audit_verdict    TEXT,
            audit_count      INTEGER DEFAULT 0,
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            updated_at       TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
        CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS project_comments (
            id              SERIAL PRIMARY KEY,
            project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            task_id         INTEGER REFERENCES tasks(id),
            author          TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_comments_task ON project_comments(task_id) WHERE task_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS task_history (
            id            SERIAL PRIMARY KEY,
            task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            field_changed TEXT NOT NULL,
            old_value     TEXT,
            new_value     TEXT,
            changed_by    TEXT NOT NULL DEFAULT 'system',
            changed_at    TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);
        CREATE INDEX IF NOT EXISTS idx_task_history_time ON task_history(changed_at DESC);

        CREATE TABLE IF NOT EXISTS memory_entries (
            id          SERIAL PRIMARY KEY,
            key         TEXT NOT NULL UNIQUE,
            value       TEXT NOT NULL,
            category    TEXT DEFAULT 'general',
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)


# =============================================================================
# Model providers (#99 layer 2: Cove defaults; foundation for #121)
# =============================================================================
# Catalog of model providers a Cove can wire credentials for. Maps the provider
# name (as used in a family config's `model_providers` list) to its .env key.
# Ollama is always present (local base URL, not an API key) so it's not here.
# New Coves default to the lean set; the founder Cove lists all four.
MODEL_PROVIDERS = {
    "openrouter": "OPENROUTER_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
}
DEFAULT_MODEL_PROVIDERS = ("openrouter",)
FOUNDER_MODEL_PROVIDERS = ("moonshot", "google", "groq", "openrouter")


def model_env_keys(providers) -> str:
    """Return the `.env` API-key lines (``KEY=``) for the given provider names.

    Order is preserved as given (stable/byte-reproducible output). Unknown names
    are skipped so a config typo can't crash provisioning.
    """
    return "\n".join(
        f"{MODEL_PROVIDERS[p]}=" for p in providers if p in MODEL_PROVIDERS
    )


def model_compose_keys(providers, indent: str = "      ") -> str:
    """Return docker-compose env lines (``KEY: ${KEY:-}``) for the providers,
    each prefixed with `indent`. Ollama is set separately in the template."""
    return "\n".join(
        f"{indent}{MODEL_PROVIDERS[p]}: ${{{MODEL_PROVIDERS[p]}:-}}"
        for p in providers if p in MODEL_PROVIDERS
    )


def build_caddy_block(*, comment: str, agent_id: str, domain: str,
                      proxy_ip: str, port: int) -> str:
    """Generate the Caddy reverse-proxy block for an agent's subdomain.

    The two flows pass different comment styles and domains (the dashboard flow
    historically hardcoded both); the proxy body is identical.
    """
    return textwrap.dedent(f"""\
        {comment}
        {agent_id}.{domain} {{
            reverse_proxy {proxy_ip}:{port}
        }}
    """)


# =============================================================================
# Personal agent.yaml (#99) — single canonical shape for both flows
# =============================================================================
# The dashboard wizard's richer shape is canonical: it's the live, framework-
# aware version, and it uses the CORRECT tool-module prefix. The loader does
# importlib.import_module(f"src.{module_path}"), so module paths must be
# `tools.x` (NOT `src.tools.x`, which would double to `src.src.tools.x`).
# These 11 tabs + 6 routes + 6 tool modules are the working set.
_AGENT_TABS = [
    {"id": "home", "label": "Home", "scripts": ["home", "overview", "tuning-panel"]},
    {"id": "chat", "label": "Chat", "scripts": ["messaging", "voice", "manager-chat", "connect"]},
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
_AGENT_ROUTES = [
    "dashboard.routes.tasks_nc",
    "dashboard.routes.bridge",
    "dashboard.routes.home",
    "dashboard.routes.files",
    "dashboard.routes.projects",
    "dashboard.routes.agents",
]
_AGENT_TOOL_MODULES = [
    "tools.calendar_tools",
    "tools.nextcloud_tools",
    "tools.quick_list_tools",
    "tools.memory_tools",
    "tools.research_tools",
    "tools.site_tools",
]


def default_personal_boundaries(agent_name: str, operator_name: str) -> list:
    """Sensible default boundaries. Public-posting is an operator choice, not
    baked in here (some operators want their agent to post publicly)."""
    return [
        f"{agent_name} is {operator_name}'s personal agent. Conversations are private.",
        "No financial transactions without explicit approval.",
        "Does not access other family members' private data.",
    ]


def build_agent_yaml(*, agent_name: str, agent_id: str, archetype: str,
                     operator_name: str, operator_id: str, family_name: str,
                     port: int, timezone: str = "America/New_York",
                     location: str = "",
                     accent_color: str = "#5ce1e6", tuning_key: str = "",
                     frequency: str = "", frequency_color: str = "",
                     pronouns: str = "", role: str = None,
                     emoji: str = "\U0001F30D", boundaries: list = None) -> str:
    """Generate a personal agent's config/agent.yaml (canonical shape)."""
    if role is None:
        role = f"Personal agent for {operator_name}."
    if boundaries is None:
        boundaries = default_personal_boundaries(agent_name, operator_name)
    config = {
        "instance": {
            "name": agent_name,
            "type": "personal",
            "port": port,
            "operator": operator_name,
            "operator_handle": operator_id,
            "family_name": family_name,
            "timezone": timezone,
            "location": location,
            "accent_color": accent_color,
        },
        "agents": [
            {
                "id": agent_id,
                "name": agent_name,
                "archetype": archetype,
                "tuning_key": tuning_key,
                "frequency": frequency,
                "frequency_color": frequency_color,
                "pronouns": pronouns,
                "emoji": emoji,
                "role": role,
                "status": "active",
                "channels": ["day", "deep"],
                "boundaries": boundaries,
                "can_delegate_to": [],
            }
        ],
        "tabs": _AGENT_TABS,
        "routes": _AGENT_ROUTES,
        "tools": {"modules": _AGENT_TOOL_MODULES},
    }
    return yaml.dump(config, default_flow_style=False, allow_unicode=True,
                     sort_keys=False)


# =============================================================================
# docker-compose.yml (#99) — single canonical stack for both flows
# =============================================================================
# Canonical = the parameterized CLI shape (family-scoped external volumes + the
# {extra_env}/{extra_volumes} hooks). The dashboard's founder-specific values
# are now just variables (Nextcloud, steward bridge, framework are universal Cove
# features). Volume names are {agent_id}-{family_lc}-* — for the founder family
# (slug "cove") that equals the existing names, so no data moves.
_DOCKER_COMPOSE_TEMPLATE = """\
# =============================================================================
# {agent_system_name} — Docker Compose Stack (shared volume architecture)
# =============================================================================
# Uses the shared cove-core:latest image (built by deploy-cove-core.sh).
# Source merged at container startup: /cove-core/src + /overlay/src -> /app/src
#
# Port: {port} | Container: {container_app} / {container_postgres}
# DB: {db_name} / user: {agent_id}
# =============================================================================

name: {compose_project}

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: {container_postgres}
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${{POSTGRES_USER:-{agent_id}}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
      POSTGRES_DB: ${{POSTGRES_DB:-{db_name}}}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - {home_dir}/cove-core/docker/init-base.sql:/docker-entrypoint-initdb.d/00-base.sql:ro
      - ./init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    ports:
      - "127.0.0.1:{db_port}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${{POSTGRES_USER:-{agent_id}}}"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    image: cove-core:latest
    container_name: {container_app}
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    user: "1000:1000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    env_file: .env
    environment:
      # Database
      DATABASE_URL: postgresql://${{POSTGRES_USER:-{agent_id}}}:${{POSTGRES_PASSWORD}}@postgres:5432/${{POSTGRES_DB:-{db_name}}}
      # Models
{model_keys}
      OLLAMA_BASE_URL: ${{OLLAMA_BASE_URL:-http://host.docker.internal:11434}}
      # Timezone
      APP_TIMEZONE: ${{APP_TIMEZONE:-{timezone}}}
      # LTP protocol
      LTP_DRY_RUN: ${{LTP_DRY_RUN:-false}}
      TUNING_DELIVERY: ${{TUNING_DELIVERY:-git}}
      TUNING_REPO_URL: ${{TUNING_REPO_URL:-git@github.com:LucidTunerAI/LTP-drops.git}}
      TUNING_FAMILY: ${{TUNING_FAMILY:-{tuning_family}}}
      # Nextcloud (universal — every Presence)
      NEXTCLOUD_URL: ${{NEXTCLOUD_URL:-http://host.docker.internal:8080}}
      NEXTCLOUD_PUBLIC_URL: ${{NEXTCLOUD_PUBLIC_URL:-{nextcloud_public_url}}}
      NEXTCLOUD_USER: ${{NEXTCLOUD_USER:-{nextcloud_user}}}
      NEXTCLOUD_PASSWORD: ${{NEXTCLOUD_PASSWORD:-}}
      # Agent identity
      AGENT_ID: ${{AGENT_ID:-{agent_id}}}
      OPERATOR_NAME: ${{OPERATOR_NAME:-{operator_name}}}
      # Shared container (VPS) — affiliate stats proxy
      SHARED_CONTAINER_URL: ${{SHARED_CONTAINER_URL:-https://app.lucidcove.org}}
      SHARED_CONTAINER_SECRET: ${{SHARED_CONTAINER_SECRET:-}}
      OPERATOR_ACCOUNT_ID: ${{OPERATOR_ACCOUNT_ID:-}}
      # Vault / framework (inside container)
      VAULT_DIR: /vault
      FRAMEWORK_DIR: /shared/framework
      SHARED_FRAMEWORK_DIR: /shared/framework
      # Weekly backup target (the overlay is mounted here; see setup-backup.sh)
      BACKUP_REPO_DIR: ${{BACKUP_REPO_DIR:-/backup/{agent_id}{family_lc}}}
      # Steward bridge (universal — every Cove has a steward)
      STUART_MC_URL: ${{STUART_MC_URL:-{stuart_mc_url}}}
      STEWARD_DATABASE_URL: ${{STEWARD_DATABASE_URL:-}}
      # YouTube API (optional, per-Presence)
      YOUTUBE_CLIENT_ID: ${{YOUTUBE_CLIENT_ID:-}}
      YOUTUBE_CLIENT_SECRET: ${{YOUTUBE_CLIENT_SECRET:-}}
      YOUTUBE_REDIRECT_URI: ${{YOUTUBE_REDIRECT_URI:-}}{extra_env}
    volumes:
      # Shared cove-core (read-only base)
      - {home_dir}/cove-core:/cove-core:ro
      # Agent overlay
      - {home_dir}/{agent_system_name}:/overlay{overlay_mode}
      # Agent config
      - {home_dir}/{agent_system_name}/config:/app/config:ro
      # Persistent data
      - app_data:/app/data
      # SSH keys (git)
      - {home_dir}/.ssh:/home/agent/.ssh:ro
      # Backup mount
      - {home_dir}/{agent_system_name}:/backup/{agent_id}{family_lc}
      # Vault{vault_comment}
      - {vault_mount}
      {extra_volumes}
    ports:
      - "{port}:{port}"
    networks:
      default:
      {proxy_network}:
        ipv4_address: {proxy_ip}

networks:
  {proxy_network}:
    external: true

volumes:
  postgres_data:
    name: {agent_id}-{family_lc}-postgres-data
    external: true
  app_data:
    name: {agent_id}-{family_lc}-app-data
    external: true
"""


def build_docker_compose(*, agent_system_name: str, agent_id: str,
                         agent_name: str, db_name: str, db_port: int, port: int,
                         timezone: str, tuning_family: str, nextcloud_user: str,
                         nextcloud_public_url: str, operator_name: str,
                         proxy_ip: str, proxy_network: str, family_lc: str,
                         container_app: str, container_postgres: str,
                         compose_project: str, home_dir: str, model_providers,
                         stuart_mc_url: str = "http://host.docker.internal:8200",
                         overlay_mode: str = ":ro", vault_mount: str = "",
                         vault_comment: str = "", extra_volumes: str = "",
                         extra_env: str = "") -> str:
    """Generate an agent's docker-compose.yml (canonical, family-scoped volumes)."""
    if not vault_mount:
        vault_mount = f"{home_dir}/vaults/{nextcloud_user}:/vault"
    return _DOCKER_COMPOSE_TEMPLATE.format(
        agent_system_name=agent_system_name, agent_id=agent_id,
        agent_name=agent_name, db_name=db_name, db_port=db_port, port=port,
        timezone=timezone, tuning_family=tuning_family,
        nextcloud_user=nextcloud_user, nextcloud_public_url=nextcloud_public_url,
        operator_name=operator_name, proxy_ip=proxy_ip,
        proxy_network=proxy_network, family_lc=family_lc,
        container_app=container_app, container_postgres=container_postgres,
        compose_project=compose_project, home_dir=home_dir,
        stuart_mc_url=stuart_mc_url, overlay_mode=overlay_mode,
        vault_mount=vault_mount, vault_comment=vault_comment,
        extra_volumes=extra_volumes, extra_env=extra_env,
        model_keys=model_compose_keys(model_providers),
    )


# =============================================================================
# deploy-to-p620.sh (#99) — single canonical deploy script for both flows
# =============================================================================
# Canonical = the CLI's parameterized script (the more thorough one: verifies
# cove-core dir + image, syncs overlay src/, auto-copies .env, family-scoped
# volume create). The dashboard passes its founder values instead of hardcoding.
_DEPLOY_SCRIPT_TEMPLATE = """\
#!/bin/bash
# =============================================================================
# {agent_system_name} — Deploy overlay to P620 (shared volume architecture)
# =============================================================================
# Syncs agent-specific files (overlay src, config, docker) to target machine.
# Does NOT touch cove-core — that's handled by deploy-cove-core.sh.
#
# Run from Mac: cd {agent_system_name} && ./deploy-to-p620.sh
# After deploy: https://{agent_id}.{domain}
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SSH_HOST="{ssh_user}@{ssh_host}"
REMOTE_DIR="{home_dir}/{agent_system_name}"

echo "════════════════════════════════════════"
echo "  {agent_system_name} — Deploy overlay"
echo "  (shared volume architecture)"
echo "════════════════════════════════════════"
echo ""
echo "  Overlay: $SCRIPT_DIR"
echo "  Target:  $SSH_HOST:$REMOTE_DIR"
echo ""

echo "[1/5] Testing SSH connection..."
if ! ssh -o ConnectTimeout=5 "$SSH_HOST" "echo 'reachable'" 2>/dev/null; then
    echo "ERROR: Cannot reach target at $SSH_HOST"
    exit 1
fi
echo "  OK"
echo ""

echo "[2/5] Verifying cove-core is deployed..."
HC_CHECK=$(ssh "$SSH_HOST" "test -d {home_dir}/cove-core/src && echo 'yes' || echo 'no'")
if [ "$HC_CHECK" = "no" ]; then
    echo "  ERROR: cove-core not found."
    echo "  Run deploy-cove-core.sh first."
    exit 1
fi

IMG_CHECK=$(ssh "$SSH_HOST" "docker images -q cove-core:latest 2>/dev/null")
if [ -z "$IMG_CHECK" ]; then
    echo "  ERROR: cove-core:latest image not found."
    echo "  Run deploy-cove-core.sh first."
    exit 1
fi
echo "  cove-core: deployed"
echo "  cove-core:latest image: exists"
echo ""

# Fix permissions
find "$SCRIPT_DIR" -type f \\( -name "*.py" -o -name "*.js" -o -name "*.html" -o -name "*.css" \\
    -o -name "*.yaml" -o -name "*.yml" -o -name "*.json" -o -name "*.md" \\
    -o -name "*.sh" -o -name "*.sql" -o -name "*.txt" \\) \\
    -exec chmod 644 {{}} + 2>/dev/null || true

echo "[3/5] Syncing overlay..."
ssh "$SSH_HOST" "mkdir -p $REMOTE_DIR/{{src,config,docker}}"

if [ -d "$SCRIPT_DIR/src" ]; then
    rsync -avz --delete \\
        --exclude '.DS_Store' \\
        --exclude '__pycache__' \\
        --exclude '*.pyc' \\
        "$SCRIPT_DIR/src/" "$SSH_HOST:$REMOTE_DIR/src/"
    OV_PY=$(find "$SCRIPT_DIR/src" -name '*.py' 2>/dev/null | wc -l | tr -d ' ')
    echo "  Overlay src: $OV_PY Python files"
fi

rsync -avz --delete \\
    --exclude '.DS_Store' \\
    "$SCRIPT_DIR/config/" "$SSH_HOST:$REMOTE_DIR/config/"
echo "  Config synced"

rsync -avz \\
    --exclude '.DS_Store' \\
    --exclude '.env' \\
    --exclude 'postgres_data' \\
    "$SCRIPT_DIR/docker/" "$SSH_HOST:$REMOTE_DIR/docker/"
echo "  Docker files synced"
echo ""

echo "[4/5] Checking .env..."
ENV_EXISTS=$(ssh "$SSH_HOST" "test -f $REMOTE_DIR/docker/.env && echo 'yes' || echo 'no'")
if [ "$ENV_EXISTS" = "no" ]; then
    echo "  WARNING: No .env found at $REMOTE_DIR/docker/.env"
    echo "  Copying generated .env template..."
    scp "$SCRIPT_DIR/docker/.env" "$SSH_HOST:$REMOTE_DIR/docker/.env"
    echo "  .env copied — EDIT IT with real passwords before first start!"
else
    echo "  .env exists — keeping it"
fi
echo ""

echo "[5/5] Creating Docker volumes (if needed) and restarting..."
ssh "$SSH_HOST" "docker volume create {agent_id}-{family_lc}-postgres-data 2>/dev/null || true; \\
                 docker volume create {agent_id}-{family_lc}-app-data 2>/dev/null || true; \\
                 cd $REMOTE_DIR/docker && docker compose up -d"
echo ""

echo "  Waiting 5 seconds for startup..."
sleep 5

if ssh "$SSH_HOST" "curl -sf http://localhost:{port}/health" >/dev/null 2>&1; then
    echo ""
    echo "════════════════════════════════════════"
    echo "  {agent_system_name} is LIVE"
    echo "════════════════════════════════════════"
    echo ""
    echo "  Mission Control: https://{agent_id}.{domain}"
    echo "  Logs: ssh $SSH_HOST 'docker logs {container_app} -f'"
    echo ""
else
    echo ""
    echo "  Dashboard not responding yet. Check logs:"
    echo "  ssh $SSH_HOST 'docker logs {container_app} --tail 30'"
    echo ""
fi
"""


def build_deploy_script(*, agent_system_name: str, agent_id: str, domain: str,
                        ssh_user: str, ssh_host: str, home_dir: str,
                        family_lc: str, port: int, container_app: str) -> str:
    """Generate deploy-to-p620.sh (canonical: verifies cove-core, syncs src/,
    auto-copies .env, family-scoped volume create)."""
    return _DEPLOY_SCRIPT_TEMPLATE.format(
        agent_system_name=agent_system_name, agent_id=agent_id, domain=domain,
        ssh_user=ssh_user, ssh_host=ssh_host, home_dir=home_dir,
        family_lc=family_lc, port=port, container_app=container_app,
    )


# =============================================================================
# setup-backup.sh (#124) — one-time per-agent backup wiring
# =============================================================================
# Run once on the target host after first deploy. Idempotent. Sets up per-repo
# deploy keys + SSH aliases + git remotes so the weekly backup (Sun 03:00, the
# system.py /api/system/backup route, which runs in every container) can push
# code + DB dumps. The two GitHub repos + deploy keys are the only manual step
# (printed at the end). Uses __TOKEN__ substitution so bash ${...} stays literal.
_BACKUP_SETUP_TEMPLATE = """\
#!/bin/bash
# =============================================================================
# __AGENT_SYSTEM_NAME__ — one-time backup wiring (idempotent; safe to re-run)
# =============================================================================
set -e
OVERLAY="$(cd "$(dirname "$0")" && pwd)"
AGENT="__AGENT_SYSTEM_NAME__"
ORG="__ORG__"

# 1. .gitignore — never commit secrets or the nested backups repo
printf 'docker/.env\\nbackups/\\n__pycache__/\\n*.pyc\\n.DS_Store\\n' > "$OVERLAY/.gitignore"

# 2. per-repo deploy keys (write-only, one repo each)
[ -f ~/.ssh/id_ed25519___AGENT_ID___code ]    || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519___AGENT_ID___code -C "__AGENT_ID__-code-deploy"
[ -f ~/.ssh/id_ed25519___AGENT_ID___backups ] || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519___AGENT_ID___backups -C "__AGENT_ID__-backups-deploy"

# 3. SSH aliases
if ! grep -q "Host github-__AGENT_ID__-code" ~/.ssh/config 2>/dev/null; then
cat >> ~/.ssh/config <<EOF

Host github-__AGENT_ID__-code
  HostName github.com
  IdentityFile ~/.ssh/id_ed25519___AGENT_ID___code
  IdentitiesOnly yes

Host github-__AGENT_ID__-backups
  HostName github.com
  IdentityFile ~/.ssh/id_ed25519___AGENT_ID___backups
  IdentitiesOnly yes
EOF
fi

# 4. git repos + remotes
[ -d "$OVERLAY/.git" ] || { git -C "$OVERLAY" init -b main; git -C "$OVERLAY" remote add origin "git@github-__AGENT_ID__-code:$ORG/$AGENT.git"; }
mkdir -p "$OVERLAY/backups"
[ -d "$OVERLAY/backups/.git" ] || { git -C "$OVERLAY/backups" init -b main; git -C "$OVERLAY/backups" remote add origin "git@github-__AGENT_ID__-backups:$ORG/$AGENT-Backups.git"; echo "# $AGENT DB backups (pg_dump, last 14)" > "$OVERLAY/backups/README.md"; }

# 5. manual step (printed)
echo
echo "==================================================================="
echo "  ONE-TIME MANUAL STEP for $AGENT:"
echo "  a) Create two PRIVATE GitHub repos under $ORG:  $AGENT  and  $AGENT-Backups"
echo "  b) Add each key below as a DEPLOY KEY with 'Allow write access':"
echo "     --- add to $ORG/$AGENT ---"
cat ~/.ssh/id_ed25519___AGENT_ID___code.pub
echo "     --- add to $ORG/$AGENT-Backups ---"
cat ~/.ssh/id_ed25519___AGENT_ID___backups.pub
echo "  c) Then push the initial commits:"
echo "       git -C \\"$OVERLAY\\" add -A && git -C \\"$OVERLAY\\" -c user.email=backup@mc.internal -c user.name='MC Backup' commit -m init && git -C \\"$OVERLAY\\" push -u origin main"
echo "       git -C \\"$OVERLAY/backups\\" add -A && git -C \\"$OVERLAY/backups\\" -c user.email=backup@mc.internal -c user.name='MC Backup' commit -m init && git -C \\"$OVERLAY/backups\\" push -u origin main"
echo "  Weekly backups then run automatically (Sun 03:00) via the scheduler."
echo "==================================================================="
"""


def build_backup_setup_script(*, agent_system_name: str, agent_id: str,
                              org: str = "LucidTunerAI") -> str:
    """Generate the one-time backup-wiring script for an agent overlay (#124)."""
    return (_BACKUP_SETUP_TEMPLATE
            .replace("__AGENT_SYSTEM_NAME__", agent_system_name)
            .replace("__AGENT_ID__", agent_id)
            .replace("__ORG__", org))


# =============================================================================
# Matrix homeserver (Dendrite) — #127
# =============================================================================
# The homeserver is per-MACHINE, keyed to the machine's primary family domain,
# NOT per-Cove. Multiple Coves on one machine (e.g. the founder Cove + the
# Clearfield sandbox on the P620) SHARE one homeserver and are separated by
# rooms. Federation connects homeservers across DIFFERENT machines.
#
# This builder templatizes the previously hand-maintained Services/Dendrite/
# dendrite.yaml: server_name from the family domain, real secrets, and the
# rate-limit bot exemptions derived from the family's agent ids.

def build_dendrite_config(*, server_name: str, db_password: str,
                          registration_shared_secret: str,
                          bot_user_ids=None) -> str:
    """Generate dendrite.yaml for a machine's Matrix homeserver.

    server_name: e.g. 'matrix.cove.lucidcove.org' (matrix.{family_domain}).
    db_password: real generated Postgres password (must match the .env).
    registration_shared_secret: generated; gates account creation.
    bot_user_ids: agent localparts exempt from client-API rate limiting
        (e.g. ['stuart', 'atlas', 'lt']) -> '@stuart:{server_name}' etc.
    """
    bots = list(bot_user_ids or [])
    if bots:
        exempt_block = "    exempt_user_ids:\n" + "\n".join(
            f'      - "@{b}:{server_name}"' for b in bots)
    else:
        exempt_block = "    exempt_user_ids: []"

    return f"""\
# =============================================================================
# Dendrite Configuration — Lucid Cove Matrix Homeserver
# =============================================================================
# Generated by the provisioner (#127). One homeserver per machine.
# Domain: {server_name}
# =============================================================================

version: 2

global:
  server_name: {server_name}
  private_key: /etc/dendrite/matrix_key.pem
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
  # Rate limiting — relaxed for internal use
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
  # to mesh IPs and breaks Cove<->Cove / hub<->Cove federation over the mesh.
  # We drop 100.64.0.0/10 from the deny list so mesh federation works, while
  # keeping every other private range blocked for SSRF safety.
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


# Compose for the per-machine Dendrite stack. Container/volume names are
# constant (one homeserver per machine, so no collisions and no migration on
# the founder box). Only the proxy network, static IP, and external DB port
# vary. __TOKEN__ substitution keeps the compose ${VAR} interpolation literal.
_DENDRITE_COMPOSE_TEMPLATE = """\
# =============================================================================
# Dendrite — Matrix Homeserver (Monolith Mode)
# =============================================================================
# Generated by the provisioner (#127). One instance per machine; all Coves on
# this hardware share it (separated by rooms). Cross-machine = federation.
# Ports: 8008 (client API), 8448 (federation).
# =============================================================================

name: dendrite

services:
  postgres:
    image: postgres:16
    container_name: dendrite-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-dendrite}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB:-dendrite}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:__DB_PORT__:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dendrite}"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    image: matrixdotorg/dendrite-monolith:latest
    container_name: dendrite-app
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./dendrite.yaml:/etc/dendrite/dendrite.yaml:ro
      - ./signing-key.pem:/etc/dendrite/matrix_key.pem:ro
      - media_data:/var/dendrite/media
      - jetstream_data:/var/dendrite/jetstream
      - search_data:/var/dendrite/searchindex
    ports:
      - "8008:8008"
      - "8448:8448"
    networks:
      default:
      __PROXY_NETWORK__:
        ipv4_address: __PROXY_IP__

networks:
  __PROXY_NETWORK__:
    external: true

volumes:
  postgres_data:
    name: dendrite-postgres-data
  media_data:
    name: dendrite-media-data
  jetstream_data:
    name: dendrite-jetstream-data
  search_data:
    name: dendrite-search-data
"""


def build_dendrite_compose(*, proxy_ip: str = "172.30.0.17",
                           proxy_network: str = "cove-proxy",
                           db_port: int = 5439) -> str:
    """Generate docker-compose.yml for the per-machine Dendrite homeserver."""
    return (_DENDRITE_COMPOSE_TEMPLATE
            .replace("__PROXY_NETWORK__", proxy_network)
            .replace("__PROXY_IP__", proxy_ip)
            .replace("__DB_PORT__", str(db_port)))


_DENDRITE_ENV_TEMPLATE = """\
# =============================================================================
# Dendrite homeserver — Environment Variables
# =============================================================================
# Generated by the provisioner (#127). The DB password must match the
# connection_string in dendrite.yaml.
# =============================================================================
POSTGRES_USER=dendrite
POSTGRES_PASSWORD=__DB_PASSWORD__
POSTGRES_DB=dendrite
"""


def build_dendrite_env(*, db_password: str) -> str:
    """Generate the homeserver .env (DB password matches dendrite.yaml)."""
    return _DENDRITE_ENV_TEMPLATE.replace("__DB_PASSWORD__", db_password)


# register.sh — bash ${...} stays literal via __TOKEN__ substitution.
# Fixes the two bugs in the old hand-deployed copy: stale server name in the
# echo, and `docker exec -it` (which fails over non-TTY SSH).
_DENDRITE_REGISTER_TEMPLATE = """\
#!/bin/bash
# =============================================================================
# Register a Dendrite user on this machine's homeserver (__SERVER_NAME__)
# =============================================================================
# Usage: bash register.sh <username> <password> [--admin]
#   bash register.sh USERNAME PASSWORD          # regular user
#   bash register.sh stuart BotPass --admin    # admin (agent bots)
# =============================================================================
set -e
USERNAME="${1:?Usage: register.sh <username> <password> [--admin]}"
PASSWORD="${2:?Usage: register.sh <username> <password> [--admin]}"
ADMIN_FLAG=""
[ "$3" = "--admin" ] && ADMIN_FLAG="--admin"

echo "Registering @${USERNAME}:__SERVER_NAME__ ..."
docker exec dendrite-app /usr/bin/create-account \\
    --config /etc/dendrite/dendrite.yaml \\
    --username "$USERNAME" --password "$PASSWORD" $ADMIN_FLAG
echo "User @${USERNAME}:__SERVER_NAME__ created."
"""


def build_dendrite_register_script(*, server_name: str) -> str:
    """Generate register.sh (no -it; correct server_name in messages)."""
    return _DENDRITE_REGISTER_TEMPLATE.replace("__SERVER_NAME__", server_name)


# setup.sh — generic (generates the ed25519 signing key). Run once per machine.
DENDRITE_SETUP_SCRIPT = """\
#!/bin/bash
# =============================================================================
# Dendrite First-Time Setup — generate the ed25519 signing key (run ONCE).
# =============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ -f signing-key.pem ]; then
    echo "signing-key.pem already exists. Skipping generation."
else
    echo "Generating ed25519 signing key..."
    docker run --rm --entrypoint /usr/bin/generate-keys \\
        matrixdotorg/dendrite-monolith:latest \\
        --private-key /dev/stdout > signing-key.pem
    chmod 600 signing-key.pem
    echo "Signing key generated."
fi
echo ""
echo "Next: docker compose up -d   then   bash register.sh <username> <password>"
"""


# Federation-ready Caddy block for the homeserver. Unlike a plain reverse_proxy,
# this serves .well-known server/client discovery so OTHER machines' homeservers
# can find and federate with this one over Caddy's TLS (port 443), and clients
# can auto-discover the base URL. The Dendrite monolith serves both the client
# and federation APIs on the client port, so a single reverse_proxy covers both.
# __TOKEN__ substitution keeps the JSON braces literal.
_MATRIX_CADDY_TEMPLATE = """\
# ── Matrix homeserver (__SERVER_NAME__) ──────────────
__SERVER_NAME__ {
    # Server discovery — federation + client (.well-known delegated to :443)
    handle /.well-known/matrix/server {
        header Content-Type application/json
        respond `{"m.server": "__SERVER_NAME__:443"}` 200
    }
    handle /.well-known/matrix/client {
        header Content-Type application/json
        header Access-Control-Allow-Origin *
        respond `{"m.homeserver": {"base_url": "https://__SERVER_NAME__"}}` 200
    }
    # Client + federation API (Dendrite monolith serves both on the client port)
    reverse_proxy __PROXY_IP__:__CLIENT_PORT__
}
"""


def build_matrix_caddy_block(*, server_name: str, proxy_ip: str,
                             client_port: int = 8008) -> str:
    """Generate the federation-ready Caddy block for a machine's homeserver."""
    return (_MATRIX_CADDY_TEMPLATE
            .replace("__SERVER_NAME__", server_name)
            .replace("__PROXY_IP__", proxy_ip)
            .replace("__CLIENT_PORT__", str(client_port)))


# =============================================================================
# Matrix layer Phase 1 (#137) — per-Cove Spaces structure
# =============================================================================
# Generates matrix-cove-setup.py: creates the Cove's Space + group room and
# links them (Matrix Spaces hierarchy), run once AS the operator so the operator
# owns the Space. The provisioner emits this per Cove; later SSO (#137 Phase 2)
# runs it automatically. __TOKEN__ substitution keeps the script's own {}/% / f
# syntax literal.
_MATRIX_COVE_SETUP_TEMPLATE = '''#!/usr/bin/env python3
"""matrix-cove-setup.py — creates this Cove's Space + group room (#137 Phase 1).
Generated by the provisioner. Run once as the operator (operator owns the Space):
    python3 matrix-cove-setup.py <operator_password>"""
import json, sys, ssl, urllib.request, urllib.error, urllib.parse
SERVER_NAME = "__SERVER_NAME__"
BASE = "https://" + SERVER_NAME
COVE_NAME = "__COVE_NAME__"
OPERATOR = "__OPERATOR__"
GROUP_ROOM = COVE_NAME + " Family"
INVITE = __INVITE_LIST__
ctx = ssl.create_default_context()

def http(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def uid(localpart):
    return "@" + localpart + ":" + SERVER_NAME

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 matrix-cove-setup.py <operator_password>"); sys.exit(1)
    pw = sys.argv[1]
    s, r = http("POST", "/_matrix/client/v3/login", body={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": OPERATOR}, "password": pw})
    if s != 200:
        print("Login failed:", s, r); sys.exit(1)
    tok = r["access_token"]; print("Logged in as", r["user_id"])
    invites = [uid(x) for x in INVITE]
    s, r = http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": COVE_NAME, "topic": COVE_NAME + " - your Cove",
        "creation_content": {"type": "m.space"},
        "preset": "private_chat", "visibility": "private", "invite": invites})
    if s != 200:
        print("Create Space failed:", s, r); sys.exit(1)
    space_id = r["room_id"]; print("Space created:", space_id)
    s, r = http("POST", "/_matrix/client/v3/createRoom", tok, {
        "name": GROUP_ROOM, "topic": "Family room for " + COVE_NAME,
        "preset": "private_chat", "visibility": "private", "invite": invites})
    if s != 200:
        print("Create group room failed:", s, r); sys.exit(1)
    room_id = r["room_id"]; print("Group room created:", room_id)
    s, _ = http("PUT", "/_matrix/client/v3/rooms/" + urllib.parse.quote(space_id)
                + "/state/m.space.child/" + urllib.parse.quote(room_id), tok, {"via": [SERVER_NAME]})
    print("space.child:", s)
    s, _ = http("PUT", "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id)
                + "/state/m.space.parent/" + urllib.parse.quote(space_id), tok,
                {"via": [SERVER_NAME], "canonical": True})
    print("space.parent:", s)
    print("DONE. space_id =", space_id, "room_id =", room_id)

if __name__ == "__main__":
    main()
'''


def build_matrix_cove_setup(*, server_name: str, cove_name: str, operator: str,
                            invite=None) -> str:
    """Generate matrix-cove-setup.py for a Cove (Space + group room + invites)."""
    inv = list(invite or [])
    inv_list = "[" + ", ".join('"%s"' % x for x in inv) + "]"
    return (_MATRIX_COVE_SETUP_TEMPLATE
            .replace("__SERVER_NAME__", server_name)
            .replace("__COVE_NAME__", cove_name)
            .replace("__OPERATOR__", operator)
            .replace("__INVITE_LIST__", inv_list))
