#!/usr/bin/env python3
"""
enable_tunnel.py — one-time HOST-side step to make a self-host Cove publicly reachable
via a Cloudflare named tunnel (so a REMOTE invitee can open a /join link).

Run this ON THE COVE'S MACHINE (it starts a docker container + reads the CF env there).
It is idempotent and OFF by default — nothing changes until an owner runs it. The invitee
never touches any of this; reachability is a property of the Cove, set once.

What it does:
  1. Create-or-reuse a Cloudflare named tunnel for this Cove (cloudflare_tunnel.ensure_tunnel).
  2. Configure its ingress → the box's bundled Caddy (publishes :443 on the host), which
     host-routes each subdomain (MC / cloud. / voice. / matrix. / {handle}.).
  3. Run the `cloudflared` container (persistent, --restart unless-stopped, host network).
  4. Repoint DNS: *.{domain} + {domain} CNAME (proxied) → {tunnel}.cfargotunnel.com.

Prereqs (host env or the Cove's docker/.env):
  CLOUDFLARE_API_TOKEN   — Account:Cloudflare Tunnel:Edit + Zone:DNS:Edit
  CLOUDFLARE_ACCOUNT_ID  — the account that owns the tunnel

Usage:
  python3 provision/enable_tunnel.py --domain smith.lucidcove.org --cove-id lucidcove-xxxx
    [--caddy-origin https://localhost:443] [--network host] [--dry-run]
"""
import argparse
import os
import subprocess
import sys

# Allow running from the repo root (provision/ is a package sibling of src/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provision import cloudflare_tunnel, cloudflare_dns  # noqa: E402


def _load_env_files(cove_id: str) -> None:
    """Best-effort load CF creds from a nearby instance .env if not already in the shell."""
    if os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        return
    import glob
    candidates = glob.glob(os.path.expanduser(f"~/cove-*/out/{cove_id}*/docker/.env"))
    candidates += glob.glob(os.path.expanduser("~/cove-*/docker/.env"))
    candidates += ["docker/.env", ".env"]
    for path in candidates:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID") and not os.getenv(k):
                        os.environ[k] = v
        except Exception:
            continue


def _run_cloudflared(cove_id: str, token: str, network: str, dry_run: bool) -> dict:
    name = f"{cove_id}-cloudflared"
    # Replace any prior container so re-running picks up a fresh token cleanly.
    rm = ["docker", "rm", "-f", name]
    run = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped",
           "--network", network, "cloudflare/cloudflared:latest",
           "tunnel", "--no-autoupdate", "run", "--token", token]
    if dry_run:
        return {"ok": True, "dry_run": True,
                "commands": [" ".join(rm), " ".join(run[:-1] + ["<token>"])]}
    subprocess.run(rm, capture_output=True, text=True)   # ignore "no such container"
    r = subprocess.run(run, capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[:400]}
    return {"ok": True, "container": name, "id": (r.stdout or "").strip()[:12]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="the Cove's public domain, e.g. smith.lucidcove.org")
    ap.add_argument("--cove-id", required=True, help="the Cove id (names the cloudflared container + tunnel)")
    ap.add_argument("--caddy-origin", default="https://localhost:443",
                    help="where cloudflared forwards on the box (the bundled Caddy). "
                         "Use https://{cove_id}-caddy:443 if you pass --network {cove_net}.")
    ap.add_argument("--network", default="host",
                    help="docker network for cloudflared (default: host, so localhost:443 reaches Caddy)")
    ap.add_argument("--wildcard", action="store_true",
                    help="ALSO proxy *.{domain} through the tunnel (Cloudflare Enterprise only). "
                         "Default: apex-only, leaving subdomains (incl. matrix.{domain}) on the mesh.")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen; touch nothing")
    args = ap.parse_args()

    domain = args.domain.strip().lstrip("*").lstrip(".")
    _load_env_files(args.cove_id)
    try:
        cloudflare_tunnel._token(); cloudflare_tunnel._account_id()
    except Exception as e:
        print(f"✗ {e}")
        print("  Set CLOUDFLARE_API_TOKEN (Tunnel:Edit + DNS:Edit) and CLOUDFLARE_ACCOUNT_ID.")
        return 1

    tunnel_name = f"cove-{args.cove_id}"
    print(f"→ Ensuring named tunnel '{tunnel_name}' …")
    try:
        tun = cloudflare_tunnel.ensure_tunnel(tunnel_name)
    except Exception as e:
        print(f"✗ tunnel create/lookup failed: {e}")
        return 1
    print(f"  tunnel id {tun['id']}  →  {tun['hostname']}")

    print(f"→ Configuring ingress → {args.caddy_origin} (origin {domain}) …")
    try:
        cloudflare_tunnel.put_ingress(tun["id"], domain, origin=args.caddy_origin)
    except Exception as e:
        print(f"✗ ingress config failed: {e}")
        return 1

    print(f"→ Starting cloudflared container (network={args.network}) …")
    cf = _run_cloudflared(args.cove_id, tun["token"], args.network, args.dry_run)
    if not cf.get("ok"):
        print(f"✗ cloudflared failed: {cf.get('error')}")
        return 1
    if cf.get("dry_run"):
        for c in cf["commands"]:
            print("    " + c)
    else:
        print(f"  container {cf['container']} up ({cf['id']})")

    _scope = f"*.{domain} + {domain}" if args.wildcard else domain
    print(f"→ Repointing DNS ({_scope} → {tun['hostname']}, proxied) …")
    if args.dry_run:
        print("    (dry-run — DNS untouched)")
    else:
        try:
            res = cloudflare_dns.ensure_cove_dns_tunnel(domain, tun["id"], wildcard=args.wildcard)
            for a in res["actions"]:
                print("    " + a)
        except Exception as e:
            print(f"✗ DNS repoint failed: {e}")
            return 1

    print(f"\n✓ {domain} is now publicly reachable via Cloudflare tunnel. "
          f"Remote /join links will resolve from any device.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
