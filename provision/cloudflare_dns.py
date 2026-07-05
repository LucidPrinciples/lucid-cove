#!/usr/bin/env python3
"""
cloudflare_dns.py — auto-provision a Cove's DNS via the Cloudflare API.

A Cove is a private mesh node; its domains are Cloudflare records pointing at the
machine's MESH IP (Tailscale/Headscale, 100.64.0.0/10). One wildcard per Cove makes
every subdomain resolve — matrix.{cove}.{base}, cloud., stuart., {handle}., ... — so
the operator NEVER touches DNS. Reuses the same Cloudflare API token Caddy already
uses for its DNS-01 TLS challenge (CLOUDFLARE_API_TOKEN).

Records created (idempotent — create if absent, update if the IP changed):
  *.{cove_domain}   A -> mesh IP   (every service subdomain)
  {cove_domain}     A -> mesh IP   (the apex, so it loads too — fixes #25)

DNS-only (NOT proxied): mesh IPs in 100.64.0.0/10 are not Cloudflare-routable, so
the orange cloud must be off; clients reach the box over the mesh.

Env:
  CLOUDFLARE_API_TOKEN   (required) — same token Caddy uses
  CLOUDFLARE_ZONE_ID     (optional) — else looked up from the base domain
  CLOUDFLARE_ZONE_NAME   (optional) — registrable zone, else derived (last 2 labels)

Usage (CLI, run on the Cove's machine so the mesh IP is local):
  python3 cloudflare_dns.py <cove_domain> [target_ip]
    cove_domain : e.g. testcove.lucidcove.org
    target_ip   : mesh IP; if omitted, auto-detected via `tailscale ip -4`
"""
import os
import subprocess
import sys

import httpx

CF_API = "https://api.cloudflare.com/client/v4"


def _token() -> str:
    tok = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("CLOUDFLARE_API_TOKEN not set (reuse the token Caddy uses for DNS-01)")
    return tok


def _headers() -> dict:
    return {"Authorization": "Bearer " + _token(), "Content-Type": "application/json"}


def _zone_name(cove_domain: str) -> str:
    """Registrable zone for a cove domain. Override with CLOUDFLARE_ZONE_NAME for
    multi-label TLDs (e.g. co.uk). Default = last two labels (lucidcove.org)."""
    z = os.getenv("CLOUDFLARE_ZONE_NAME", "").strip()
    if z:
        return z
    return ".".join(cove_domain.split(".")[-2:])


def _detect_mesh_ip() -> str:
    """The machine's Tailscale/Headscale mesh IPv4 (100.64.0.0/10)."""
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=10)
        ip = (out.stdout or "").strip().splitlines()[0].strip()
        if ip:
            return ip
    except Exception:
        pass
    raise RuntimeError("Could not auto-detect mesh IP; pass target_ip explicitly")


def _client() -> httpx.Client:
    return httpx.Client(timeout=20.0, headers=_headers())


def _zone_id(client: httpx.Client, cove_domain: str) -> str:
    zid = os.getenv("CLOUDFLARE_ZONE_ID", "").strip()
    if zid:
        return zid
    zone = _zone_name(cove_domain)
    r = client.get(CF_API + "/zones", params={"name": zone})
    r.raise_for_status()
    result = r.json().get("result") or []
    if not result:
        raise RuntimeError(f"Cloudflare zone not found for {zone} (set CLOUDFLARE_ZONE_ID)")
    return result[0]["id"]


def _ensure_record(client: httpx.Client, zone_id: str, name: str, ip: str) -> str:
    """Create or update one DNS-only A record name -> ip. Returns the action taken."""
    r = client.get(CF_API + f"/zones/{zone_id}/dns_records", params={"type": "A", "name": name})
    r.raise_for_status()
    existing = r.json().get("result") or []
    body = {"type": "A", "name": name, "content": ip, "ttl": 60, "proxied": False}
    if existing:
        rec = existing[0]
        if rec.get("content") == ip and rec.get("proxied") is False:
            return f"ok (unchanged) {name} -> {ip}"
        u = client.put(CF_API + f"/zones/{zone_id}/dns_records/{rec['id']}", json=body)
        u.raise_for_status()
        return f"updated {name} -> {ip}"
    c = client.post(CF_API + f"/zones/{zone_id}/dns_records", json=body)
    c.raise_for_status()
    return f"created {name} -> {ip}"


