#!/usr/bin/env python3
"""
enable_public_cloud.py — publish ONE public hostname for Cove Nextcloud bulk egress.

Why
---
Download pack mints short-lived, read-only per-file share links and hands the
browser a direct Cloud URL. Remote multi-GB pulls need that URL host to ride a
normal public HTTPS path (Cloudflare Tunnel outbound from the house) — not the
mesh-only cloud.{domain} A record.

This script does NOT replace a whole Cove tunnel config (see enable_tunnel.py).
It MERGES one hostname into an existing named tunnel and points DNS at it, then
prints the NEXTCLOUD_PUBLIC_URL value to set on the Cove app so Download pack
mints links on that origin.

Security (product contract)
---------------------------
- Does not disable Nextcloud auth. Login still requires credentials.
- Download pack links remain per-file, read-only, expiring public shares.
- Prefer a dedicated hostname (files.*) over repointing mesh cloud.* unless you
  intentionally want the Cloud UI on the public name too.

Prereqs (host env or nearby docker/.env — never paste tokens into chat):
  CLOUDFLARE_API_TOKEN   — Tunnel:Edit + Zone:DNS:Edit
  CLOUDFLARE_ACCOUNT_ID

Usage (on the Cove host):
  python3 provision/enable_public_cloud.py \\
    --hostname files.example.org \\
    --tunnel-id <existing-tunnel-uuid> \\
    --service http://127.0.0.1:8082

  # Or look up tunnel by name:
  python3 provision/enable_public_cloud.py \\
    --hostname files.example.org \\
    --tunnel-name cove-lucidcove-xxxx \\
    --service http://127.0.0.1:8082

  # Dry run (no API writes):
  python3 provision/enable_public_cloud.py ... --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provision import cloudflare_dns, cloudflare_tunnel  # noqa: E402


def _load_env_files() -> None:
    """Best-effort load CF creds from nearby .env files if not already set."""
    if os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        return
    import glob

    candidates = []
    candidates += glob.glob(os.path.expanduser("~/cove-*/out/*/docker/.env"))
    candidates += glob.glob(os.path.expanduser("~/Cove*/**/docker/.env"))
    candidates += glob.glob(os.path.expanduser("~/*Cove*/docker/.env"))
    candidates += glob.glob(os.path.expanduser("~/.lucidcove/**/.env"))
    candidates += ["docker/.env", ".env"]
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID") and not os.getenv(k):
                        os.environ[k] = v
        except Exception:
            continue


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Publish one public Cloud/files hostname on an existing Cloudflare tunnel"
    )
    ap.add_argument(
        "--hostname",
        required=True,
        help="Public FQDN for bulk egress, e.g. files.example.org "
        "(prefer one label under the zone for Universal SSL)",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tunnel-id", help="Existing Cloudflare tunnel UUID")
    g.add_argument(
        "--tunnel-name",
        help="Existing tunnel name (looked up via API), e.g. cove-lucidcove-xxxx",
    )
    ap.add_argument(
        "--service",
        default="http://127.0.0.1:8082",
        help="Origin URL cloudflared dials on the host "
        "(default http://127.0.0.1:8082 for host-network Nextcloud). "
        "Use https://127.0.0.1:443 only if Caddy terminates TLS for this host.",
    )
    ap.add_argument(
        "--origin-server-name",
        default="",
        help="Optional TLS SNI / Host override when service is https://…",
    )
    ap.add_argument(
        "--skip-dns",
        action="store_true",
        help="Only merge tunnel ingress; do not create/update the DNS CNAME",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print plan; touch nothing")
    args = ap.parse_args()

    hostname = args.hostname.strip().rstrip(".").lower()
    service = args.service.strip()
    if hostname.count(".") < 1:
        print("✗ hostname must be a full DNS name")
        return 1

    _load_env_files()
    try:
        cloudflare_tunnel._token()
        cloudflare_tunnel._account_id()
    except Exception as e:
        print(f"✗ {e}")
        print("  Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID on the host (do not paste into chat).")
        return 1

    tunnel_id = (args.tunnel_id or "").strip()
    if args.tunnel_name:
        name = args.tunnel_name.strip()
        print(f"→ Looking up tunnel name {name!r} …")
        if args.dry_run:
            print("    (dry-run — skip lookup)")
        else:
            try:
                tun = cloudflare_tunnel.ensure_tunnel(name)
            except Exception as e:
                print(f"✗ tunnel lookup failed: {e}")
                return 1
            tunnel_id = tun["id"]
            print(f"  tunnel id {tunnel_id} → {tun['hostname']}")
            # ensure_tunnel returns a run token; never print it

    if not tunnel_id and not args.dry_run:
        print("✗ tunnel id unresolved")
        return 1

    public_url = f"https://{hostname}"
    print(f"→ Public Cloud base will be: {public_url}")
    print(f"→ Origin service: {service}")
    print(f"→ Tunnel: {tunnel_id or '(dry-run)'}")

    if args.dry_run:
        print("    (dry-run — ingress/DNS untouched)")
        print("\nWhen applied, set on the Cove app container/env:")
        print(f"  NEXTCLOUD_PUBLIC_URL={public_url}")
        print("Then restart the app and use Mission Control → Files → Download pack.")
        return 0

    print(f"→ Merging hostname into tunnel ingress (preserves other routes) …")
    try:
        ing = cloudflare_tunnel.ensure_public_hostname(
            tunnel_id,
            hostname,
            service,
            origin_server_name=args.origin_server_name or "",
        )
    except Exception as e:
        print(f"✗ ingress merge failed: {e}")
        return 1
    print(f"  hostname={ing['hostname']} replaced_existing={ing.get('replaced')} rules={len(ing.get('ingress') or [])}")

    if args.skip_dns:
        print("→ DNS skipped (--skip-dns)")
    else:
        print(f"→ DNS CNAME {hostname} → {tunnel_id}.cfargotunnel.com (proxied) …")
        try:
            dns = cloudflare_dns.ensure_hostname_dns_tunnel(hostname, tunnel_id)
            for a in dns.get("actions") or []:
                print("    " + a)
        except Exception as e:
            print(f"✗ DNS failed: {e}")
            print("  Ingress may already be live; fix DNS then retry or set CNAME in the dashboard.")
            return 1

    print(
        f"\n✓ {hostname} is on the tunnel for bulk file egress.\n"
        f"  Set on this Cove's app env (then restart app):\n"
        f"    NEXTCLOUD_PUBLIC_URL={public_url}\n"
        f"  Smoke: open {public_url}/ (expect Nextcloud login, not a mesh dead-end).\n"
        f"  Download pack will mint https://{hostname}/s/…/download/… links.\n"
        f"  Rotate the tunnel token if it was ever pasted into chat logs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
