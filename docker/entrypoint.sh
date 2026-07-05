#!/bin/bash
# =============================================================================
# cove-core — Shared Container Entrypoint
# =============================================================================
# Runtime merge: /cove-core/src (shared base) + /overlay/src (agent-specific)
# → /app/src (working directory the app reads from)
#
# This runs on EVERY container start. The merge is fast (small files, local cp).
# After merge, starts the dashboard (FastAPI + scheduler) on the port from config.
#
# Agent-specific hooks: if /overlay/entrypoint-hook.sh exists, it runs after merge.
# =============================================================================

set -e

# ── Read agent identity from config ────────────────────────────────────
AGENT_NAME=$(python3 -c "
import yaml
try:
    with open('/app/config/agent.yaml') as f:
        cfg = yaml.safe_load(f)
    inst = cfg.get('instance', {})
    name = inst.get('name', 'Agent')
    family = inst.get('family_name', '')
    print(f'{name} {family}'.strip())
except Exception:
    print('Agent')
" 2>/dev/null || echo "Agent")

AGENT_PORT=$(python3 -c "
import yaml
try:
    with open('/app/config/agent.yaml') as f:
        cfg = yaml.safe_load(f)
    print(cfg.get('instance', {}).get('port', 8200))
except Exception:
    print(8200)
" 2>/dev/null || echo "8200")

echo "════════════════════════════════════════"
echo "  $AGENT_NAME — Starting"
echo "════════════════════════════════════════"
echo "  DATABASE_URL:  ${DATABASE_URL:+set}"
echo "  OLLAMA:        ${OLLAMA_BASE_URL:-http://localhost:11434}"
echo "  APP_TIMEZONE:  ${APP_TIMEZONE:-America/New_York}"
echo "  LTP_DRY_RUN:   ${LTP_DRY_RUN:-true}"
echo "  PORT:          $AGENT_PORT"
echo "════════════════════════════════════════"

# ── Runtime merge: cove-core + overlay → /app/src ──────────────────
# Python files are COPIED (require restart to pick up changes — that's Python).
# Static assets (JS, CSS, HTML, images) are SYMLINKED from the mount so they're
# read live — change a file on the host, browser refresh picks it up instantly.
# Overlay files copy on top of both, replacing symlinks where the overlay wins.

echo "[merge] Merging cove-core base..."
if [ -d /cove-core/src ]; then
    # ── Python: copy (restart required for changes) ──
    find /cove-core/src -name '*.py' | while read file; do
        rel="${file#/cove-core/src/}"
        mkdir -p "/app/src/$(dirname "$rel")"
        cp "$file" "/app/src/$rel"
    done

    # ── Static assets: symlink for live updates ──
    if [ -d /cove-core/src/dashboard/static ]; then
        # Create directory structure
        find /cove-core/src/dashboard/static -type d | while read dir; do
            mkdir -p "/app/src/dashboard/static${dir#/cove-core/src/dashboard/static}"
        done
        # Symlink individual files (live from read-only mount)
        find /cove-core/src/dashboard/static -type f -not -name '.DS_Store' | while read file; do
            rel="${file#/cove-core/src/dashboard/static/}"
            ln -sf "$file" "/app/src/dashboard/static/$rel"
        done
    fi

    HC_PY=$(find /cove-core/src -name '*.py' 2>/dev/null | wc -l | tr -d ' ')
    HC_STATIC=$(find /cove-core/src/dashboard/static -type f -not -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')
    echo "  cove-core: ${HC_PY} .py copied, ${HC_STATIC} static symlinked (live)"
else
    echo "  WARNING: /cove-core/src not found — running without shared base"
fi

echo "[merge] Applying agent overlay..."
if [ -d /overlay/src ] && [ "$(ls -A /overlay/src 2>/dev/null)" ]; then
    # Overlay: copy everything. Python copied normally. Static files replace
    # symlinks where the overlay needs different behavior (e.g. avatars, custom JS).
    cp -r --remove-destination /overlay/src/* /app/src/ 2>/dev/null || true
    OV_PY=$(find /overlay/src -name '*.py' 2>/dev/null | wc -l | tr -d ' ')
    OV_STATIC=$(find /overlay/src/dashboard/static -type f -not -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')
    echo "  overlay: ${OV_PY} .py, ${OV_STATIC:-0} static (copied, override core)"
else
    echo "  overlay: (none)"
fi

APP_PY=$(find /app/src -name '*.py' 2>/dev/null | wc -l | tr -d ' ')
APP_STATIC=$(find /app/src/dashboard/static \( -type f -o -type l \) -not -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')
echo "[merge] Result: ${APP_PY} .py, ${APP_STATIC} static in /app/src"
echo "[merge] Static assets from cove-core are LIVE — browser refresh picks up changes"
echo ""

# ── Directories ────────────────────────────────────────────────────────
mkdir -p /app/data/logs /app/data/projects /app/data/scratch /app/data/runbooks

# ── Seed runbooks from cove-core (always overwrite — seeds are source of truth) ──
if [ -d /cove-core/runbooks ]; then
    cp /cove-core/runbooks/*.json /app/data/runbooks/ 2>/dev/null || true
    RB_COUNT=$(ls /app/data/runbooks/*.json 2>/dev/null | wc -l | tr -d ' ')
    echo "[init] Runbooks seeded: ${RB_COUNT} from cove-core"
fi

# ── Auto-apply migrations (idempotent) — #171 ──────────────────────────
# A code update + restart auto-creates any new tables on an EXISTING Cove DB,
# so we never hand-run psql after a deploy. Every migration is CREATE/ALTER
# IF NOT EXISTS, so re-running is a no-op. Best-effort: a migration error is
# logged but never blocks startup (the `|| echo` keeps `set -e` from exiting).
if [ -n "$DATABASE_URL" ] && [ -d /cove-core/docker/migrations ]; then
    echo "[migrate] applying migrations (idempotent)..."
    for f in $(ls /cove-core/docker/migrations/*.sql 2>/dev/null | sort); do
        # Capture stderr so a failed migration NAMES its error — the old form
        # discarded all output (and with ON_ERROR_STOP=0 psql exits 0 even on
        # failed statements, so partial failures printed ✓). A later runtime
        # "relation ... does not exist" now has a log line to point back to.
        MIG_ERR="$(psql "$DATABASE_URL" -v ON_ERROR_STOP=0 -q -f "$f" 2>&1 >/dev/null || true)"
        if [ -z "$MIG_ERR" ]; then
            echo "  ✓ $(basename "$f")"
        else
            echo "  ⚠ $(basename "$f") (skipped/partial — non-fatal): $(printf '%s' "$MIG_ERR" | tail -2 | tr '\n' ' ')"
        fi
    done
    echo "[migrate] done"
fi

# ── Git identity (set at runtime from config) ──────────────────────────
GIT_NAME=$(python3 -c "
import yaml
try:
    with open('/app/config/agent.yaml') as f:
        cfg = yaml.safe_load(f)
    inst = cfg.get('instance', {})
    name = inst.get('name', 'Agent')
    family = inst.get('family_name', '')
    print(f'{name} {family}'.strip())
except Exception:
    print('Agent')
" 2>/dev/null || echo "Agent")

GIT_EMAIL=$(python3 -c "
import yaml
try:
    with open('/app/config/agent.yaml') as f:
        cfg = yaml.safe_load(f)
    agent_id = cfg.get('instance', {}).get('agent_id', 'agent')
    print(f'{agent_id}@family.local')
except Exception:
    print('agent@family.local')
" 2>/dev/null || echo "agent@family.local")

git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"

# ── Git HTTPS credentials (if .git-credentials is mounted) ───────────
if [ -f /home/agent/.git-credentials ]; then
    git config --global credential.helper store
fi

# ── Agent-specific hook (optional) ─────────────────────────────────────
if [ -f /overlay/entrypoint-hook.sh ]; then
    echo "[hook] Running agent entrypoint hook..."
    bash /overlay/entrypoint-hook.sh
fi

# ── Start dashboard ────────────────────────────────────────────────────
# Direct-TLS is legacy (Caddy terminates TLS now) — env-driven paths for any
# deployment that still mounts certs into /ssl; no host names baked in.
SSL_CERT="${SSL_CERT_FILE:-/ssl/cove.crt}"
SSL_KEY="${SSL_KEY_FILE:-/ssl/cove.key}"

if [ -f "$SSL_CERT" ] && [ -f "$SSL_KEY" ]; then
    echo "Starting dashboard on :${AGENT_PORT} with TLS..."
    SSL_ARGS="--ssl-certfile $SSL_CERT --ssl-keyfile $SSL_KEY"
else
    echo "Starting dashboard on :${AGENT_PORT} (no TLS)..."
    SSL_ARGS=""
fi

uvicorn src.dashboard.app:app \
    --host 0.0.0.0 \
    --port "$AGENT_PORT" \
    $SSL_ARGS \
    --log-level info \
    2>&1 | python3 -u -c "
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import os

log_dir = Path('/app/data/logs')
log_dir.mkdir(exist_ok=True)
tz = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))
current_date = None
log_file = None

for line in sys.stdin:
    sys.stdout.write(line)
    sys.stdout.flush()
    now = datetime.now(tz)
    today = now.strftime('%Y-%m-%d')
    if today != current_date:
        if log_file:
            log_file.close()
        current_date = today
        log_file = open(log_dir / f'app-{today}.log', 'a')
    if log_file:
        ts = now.strftime('%Y-%m-%d %H:%M:%S')
        stripped = line.strip()
        if stripped and not stripped[:4].isdigit():
            log_file.write(f'[{ts}] {line}')
        else:
            log_file.write(line)
        log_file.flush()
"
