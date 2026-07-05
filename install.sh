#!/usr/bin/env bash
# =============================================================================
# Lucid Cove — one-command installer.
#
#   curl -fsSL https://raw.githubusercontent.com/LucidPrinciples/lucid-cove/main/install.sh | bash
#   …or, from a clone:  bash install.sh
#
# It checks the ONE thing it can't safely install for you (Docker), then does the
# rest: clone, generate a from-scratch Cove, and start it. Everything the Cove
# needs (database, Nextcloud, the app, HTTPS/Caddy, voice) is pulled + built by
# Docker — you don't install any of it by hand. The provisioner runs inside a
# throwaway Python container, so the host needs no Python/PyYAML either.
#
# After it finishes, open the printed link and make up your identity + Cove in the
# browser wizard. No config to hand-edit.
# =============================================================================
set -euo pipefail

REPO="https://github.com/LucidPrinciples/lucid-cove.git"
c_cyan(){ printf "\n\033[1;36m%s\033[0m\n" "$1"; }
c_red(){ printf "\n\033[1;31m%s\033[0m\n" "$1" >&2; }
c_dim(){ printf "\033[2m%s\033[0m\n" "$1"; }
c_yellow(){ printf "\033[1;33m%s\033[0m\n" "$1"; }

# ── 1. Prerequisites we cannot safely auto-install ──────────────────────────
docker_help() {
  c_red "Docker is required and isn't available."
  echo "  • Mac / Windows: install Docker Desktop → https://www.docker.com/products/docker-desktop/"
  echo "  • Linux:         curl -fsSL https://get.docker.com | sh"
  echo "Then re-run this installer."
  exit 1
}
command -v docker >/dev/null 2>&1 || docker_help

ensure_docker_running() {
  docker info >/dev/null 2>&1 && return 0
  # macOS: Docker is the Docker Desktop app — start it for them and wait.
  if [ "$(uname)" = "Darwin" ] && [ -d "/Applications/Docker.app" ]; then
    c_cyan "Docker isn't running — starting Docker Desktop for you…"
    open -a Docker >/dev/null 2>&1 || true
    printf "  waiting for Docker to be ready"
    for _ in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then printf " ready.\n"; return 0; fi
      printf "."; sleep 3
    done
    printf "\n"
    c_red "Docker Desktop didn't finish starting in ~3 minutes."
    echo "  Open Docker Desktop, wait for the whale icon to say 'running', then re-run: bash install.sh"
    exit 1
  fi
  # Linux / other: tell apart "no permission" (docker group) from "daemon down".
  derr="$(docker info 2>&1 || true)"
  if printf '%s' "$derr" | grep -qi "permission denied"; then
    c_red "Docker is installed but your user can't talk to it (permission denied)."
    echo "  Add yourself to the docker group, then open a new shell:"
    echo "     sudo usermod -aG docker \$USER && newgrp docker"
    echo "  …or run the installer with sudo:   sudo bash install.sh"
    exit 1
  fi
  c_red "Docker is installed but not running."
  echo "  • Mac/Windows: open Docker Desktop and wait until it says 'running', then re-run."
  echo "  • Linux:       sudo systemctl start docker     (then re-run)"
  exit 1
}
ensure_docker_running
command -v git >/dev/null 2>&1 || { c_red "git is required. Install git, then re-run."; exit 1; }
# curl runs the health poll at the end — without it the installer would wait 5
# minutes and wrongly report "still starting" even when the Cove is fine.
command -v curl >/dev/null 2>&1 || { c_red "curl is required (it's how the installer waits for your Cove to come up). Install curl, then re-run."; exit 1; }
# lsof backs the free-port picker — without it a busy non-docker host listener
# can be picked as "free" and the Cove collides with it.
command -v lsof >/dev/null 2>&1 || { c_red "lsof is required (it's how the installer finds free ports). Install lsof (e.g. sudo apt-get install lsof), then re-run."; exit 1; }
# Docker Compose v2 plugin ("docker compose", not the old docker-compose v1) —
# every stack file here uses it, and it used to fail AFTER clone + config with
# "'compose' is not a docker command".
if ! docker compose version >/dev/null 2>&1; then
  c_red "Docker Compose v2 is required ('docker compose' — the plugin, not the old docker-compose)."
  echo "  • Docker Desktop includes it."
  echo "  • Linux: sudo apt-get install docker-compose-plugin   (or see https://docs.docker.com/compose/install/)"
  echo "Then re-run this installer."
  exit 1
