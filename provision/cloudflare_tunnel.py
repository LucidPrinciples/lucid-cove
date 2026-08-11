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
    apex + `*.{domain}` cover MC, cloud., voice., matrix., and every {handle}. subdomain.

    WARNING: this REPLACES the entire tunnel ingress config. Prefer
    ``ensure_public_hostname`` when adding a single bulk-egress hostname onto an
    existing multi-service tunnel (analytics, etc.) without wiping other routes.
    """
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


def get_ingress(tunnel_id: str) -> list:
    """Return the tunnel's current ingress rule list (may be empty)."""
    tunnel_id = (tunnel_id or "").strip()
    if not tunnel_id:
        raise ValueError("tunnel_id is required")
    acct = _account_id()
    with _client() as client:
        r = client.get(f"{CF_API}/accounts/{acct}/cfd_tunnel/{tunnel_id}/configurations")
        r.raise_for_status()
        result = r.json().get("result") or {}
        cfg = result.get("config") if isinstance(result, dict) else None
        if not isinstance(cfg, dict):
            # Some API shapes nest under result.config; others return config at top
            cfg = result if isinstance(result, dict) else {}
        ingress = cfg.get("ingress") if isinstance(cfg, dict) else None
        return list(ingress) if isinstance(ingress, list) else []


def ensure_public_hostname(
    tunnel_id: str,
    hostname: str,
    service: str,
    *,
    origin_server_name: str = "",
    no_tls_verify: bool = True,
) -> dict:
    """Merge one public hostname into an existing tunnel without wiping other routes.

    Used for Download-pack bulk egress: publish e.g. ``files.example.org`` → local
    Nextcloud (``http://127.0.0.1:8082``) on a tunnel that already serves analytics
    or other hostnames. Catch-all ``http_status:*`` rules stay last.

    Does not touch DNS — pair with ``cloudflare_dns.ensure_hostname_dns_tunnel``.
    """
    tunnel_id = (tunnel_id or "").strip()
    hostname = (hostname or "").strip().rstrip(".").lower()
    service = (service or "").strip()
    if not tunnel_id or not hostname or not service:
        raise ValueError("tunnel_id, hostname, and service are required")

    origin_req: dict = {}
    osn = (origin_server_name or "").strip().rstrip(".")
    if osn:
        origin_req["originServerName"] = osn
    if service.startswith("https://") and no_tls_verify:
        origin_req["noTLSVerify"] = True

    new_rule: dict = {"hostname": hostname, "service": service}
    if origin_req:
        new_rule["originRequest"] = origin_req

    existing = get_ingress(tunnel_id)
    catch_all: list = []
    kept: list = []
    replaced = False
    for rule in existing:
        if not isinstance(rule, dict):
            continue
        # Catch-all: no hostname (CF requires last)
        if not (rule.get("hostname") or "").strip():
            catch_all.append(rule)
            continue
        if (rule.get("hostname") or "").strip().rstrip(".").lower() == hostname:
            kept.append(new_rule)
            replaced = True
        else:
            kept.append(rule)
    if not replaced:
        kept.append(new_rule)
    if not catch_all:
        catch_all = [{"service": "http_status:404"}]
    ingress = kept + catch_all

    acct = _account_id()
    with _client() as client:
        r = client.put(
            f"{CF_API}/accounts/{acct}/cfd_tunnel/{tunnel_id}/configurations",
            json={"config": {"ingress": ingress}},
        )
        r.raise_for_status()
    return {
        "ok": True,
        "tunnel_id": tunnel_id,
        "hostname": hostname,
        "service": service,
        "replaced": replaced,
        "ingress": ingress,
    }
