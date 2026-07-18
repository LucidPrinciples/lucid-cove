#!/usr/bin/env bash
# =============================================================================
# probe-host-reachability.sh — MESH3 L2 host punchability probe
# =============================================================================
# Run on the COVE HOST (not inside the app container). Writes a JSON status
# file the Mission Control Attention card reads from the bind-mounted config
# dir. Does not open ports, change firewalls, or touch Headscale ACLs.
#
# Usage:
#   bash scripts/probe-host-reachability.sh --out /path/to/cove/config/host_reachability.json
#
# Exit 0 always when it could write a report (including "hard to reach").
# Exit 2 only if --out is missing / unwritable.
# =============================================================================
set -euo pipefail

OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$OUT" ]; then
  echo "usage: $0 --out /path/to/host_reachability.json" >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT")"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
TS_BIN="$(command -v tailscale 2>/dev/null || true)"
NETCHECK_RAW=""
STATUS_HINT=""
UDP=""
IPV4=""
IPV6=""
MAPPING_VARIES=""
PORT_MAPPING=""
NEAREST_DERP=""
HARD="false"
REASON=""
OK="true"

if [ -z "$TS_BIN" ]; then
  OK="false"
  HARD="true"
  REASON="tailscale_not_installed"
  STATUS_HINT="Install Tailscale on this box, join the Cove mesh, then re-run this probe."
else
  # netcheck can take ~10s; keep a hard ceiling so the operator isn't stuck.
  set +e
  NETCHECK_RAW="$("$TS_BIN" netcheck 2>&1)"
  NC_EC=$?
  set -e
  if [ "$NC_EC" -ne 0 ] && [ -z "$NETCHECK_RAW" ]; then
    OK="false"
    HARD="true"
    REASON="netcheck_failed"
    STATUS_HINT="tailscale netcheck failed — is tailscaled running and is this box on the mesh?"
  else
    # Parse the human netcheck report (stable enough across client versions).
    UDP="$(printf '%s\n' "$NETCHECK_RAW" | sed -n 's/.*UDP:[[:space:]]*//p' | head -1 | tr -d '\r' | awk '{print tolower($1)}')"
    IPV4="$(printf '%s\n' "$NETCHECK_RAW" | sed -n 's/.*IPv4:[[:space:]]*//p' | head -1 | tr -d '\r')"
    IPV6="$(printf '%s\n' "$NETCHECK_RAW" | sed -n 's/.*IPv6:[[:space:]]*//p' | head -1 | tr -d '\r')"
    MAPPING_VARIES="$(printf '%s\n' "$NETCHECK_RAW" | sed -n 's/.*MappingVariesByDestIP:[[:space:]]*//p' | head -1 | tr -d '\r' | awk '{print tolower($1)}')"
    PORT_MAPPING="$(printf '%s\n' "$NETCHECK_RAW" | sed -n 's/.*PortMapping:[[:space:]]*//p' | head -1 | tr -d '\r')"
    NEAREST_DERP="$(printf '%s\n' "$NETCHECK_RAW" | sed -n 's/.*Nearest DERP:[[:space:]]*//p' | head -1 | tr -d '\r')"

    # Classify "hard to reach" for a Cove HOST (one reachable end helps the whole family):
    #  - UDP broken
    #  - hard/symmetric NAT (mapping varies by dest)
    #  - no UPnP/NAT-PMP/PCP port mapping (empty PortMapping) on IPv4
    # Easy: UDP works, mapping stable, and a port mapping exists (router helped).
    pm_trim="$(printf '%s' "$PORT_MAPPING" | tr -d '[:space:]')"
    if [ "$UDP" != "true" ] && [ "$UDP" != "yes" ]; then
      HARD="true"
      REASON="udp_blocked_or_broken"
      STATUS_HINT="This box cannot use UDP on the mesh path. Check local firewalls; prefer enabling UPnP/NAT-PMP on the router or forwarding UDP 41641 to this machine."
    elif [ "$MAPPING_VARIES" = "true" ] || [ "$MAPPING_VARIES" = "yes" ]; then
      HARD="true"
      REASON="hard_nat_mapping_varies"
      STATUS_HINT="Router NAT looks hard (mapping changes by destination). Enable UPnP/NAT-PMP if available, or forward UDP 41641 to this machine and give it a DHCP reservation."
    elif [ -z "$pm_trim" ]; then
      HARD="true"
      REASON="no_port_mapping"
      STATUS_HINT="No UPnP/NAT-PMP port mapping from the router. Enabling UPnP (or forwarding UDP 41641 to this box) makes punched paths much more reliable for every family device."
    else
      HARD="false"
      REASON="ok_port_mapped"
      STATUS_HINT="Router is helping with a mapped UDP port — host reachability looks good."
    fi
  fi
fi

# Optional mesh IP for the card.
MESH_IP=""
if [ -n "$TS_BIN" ]; then
  MESH_IP="$("$TS_BIN" ip -4 2>/dev/null | head -1 | tr -d '\r' || true)"
fi

# JSON escape helper (stdin-safe for multiline netcheck dumps)
json_esc() {
  printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

NC_JSON="$(json_esc "$NETCHECK_RAW")"
HINT_JSON="$(json_esc "$STATUS_HINT")"
IPV4_JSON="$(json_esc "$IPV4")"
IPV6_JSON="$(json_esc "$IPV6")"
PM_JSON="$(json_esc "$PORT_MAPPING")"
DERP_JSON="$(json_esc "$NEAREST_DERP")"
MESH_JSON="$(json_esc "$MESH_IP")"
REASON_JSON="$(json_esc "$REASON")"

cat >"$OUT" <<EOF
{
  "version": 1,
  "ts": "$TS",
  "ok": $OK,
  "hard_to_reach": $HARD,
  "reason": $REASON_JSON,
  "hint": $HINT_JSON,
  "udp": $( [[ "$UDP" == "true" || "$UDP" == "yes" ]] && echo true || echo false ),
  "mapping_varies_by_dest_ip": $( [[ "$MAPPING_VARIES" == "true" || "$MAPPING_VARIES" == "yes" ]] && echo true || echo false ),
  "port_mapping": $PM_JSON,
  "ipv4": $IPV4_JSON,
  "ipv6": $IPV6_JSON,
  "nearest_derp": $DERP_JSON,
  "mesh_ip": $MESH_JSON,
  "netcheck_raw": $NC_JSON
}
EOF

echo "wrote $OUT"
echo "hard_to_reach=$HARD reason=$REASON"
if [ "$HARD" = "true" ]; then
  echo "$STATUS_HINT"
fi
exit 0