fi
# Disk space: the first run builds/pulls the app, voice (CPU torch), Nextcloud,
# Dendrite, pgvector and Redis images plus whisper/piper model downloads — well
# past 10 GB. Without this check that surfaces as cryptic mid-build failures.
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
# NB Docker Desktop (macOS/Windows) reports a DockerRootDir that only exists
# inside its VM — df fails on the host, and without `|| true` that failure
# rides the command substitution through set -e/pipefail and kills the install
# SILENTLY before the $HOME fallback can run (the run-3 iMac bug). The $HOME
# fallback measures the host disk that actually backs the VM's disk image.
FREE_GB="$(df -Pk "${DOCKER_ROOT:-$PWD}" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}' || true)"
[ -z "${FREE_GB:-}" ] && FREE_GB="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}' || true)"
if [ -n "${FREE_GB:-}" ] && [ "$FREE_GB" -lt 15 ]; then
  c_red "Not enough free disk space: ${FREE_GB} GB free where Docker keeps its data."
  echo "  The first run needs roughly 15 GB (images + voice models), and your Cove's"
  echo "  data grows from there. Free up space, then re-run: bash install.sh"
  exit 1
fi

# ── 2. Get the code (clone if we're not already inside it) ───────────────────
# Each run gets its OWN clone dir — one clone = one Cove, so re-running the
# installer stands up ANOTHER Cove beside the first (multi-Cove on one box is
# the product shape; the shared Caddy + port preflight handle coexistence).
# A fixed dir name here made run #2 die on "destination path already exists".
# Re-running INSIDE an existing clone still repairs/updates that same Cove.
if [ ! -f provision/centralized.py ]; then
  c_cyan "Downloading Lucid Cove…"
  DEST="lucid-cove-$(date +%y%m%d-%H%M%S)"
  [ -d "$DEST" ] && DEST="$DEST-$RANDOM"
  git clone --depth 1 "$REPO" "$DEST"
  cd "$DEST"
fi
CORE="$(pwd)"

# ── 3. Write a from-scratch config (identity is created in the browser wizard) ─
CFG="$CORE/cove.config.yaml"
if [ ! -f "$CFG" ]; then
  c_cyan "Preparing your Lucid Cove (from scratch)…"
  # Host-side port preflight. The provisioner runs in a throwaway container with no
  # Docker CLI and its own network namespace, so it CANNOT see which host ports are
  # taken — it would always keep the static defaults and a 2nd Cove on this box would
  # collide (the 8301 voice error). So pick free ports HERE on the host and pass them
  # in; each Cove on the machine gets its own. (docker ps for Cove ports, lsof for any
  # other host listener.)
  _busy() { docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$1->" || lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
  _free() { local p="$1"; while _busy "$p"; do p=$((p+1)); done; printf '%s' "$p"; }
  APP_PORT=$(_free 8200); NC_PORT=$(_free 8080); MX_PORT=$(_free 8008); VOICE_PORT=$(_free 8301)
  c_dim "  ports: app $APP_PORT · nextcloud $NC_PORT · matrix $MX_PORT · voice $VOICE_PORT"
  # Host-side mesh preflight (CF-90b). Mesh membership is foundational: a claimed
  # address must point DNS at the box's MESH IP or nothing can reach it (NAT). The
  # provisioner runs in a throwaway container with its own network namespace, so it
  # can NEVER see the host's tailscale interface — detect HERE on the host and pass
  # it in, exactly like the port preflight above. Best-effort: no mesh, no value.
  MESH_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  case "$MESH_IP" in 100.*) : ;; *) MESH_IP="" ;; esac
  if [ -z "$MESH_IP" ]; then
    MESH_IP="$( { ip -4 -o addr show 2>/dev/null || ifconfig 2>/dev/null; } \
      | grep -oE '100\.[0-9]+\.[0-9]+\.[0-9]+' \
      | awk -F. '$2>=64 && $2<=127' | head -1 || true)"
  fi
  [ -n "$MESH_IP" ] && c_dim "  mesh: this box is on the mesh ($MESH_IP) — address claims will point there"
  # Host-side GPU preflight. The provisioner runs in a container where nvidia-smi can
  # never exist, so it would always record "no GPU" — detect HERE on the host and pass
  # the facts in, exactly like the port/mesh/timezone preflights. Drives local video
  # transcription + local-model sizing. Best-effort: no GPU (or no nvidia-smi), no block.
  GPU_NAME=""; GPU_VRAM=""
  GPU_LINE="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || true)"
  if [ -n "$GPU_LINE" ]; then
    GPU_NAME="$(printf '%s' "$GPU_LINE" | cut -d, -f1 | sed 's/^ *//;s/ *$//')"
    GPU_VRAM="$(printf '%s' "$GPU_LINE" | cut -d, -f2 | tr -dc '0-9')"
    c_dim "  gpu: $GPU_NAME (${GPU_VRAM:-?} MB) — local video transcription + model sizing on"
  fi
  cat > "$CFG" <<YAML
