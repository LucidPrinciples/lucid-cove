#!/usr/bin/env bash
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
# --accept-dns=true: use mesh DNS when available so lucidcove.org mesh A records
# are not dropped by local DNS-rebinding filters (install NXDOMAIN hard-stop).
tailscale up --login-server https://headscale.lucidcove.org --authkey "$KEY" --accept-dns=true \
  || sudo tailscale up --login-server https://headscale.lucidcove.org --authkey "$KEY" --accept-dns=true
# Idempotent if already joined without accept-dns
tailscale set --accept-dns=true 2>/dev/null || sudo tailscale set --accept-dns=true 2>/dev/null || true
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
