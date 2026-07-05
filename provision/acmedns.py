#!/usr/bin/env python3
"""
acmedns.py — sovereign DNS-01 for self-host Coves on a lucidcove.org subdomain.

THE PROBLEM: a self-hoster on a private/mesh-only box wants a lucidcove.org
subdomain with a real Let's Encrypt cert. HTTP-01 can't validate a mesh IP, so it
must be DNS-01 — but DNS-01 for *our* zone normally needs *our* Cloudflare token,
which we must never ship to a user box.

THE STANDARD FIX (acme-dns): run an acme-dns server (we host it on the VPS,
authoritative for `acme.lucidcove.org` via NS delegation). For each self-host
subdomain we:
  1) register an acme-dns account → get {username, password, fulldomain, subdomain};
     that credential can ONLY write the TXT at its own {fulldomain} — nothing else.
  2) create the one-time CNAME `_acme-challenge.{sub} -> {fulldomain}` in Cloudflare
     (hub-side, with our token) so Let's Encrypt follows the delegation.
  3) bake the acme-dns credential into the Cove's bundled Caddy (tls dns acmedns).
The user box holds a credential scoped to one challenge record. Token never leaves us.

This module is the hub/provisioner side (steps 1-2 + returning the credential).
Stdlib + httpx (already a dep). Best-effort: returns {ok: False, reason} so the
provisioner degrades to the documented manual path instead of failing.

Env:
  LP_ACMEDNS_URL          — INTERNAL base the hub uses to call /register, e.g.
                            http://127.0.0.1:8081 (register is UNAUTHENTICATED — keep
                            it off the public internet; the hub runs on the same VPS).
  LP_ACMEDNS_PUBLIC_URL   — PUBLIC base the self-host box's Caddy uses for /update,
                            e.g. https://acme.lucidcove.org. Defaults to LP_ACMEDNS_URL.
  (DNS step reuses cloudflare_dns: CLOUDFLARE_API_TOKEN / ZONE_*)
"""
import os

import httpx

try:
    from cloudflare_dns import ensure_acme_challenge_cname  # sibling (CLI / provisioner)
except ImportError:  # packaged import
    from provision.cloudflare_dns import ensure_acme_challenge_cname


def _acmedns_url() -> str:
    return (os.getenv("LP_ACMEDNS_URL", "") or "").strip().rstrip("/")


def _acmedns_public_url() -> str:
    """The base the SELF-HOST box's Caddy uses for /update (its scoped credential
    authenticates that call). Public; defaults to the internal URL if unset."""
    return (os.getenv("LP_ACMEDNS_PUBLIC_URL", "") or _acmedns_url()).strip().rstrip("/")


def register_account(acmedns_url: str = "") -> dict:
    """Register a fresh acme-dns account. Returns the raw acme-dns credential
    {username, password, fulldomain, subdomain, allowfrom}. Raises on transport error."""
    base = (acmedns_url or _acmedns_url())
    if not base:
        raise RuntimeError("LP_ACMEDNS_URL not set (the acme-dns server base URL)")
    with httpx.Client(timeout=20.0) as client:
        r = client.post(base + "/register", json={})
        r.raise_for_status()
        return r.json()


def provision_subdomain_cert_delegation(sub_domain: str, *, acmedns_url: str = "") -> dict:
    """Full hub-side setup for one self-host subdomain's DNS-01:
      register acme-dns account → create the _acme-challenge CNAME → return the
      credential the Cove's Caddy needs. Best-effort; never raises.

    Returns on success:
      {ok: True, acmedns: {server_url, username, password, subdomain, fulldomain},
       cname: <action>}
    The `acmedns` block is what gets written into the Cove (Caddy acme-dns config).
    On failure: {ok: False, reason}."""
    sub_domain = (sub_domain or "").strip().rstrip(".")
    if not sub_domain:
        return {"ok": False, "reason": "sub_domain required"}
    base = (acmedns_url or _acmedns_url())
    if not base:
        return {"ok": False, "reason": "LP_ACMEDNS_URL not set — skipping acme-dns (document manual cert)"}
    try:
        acct = register_account(base)
    except Exception as e:
        return {"ok": False, "reason": f"acme-dns register failed: {str(e)[:160]}"}
    fulldomain = (acct.get("fulldomain") or "").strip()
    if not fulldomain:
        return {"ok": False, "reason": "acme-dns register returned no fulldomain"}
    try:
        cname = ensure_acme_challenge_cname(sub_domain, fulldomain)
    except Exception as e:
        return {"ok": False, "reason": f"CNAME delegation failed: {str(e)[:160]}",
                "acmedns_partial": {"fulldomain": fulldomain}}
    return {
        "ok": True,
        "acmedns": {
            # The CREDENTIAL points at the PUBLIC url — that's where the self-host
            # box's Caddy sends /update, authenticated by this account's user/pass.
            "server_url": _acmedns_public_url(),
            "username": acct.get("username", ""),
            "password": acct.get("password", ""),
            "subdomain": acct.get("subdomain", ""),
            "fulldomain": fulldomain,
        },
        "cname": cname.get("action"),
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: acmedns.py <sub_domain>  (env LP_ACMEDNS_URL + CLOUDFLARE_API_TOKEN)")
        sys.exit(1)
    print(json.dumps(provision_subdomain_cert_delegation(sys.argv[1]), indent=2))
