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
  python3 cloudflare_dns.py --remove <cove_domain>
    Delete the apex + wildcard (+ acme-challenge) records for that Cove.
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
    _delete_conflicts(client, zone_id, name, "A")   # CNAME -> A needs the CNAME gone (rollback)
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


def _assert_safe_cove_domain(cove_domain: str) -> str:
    """Normalize and refuse zone-apex / bare-domain targets so remove never wipes the
    whole lucidcove.org zone (or any registrable zone apex)."""
    cove_domain = (cove_domain or "").strip().lower().rstrip(".")
    if not cove_domain:
        raise ValueError("cove_domain is required")
    labels = [p for p in cove_domain.split(".") if p]
    if len(labels) < 3:
        # e.g. lucidcove.org (2 labels) — never a single Cove's DNS bundle
        raise ValueError(
            f"refusing DNS remove/ensure target {cove_domain!r}: need a Cove subdomain "
            f"(e.g. mycove.lucidcove.org), not the zone apex")
    zone = _zone_name(cove_domain)
    if cove_domain == zone:
        raise ValueError(f"refusing DNS op on zone apex {cove_domain!r}")
    return cove_domain


def _delete_all_at_name(client: httpx.Client, zone_id: str, name: str) -> list:
    """Delete every A/AAAA/CNAME record at `name`. Idempotent. Returns action strings."""
    name = name.rstrip(".")
    actions = []
    for t in ("A", "AAAA", "CNAME"):
        r = client.get(CF_API + f"/zones/{zone_id}/dns_records", params={"type": t, "name": name})
        r.raise_for_status()
        for rec in (r.json().get("result") or []):
            rid = rec.get("id")
            if not rid:
                continue
            d = client.delete(CF_API + f"/zones/{zone_id}/dns_records/{rid}")
            d.raise_for_status()
            actions.append(f"deleted {t} {name}")
    if not actions:
        actions.append(f"ok (absent) {name}")
    return actions


def remove_cove_dns(cove_domain: str) -> dict:
    """Deprovision mirror of ensure_cove_dns: delete the Cove's apex + wildcard A/AAAA/CNAME
    records (and the hub-minted `_acme-challenge.{cove}` CNAME if present).

    Idempotent — missing records are reported as absent, not errors. Refuses zone-apex
    targets so a bad call cannot wipe lucidcove.org itself. Returns {ok, domain, zone_id,
    actions:[...]}."""
    cove_domain = _assert_safe_cove_domain(cove_domain)
    names = [
        cove_domain,
        f"*.{cove_domain}",
        f"_acme-challenge.{cove_domain}",
    ]
    with _client() as client:
        zid = _zone_id(client, cove_domain)
        actions = []
        for n in names:
            actions.extend(_delete_all_at_name(client, zid, n))
    return {"ok": True, "domain": cove_domain, "zone_id": zid, "actions": actions}


def ensure_cove_dns_tunnel(cove_domain: str, tunnel_id: str, wildcard: bool = False) -> dict:
    """PUBLIC-reachability variant: point the Cove's domain at its Cloudflare named tunnel
    (CNAME -> {tunnel_id}.cfargotunnel.com, PROXIED) instead of the mesh IP. This is what
    makes a remote invite link resolve from any phone.

    APEX-ONLY by default (just {cove_domain}): the invite link + the post-signup door both
    land on the Cove ROOT, so the apex is all a remote invite needs — and leaving *.{domain}
    on the mesh A record keeps matrix.{domain} (federation) off the public proxy. Pass
    wildcard=True to ALSO proxy *.{domain} — note a PROXIED wildcard is Cloudflare
    Enterprise-only, so on a normal plan this stays apex-only.

    Proxied=True is REQUIRED for cfargotunnel targets. Any conflicting A/AAAA at the name is
    removed first (CF forbids a CNAME coexisting with an A record). Idempotent."""
    cove_domain = cove_domain.strip().rstrip(".")
    tunnel_id = (tunnel_id or "").strip()
    if not cove_domain or not tunnel_id:
        raise ValueError("cove_domain and tunnel_id are required")
    target = f"{tunnel_id}.cfargotunnel.com"
    names = [cove_domain] + ([f"*.{cove_domain}"] if wildcard else [])
    with _client() as client:
        zid = _zone_id(client, cove_domain)
        actions = [_ensure_cname_proxied(client, zid, n, target) for n in names]
    return {"ok": True, "target": target, "zone_id": zid, "actions": actions}


def _delete_conflicts(client: httpx.Client, zone_id: str, name: str, keep_type: str) -> None:
    """Delete any records at `name` whose type conflicts with `keep_type`. CNAME can't
    coexist with A/AAAA (and vice-versa), so repointing A<->CNAME must purge the other."""
    name = name.rstrip(".")
    for t in ("A", "AAAA", "CNAME"):
        if t == keep_type:
            continue
        r = client.get(CF_API + f"/zones/{zone_id}/dns_records", params={"type": t, "name": name})
        r.raise_for_status()
        for rec in (r.json().get("result") or []):
            d = client.delete(CF_API + f"/zones/{zone_id}/dns_records/{rec['id']}")
            d.raise_for_status()


def _ensure_cname_proxied(client: httpx.Client, zone_id: str, name: str, target: str) -> str:
    """Create/update a PROXIED CNAME name -> target (for cfargotunnel tunnel records).
    Purges any conflicting A/AAAA at the name first."""
    name = name.rstrip(".")
    target = target.rstrip(".")
    r = client.get(CF_API + f"/zones/{zone_id}/dns_records", params={"type": "CNAME", "name": name})
    r.raise_for_status()
    existing = r.json().get("result") or []
    body = {"type": "CNAME", "name": name, "content": target, "ttl": 1, "proxied": True}
    if existing:
        rec = existing[0]
        if (rec.get("content") or "").rstrip(".") == target and rec.get("proxied") is True:
            return f"ok (unchanged) {name} -> {target}"
        u = client.put(CF_API + f"/zones/{zone_id}/dns_records/{rec['id']}", json=body)
        u.raise_for_status()
        return f"updated {name} -> {target}"
    _delete_conflicts(client, zone_id, name, "CNAME")   # A/AAAA -> CNAME needs the A gone
    c = client.post(CF_API + f"/zones/{zone_id}/dns_records", json=body)
    c.raise_for_status()
    return f"created {name} -> {target}"


def main():
    if len(sys.argv) < 2:
        print("usage: cloudflare_dns.py <cove_domain> [target_ip]")
        print("       cloudflare_dns.py --remove <cove_domain>")
        sys.exit(1)
    if sys.argv[1] in ("--remove", "remove", "delete"):
        if len(sys.argv) < 3:
            print("usage: cloudflare_dns.py --remove <cove_domain>")
            sys.exit(1)
        cove_domain = sys.argv[2]
        try:
            res = remove_cove_dns(cove_domain)
        except Exception as e:
            print(f"DNS remove FAILED for {cove_domain}: {e}")
            sys.exit(1)
        print(f"DNS removed for {res['domain']}:")
        for a in res["actions"]:
            print("  " + a)
        return
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
