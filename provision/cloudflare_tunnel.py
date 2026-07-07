#!/usr/bin/env python3
"""
cloudflare_tunnel.py — make a self-host Cove PUBLICLY reachable via a Cloudflare
named tunnel, so a REMOTE invitee (off-mesh phone) can open a /join link.

Why this exists
---------------
A home Cove sits behind NAT with NO inbound ports (mesh-only). DNS-01 cert issuance
needs no inbound, but SERVING the page to an off-mesh browser does. A Cloudflare
named tunnel is a persistent OUTBOUND connection from a `cloudflared` container on the
box to Cloudflare's edge — no port-forward, no exposed home IP, works behind NAT. DNS
for the Cove then points (CNAME, proxied) at `{tunnel_id}.cfargotunnel.com` instead of
the mesh IP, and the invite link works on any phone, anywhere. Reachability is a
one-time HOST-side setup by the owner; the invitee never touches it.

Named tunnel (NOT a quick trycloudflare tunnel): a durable invite link needs a STABLE
URL, which a quick tunnel can't give (its URL changes every restart).

This module is the Cloudflare API half (create/lookup the tunnel, its token, and its
ingress config). `enable_tunnel.py` is the host-side orchestrator that runs cloudflared
and repoints DNS. Everything is OFF by default — nothing here runs unless the owner opts
in and the CF env is present.

Env:
  CLOUDFLARE_API_TOKEN    (required) — needs Account:Cloudflare Tunnel:Edit + Zone:DNS:Edit
                          (a superset of the DNS-01 token; see docs). Reused if it has scope.
  CLOUDFLARE_ACCOUNT_ID   (required) — the Cloudflare account that owns the tunnel.

CF Tunnel API: https://developers.cloudflare.com/api/operations/cloudflare-tunnel-create-a-cloudflare-tunnel
"""
import os
import secrets

import httpx

CF_API = "https://api.cloudflare.com/client/v4"


def _token() -> str:
    tok = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("CLOUDFLARE_API_TOKEN not set (needs Cloudflare Tunnel:Edit scope)")
    return tok


def _account_id() -> str:
    acct = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not acct:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID not set (the account that owns the tunnel)")
    return acct


def _headers() -> dict:
    return {"Authorization": "Bearer " + _token(), "Content-Type": "application/json"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers=_headers())


def _tunnel_hostname(tunnel_id: str) -> str:
    """The CNAME target every proxied Cove record points at."""
    return f"{tunnel_id}.cfargotunnel.com"


def _find_tunnel(client: httpx.Client, acct: str, name: str) -> dict | None:
    """Return the live (non-deleted) tunnel with this name, or None."""
    r = client.get(f"{CF_API}/accounts/{acct}/cfd_tunnel",
                   params={"name": name, "is_deleted": "false"})
    r.raise_for_status()
    for t in (r.json().get("result") or []):
        if t.get("name") == name and not t.get("deleted_at"):
            return t
    return None


def ensure_tunnel(name: str) -> dict:
    """Create-or-reuse a named, Cloudflare-managed (`config_src=cloudflare`) tunnel.

    Idempotent: a tunnel with this name is reused (we don't rotate its secret). Returns
    {id, name, token, hostname} where `token` is the `cloudflared tunnel run --token` value
    and `hostname` is the cfargotunnel CNAME target."""
    name = (name or "").strip()
    if not name:
        raise ValueError("tunnel name is required")
    acct = _account_id()
    with _client() as client:
        t = _find_tunnel(client, acct, name)
        if not t:
            # config_src=cloudflare → ingress is managed via the API (below), not a local
            # config.yml. tunnel_secret is a 32-byte base64 secret CF stores for the tunnel.
            import base64
            secret = base64.b64encode(secrets.token_bytes(32)).decode()
            r = client.post(f"{CF_API}/accounts/{acct}/cfd_tunnel",
                            json={"name": name, "tunnel_secret": secret,
                                  "config_src": "cloudflare"})
            r.raise_for_status()
            t = r.json()["result"]
        tid = t["id"]
        # The run token (opaque; encodes account + tunnel + secret).
        tr = client.get(f"{CF_API}/accounts/{acct}/cfd_tunnel/{tid}/token")
        tr.raise_for_status()
        token = tr.json().get("result") or ""
    return {"id": tid, "name": name, "token": token, "hostname": _tunnel_hostname(tid)}


def put_ingress(tunnel_id: str, domain: str, origin: str = "https://localhost:443") -> dict:
    """Configure the tunnel's ingress: route the Cove apex + every subdomain to the box's
    bundled Caddy (which host-routes each subdomain to the right service). `origin` is where
    cloudflared forwards on the box — the Cove's Caddy publishes 443 on the host, so the
    default `https://localhost:443` works when cloudflared runs on the host network.

    originRequest.originServerName = {domain} so Caddy serves the right vhost/cert; the
    apex + `*.{domain}` cover MC, cloud., voice., matrix., and every {handle}. subdomain."""
    domain = (domain or "").strip().rstrip(".")
    if not tunnel_id or not domain:
        raise ValueError("tunnel_id and domain are required")
    acct = _account_id()
    origin_req = {"originServerName": domain, "noTLSVerify": True}
    ingress = [
        {"hostname": domain, "service": origin, "originRequest": origin_req},
        {"hostname": f"*.{domain}", "service": origin, "originRequest": origin_req},
        {"service": "http_status:404"},   # required catch-all
    ]
    with _client() as client:
        r = client.put(f"{CF_API}/accounts/{acct}/cfd_tunnel/{tunnel_id}/configurations",
                       json={"config": {"ingress": ingress}})
        r.raise_for_status()
    return {"ok": True, "tunnel_id": tunnel_id, "ingress": ingress}