# Generated by install.sh. from_scratch = you make up your @handle + Cove name in
# the setup wizard (checked for uniqueness across the network). No domain here —
# claim your address in the browser after setup.
from_scratch: true
team: on
deploy:
  target: standalone
  cove_core_path: $CORE
  app_port: $APP_PORT
  nextcloud_port: $NC_PORT
  matrix_port: $MX_PORT
  voice_port: $VOICE_PORT${MESH_IP:+
  mesh_ip: $MESH_IP}
model_providers:
  - openrouter        # add your key in the wizard / Settings — not required to boot
matrix:
  enabled: true       # Connect/chat — fully works once you claim an address (needs matrix.{domain})${GPU_NAME:+
compute:
  gpu:
    present: true     # detected on the host by install.sh (the provisioner container is blind)
    name: "$GPU_NAME"
    vram_mb: ${GPU_VRAM:-0}}
YAML
  c_dim "  wrote $CFG"
fi

# ── 4. Generate the Cove (provisioner runs in a container — no host Python) ───
# Detect the host timezone on the HOST (the provisioner container is UTC) and pass it in,
# so the Cove inherits your machine's zone instead of Etc/UTC.
HOST_TZ="$(readlink /etc/localtime 2>/dev/null | sed -n 's#.*/zoneinfo/##p')"
[ -z "$HOST_TZ" ] && [ -f /etc/timezone ] && HOST_TZ="$(cat /etc/timezone 2>/dev/null)"
c_cyan "Building your Lucid Cove…"
[ -n "$HOST_TZ" ] && c_dim "  timezone: $HOST_TZ"
docker run --rm \
  -u "$(id -u):$(id -g)" -e HOME=/tmp -e TZ="${HOST_TZ:-}" \
  -v "$CORE":/work -w /work \
  python:3.12-slim \
  sh -lc "pip install -q pyyaml && python3 provision/centralized.py cove.config.yaml --output out"

DIR="$(ls -d "$CORE"/out/*-cove 2>/dev/null | head -1)"
if [ -z "${DIR:-}" ]; then c_red "Provisioning produced no output — see messages above."; exit 1; fi

# ── 4b. Shared Caddy (one per box) — owns 80/443, routes EVERY Cove ───────────
# A self-host box runs ONE shared Caddy on an external Docker bridge so MULTIPLE
# Coves can co-exist (each Cove is Caddy-less and routed by container name) — which
# is what lets Coves on the same box federate Matrix to each other. Idempotent:
# create the bridge + bring up the shared Caddy only if they aren't already there.
# The provisioner generated the stack into ~/.lucidcove/caddy (compose + base
# Caddyfile + conf.d). Single-Cove uses the same path (no special-casing the count).
SHARED_CADDY_DIR="$HOME/.lucidcove/caddy"
# The provisioner wrote the bootstrap into the output (it runs in a container where ~ is
# /tmp). Copy it to the host's shared dir ONCE — never clobber an existing conf.d, so a
# 2nd Cove on this box doesn't wipe the 1st Cove's routing snippets.
BOOT="$CORE/out/_shared-caddy"
if [ -d "$BOOT" ] && [ ! -f "$SHARED_CADDY_DIR/docker-compose.yml" ]; then
  mkdir -p "$SHARED_CADDY_DIR/conf.d"
  cp "$BOOT/docker-compose.yml" "$SHARED_CADDY_DIR/docker-compose.yml"
  [ -f "$SHARED_CADDY_DIR/Caddyfile" ] || cp "$BOOT/Caddyfile" "$SHARED_CADDY_DIR/Caddyfile"
