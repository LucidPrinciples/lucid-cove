#!/usr/bin/env bash
# =============================================================================
# cove-lifecycle.sh — spin up / tear down a centralized Cove for the
# debug -> delete -> repeat test loop.
#
#   ./cove-lifecycle.sh new      <config.yaml> [outdir]   generate a Cove folder
#   ./cove-lifecycle.sh up       <cove-dir>               build + start the stack
#   ./cove-lifecycle.sh logs     <cove-dir>               follow the app logs
#   ./cove-lifecycle.sh status   <cove-dir>               show container status
#   ./cove-lifecycle.sh down     <cove-dir>               STOP + DELETE everything (incl. volumes/data)
#   ./cove-lifecycle.sh redo     <config.yaml> [outdir]   down (if exists) -> new -> up, one shot
#
# "down" is intentionally destructive (compose down -v) so each test starts clean.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${1:-help}"

_appcid() { grep -oE '^name: .*' "$1/docker-compose.yml" | awk '{print $2}'; }

case "$cmd" in
  new)
    cfg="${2:?usage: new <config.yaml> [outdir]}"; out="${3:-$(dirname "$cfg")}"
    python3 "$HERE/centralized.py" "$cfg" --output "$out"
    ;;

  up)
    dir="${2:?usage: up <cove-dir>}"
    ( cd "$dir" && docker compose up -d --build )
    echo "Up. App:    http://localhost:$(grep -oE 'PORT: [0-9]+' "$dir/docker-compose.yml" | head -1 | awk '{print $2}')"
    echo "    Nextcloud: $(grep -oE '"[0-9]+:80"' "$dir/docker-compose.yml" | head -1 | tr -d '\"')"
    claim=$(grep -oE 'https?://[^ )]+/p/[A-Za-z0-9_-]+' "$dir/NEXT_STEPS.md" | head -1)
    [ -n "$claim" ] && echo "★ Claim your Cove (→ setup wizard): $claim"
    ;;

  logs)
    dir="${2:?usage: logs <cove-dir>}"
    ( cd "$dir" && docker compose logs -f app )
    ;;

  status)
    dir="${2:?usage: status <cove-dir>}"
    ( cd "$dir" && docker compose ps )
    ;;

  down)
    dir="${2:?usage: down <cove-dir>}"
    # Best-effort: drop hub registry row + Cloudflare DNS so throwaway coves
    # don't strand records (mirrors ensure_cove_dns on the way up).
    if [ -f "$dir/cove.yaml" ] || [ -f "$dir/cove.config.yaml" ]; then
      cfg="$dir/cove.yaml"; [ -f "$cfg" ] || cfg="$dir/cove.config.yaml"
      # Prefer hub DELETE (registry + DNS). Falls back to local remove_dns if no hub auth.
      HERE="$HERE" CFG="$cfg" python3 - <<'PY' 2>/dev/null || true
import os, re, sys, urllib.request
text = open(os.environ["CFG"]).read()
def grab(key):
    m = re.search(r"^\s*" + re.escape(key) + r"\s*:\s*[\"']?([^\s\"']+)", text, re.M)
    return (m.group(1).strip() if m else "")
cove_id = grab("id") or grab("cove_id")
domain = grab("domain")
base = (os.environ.get("LP_REGISTRY_URL") or "").rstrip("/")
secret = os.environ.get("LP_REGISTRY_SECRET") or ""
if base and secret and cove_id:
    req = urllib.request.Request(base + "/api/registry/cove/" + cove_id, method="DELETE")
    req.add_header("X-Registry-Secret", secret)
    req.add_header("User-Agent", "LucidCove-Cove/1.0")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("registry+dns:", r.read().decode()[:200])
    except Exception as e:
        print("registry delete skipped:", e)
elif domain and os.environ.get("CLOUDFLARE_API_TOKEN"):
    sys.path.insert(0, os.environ.get("HERE", "."))
    try:
        from cloudflare_dns import remove_cove_dns
        print("dns:", remove_cove_dns(domain))
    except Exception as e:
        print("dns remove skipped:", e)
PY
    fi
    echo "Tearing down $(_appcid "$dir") INCLUDING volumes/data..."
    ( cd "$dir" && docker compose down -v )
    echo "Gone. Re-run 'new' + 'up' for a clean test."
    ;;

  redo)
    cfg="${2:?usage: redo <config.yaml> [outdir]}"; out="${3:-$(dirname "$cfg")}"
    cove_id="$(grep -oE '^[[:space:]]+id:[[:space:]]*"?[a-z0-9_-]+' "$cfg" | head -1 | grep -oE '[a-z0-9_-]+$')"
    dir="$out/${cove_id}-cove"
    if [ -d "$dir" ]; then
      echo "Existing $dir — tearing it down first..."
      ( cd "$dir" && docker compose down -v 2>/dev/null || true )
      rm -rf "$dir"
    fi
    python3 "$HERE/centralized.py" "$cfg" --output "$out"
    ( cd "$dir" && docker compose up -d --build )
    echo "Fresh Cove up at $dir"
    claim=$(grep -oE 'https?://[^ )]+/p/[A-Za-z0-9_-]+' "$dir/NEXT_STEPS.md" | head -1)
    [ -n "$claim" ] && echo "★ Claim your Cove (→ setup wizard): $claim"
    ;;

  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}"
    ;;
esac