def _ensure_cname(client: httpx.Client, zone_id: str, name: str, target: str) -> str:
    """Create or update one DNS-only CNAME name -> target. Returns the action taken.
    Used for acme-dns challenge delegation (_acme-challenge.{sub} -> {fulldomain}.acme-dns)
    so a self-host box can satisfy DNS-01 WITHOUT ever holding our Cloudflare token."""
    name = name.rstrip(".")
    target = target.rstrip(".")
    r = client.get(CF_API + f"/zones/{zone_id}/dns_records", params={"type": "CNAME", "name": name})
    r.raise_for_status()
    existing = r.json().get("result") or []
    body = {"type": "CNAME", "name": name, "content": target, "ttl": 60, "proxied": False}
    if existing:
        rec = existing[0]
        if (rec.get("content") or "").rstrip(".") == target and rec.get("proxied") is False:
            return f"ok (unchanged) {name} -> {target}"
        u = client.put(CF_API + f"/zones/{zone_id}/dns_records/{rec['id']}", json=body)
        u.raise_for_status()
        return f"updated {name} -> {target}"
    c = client.post(CF_API + f"/zones/{zone_id}/dns_records", json=body)
    c.raise_for_status()
    return f"created {name} -> {target}"


def ensure_acme_challenge_cname(sub_domain: str, fulldomain: str) -> dict:
    """Delegate DNS-01 for one subdomain to acme-dns: create the CNAME
    `_acme-challenge.{sub_domain} -> {fulldomain}` (the acme-dns account's fulldomain).
    We hold the Cloudflare token and create this record hub-side; the self-host box
    then only ever writes the TXT at {fulldomain} via its scoped acme-dns credential.
    Idempotent. Returns {ok, action}."""
    sub_domain = (sub_domain or "").strip().rstrip(".")
    fulldomain = (fulldomain or "").strip().rstrip(".")
    if not sub_domain or not fulldomain:
        raise ValueError("sub_domain and fulldomain are required")
    name = f"_acme-challenge.{sub_domain}"
    with _client() as client:
        zid = _zone_id(client, sub_domain)
        action = _ensure_cname(client, zid, name, fulldomain)
    return {"ok": True, "zone_id": zid, "action": action}


def ensure_cove_dns(cove_domain: str, target_ip: str = "") -> dict:
    """Ensure *.{cove_domain} and {cove_domain} both resolve to the Cove's mesh IP.
    Idempotent. Returns {ok, ip, actions:[...]}."""
    cove_domain = cove_domain.strip().rstrip(".")
    if not cove_domain:
        raise ValueError("cove_domain is required")
    ip = (target_ip or "").strip() or _detect_mesh_ip()
    with _client() as client:
        zid = _zone_id(client, cove_domain)
        actions = [
            _ensure_record(client, zid, f"*.{cove_domain}", ip),
            _ensure_record(client, zid, cove_domain, ip),
        ]
    return {"ok": True, "ip": ip, "zone_id": zid, "actions": actions}


def main():
    if len(sys.argv) < 2:
        print("usage: cloudflare_dns.py <cove_domain> [target_ip]")
        sys.exit(1)
    cove_domain = sys.argv[1]
    target_ip = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        res = ensure_cove_dns(cove_domain, target_ip)
    except Exception as e:
        print(f"DNS provisioning FAILED for {cove_domain}: {e}")
        sys.exit(1)
    print(f"DNS ready for {cove_domain} (mesh IP {res['ip']}):")
    for a in res["actions"]:
        print("  " + a)


if __name__ == "__main__":
    main()