fi
if [ -f "$SHARED_CADDY_DIR/docker-compose.yml" ]; then
  if ! docker network inspect lucidcove-net >/dev/null 2>&1; then
    c_cyan "Creating the shared Cove network (lucidcove-net)…"
    docker network create lucidcove-net >/dev/null
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx lucidcove-caddy; then
    # Ports 80/443 preflight (the one pair the port picker above can't relocate:
    # the shared Caddy MUST own them). A box already running nginx/apache/another
    # proxy used to abort mid-install on docker's raw "port is already allocated"
    # with no remedy. Detect the listener, name it, say what to do.
    PORT_CONFLICT=""
    for _p in 80 443; do
      if (exec 3<>"/dev/tcp/127.0.0.1/${_p}") 2>/dev/null; then
        exec 3>&- 2>/dev/null || true
        _who="$(lsof -nP -iTCP:${_p} -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1}')"
        PORT_CONFLICT="${PORT_CONFLICT:+$PORT_CONFLICT, }${_p}${_who:+ ($_who)}"
      fi
    done
    if [ -n "$PORT_CONFLICT" ]; then
      c_red "Port(s) already in use: $PORT_CONFLICT — your Cove's web routing needs 80 and 443."
      echo "  Something on this box (usually nginx, apache, or another reverse proxy) is"
      echo "  listening there. Stop or reconfigure it, e.g.:"
      echo "     sudo systemctl stop nginx && sudo systemctl disable nginx"
      echo "  then re-run: bash install.sh"
      exit 1
    fi
    c_cyan "Starting the shared Caddy (owns 80/443 for every Cove on this box)…"
    ( cd "$SHARED_CADDY_DIR" && COVE_CORE="$CORE" docker compose up -d --build )
  else
    c_dim "  shared Caddy already running — leaving it as is."
  fi
fi

# ── 5. Start the Cove (first run pulls images + builds — a few minutes) ───────
# Build the app/voice images up front (foreground, so build errors still surface),
# THEN bring the whole stack up in the BACKGROUND. The wizard only needs the app to
# render; Matrix/Dendrite (Connect & chat) pulls a big homeserver image and generates
# its signing key on first boot, which used to make `up -d` sit for many minutes before
# the link was ever handed over. Backgrounding the bring-up lets step 6 hand you the
# link the moment the APP answers, while Matrix finishes coming up on its own.
c_cyan "Starting your Lucid Cove (first run builds the app + voice — a few minutes)…"
( cd "$DIR" && docker compose build )
c_dim "  app built — bringing the Cove online (Matrix/Connect finishes in the background)…"
( cd "$DIR" && nohup docker compose up -d >/tmp/lucidcove-up.log 2>&1 & )

# ── 6. Wait until the Cove is actually answering before handing over the link ──
# First boot downloads voice models + indexes the knowledge base, so the app isn't
# ready the instant the containers start. Poll its health endpoint so the wizard never
# loads against a half-booted app. The shared Caddy has no route for this Cove yet (the
# routing snippet is written when an address is claimed), so poll the Cove's own
# published app port on localhost, not the shared :80.
CLAIM="$(grep -Eo 'https?://[^ )]+/p/[A-Za-z0-9_-]+' "$DIR/NEXT_STEPS.md" 2>/dev/null | head -1 || true)"
# Derive the localhost base (scheme://host[:port]) from the claim URL for the health poll.
POLL_BASE="$(printf '%s' "${CLAIM:-http://localhost:8200/}" | grep -Eo '^https?://[^/]+' || true)"
[ -z "$POLL_BASE" ] && POLL_BASE="http://localhost:8200"
c_yellow "First boot takes a minute or two — your Cove is downloading its voice models"
c_yellow "and indexing its knowledge base. Hang tight; I'll hand you the link the moment"
c_yellow "it's ready. (If a page ever looks empty, give it a few seconds and refresh.)"
printf "Getting your Cove ready"
COVE_READY=
for _i in $(seq 1 150); do
  if curl -fsS -o /dev/null "$POLL_BASE/api/system/ping" 2>/dev/null; then COVE_READY=1; break; fi
  printf "."; sleep 2
done
printf "\n"

# ── 7. Where to go ───────────────────────────────────────────────────────────
if [ -n "$COVE_READY" ]; then
  c_cyan "Your Lucid Cove is ready. Open this to finish setup in your browser:"
else
  c_cyan "Your Lucid Cove is still starting (taking longer than usual). Give it another"
  c_cyan "minute, then open this — refresh once if a page looks empty:"
fi
echo "  ${CLAIM:-$POLL_BASE/}"
c_dim "  (On the box itself this localhost link works and the mic/voice work too —"
c_dim "   it's a secure context. Once you claim an address in the wizard, the shared"
c_dim "   Caddy routes it on 80/443 over HTTPS. To reach the Cove from other devices,"
c_dim "   claim an address and join the mesh.)"
# Install-end = the link and nothing else (run-3 B4, Chords): the nags inside
# the Cove own the ENTIRE go-live flow (Set Address walks mesh + address in
# order). No trailing "when you're ready" coaching, no headless/NEXT_STEPS
# pointer — that text re-nagged what the wizard already handles and kept
# creeping back. Headless users find NEXT_STEPS.md in the out/ dir on their own.
