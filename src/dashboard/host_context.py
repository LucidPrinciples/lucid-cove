"""
Host-based routing context for Centralized Coves.

Scheme:  {label}.{cove-domain}

  - host == cove domain (or unparseable)  -> kind="cove"    (Cove entry / root)
  - label matches a manager name          -> kind="manager" (steward/merchant supervision MC)
  - otherwise                             -> kind="handle"   (an operator/presence door)

The subdomain only SELECTS the door. Authentication (the session cookie) stays the
source of truth for identity and data: get_current_presence always resolves the
cookie's owner, so a mismatched subdomain shows the wrong *door*, never another
presence's data. host_match() reports whether the authenticated session matches the
door the subdomain selected — the frontend uses it to show a scoped login when not.

Infix-agnostic: it strips the configured cove `domain` suffix, so dropping the
legacy ".cove." infix is purely a config change, no code change here.
"""
from typing import Optional


def request_host(request) -> str:
    """The external host for this request (respects Caddy's X-Forwarded-Host)."""
    h = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return h.split(",")[0].split(":")[0].strip().lower()


def resolve_host_context(host: str, cove: dict) -> dict:
    """Classify the request host against the Cove's domain. Safe default: 'cove'."""
    cove_domain = (cove.get("domain") or "").strip().lower()
    host = (host or "").split(":")[0].strip().lower()
    ctx = {"kind": "cove", "label": None, "cove_domain": cove_domain, "host": host}
    if not host or not cove_domain or host == cove_domain:
        return ctx
    suffix = "." + cove_domain
    if not host.endswith(suffix):
        return ctx  # unknown host -> treat as cove root (never leak a door)
    label = host[: -len(suffix)].split(".")[0]
    if not label:
        return ctx
    ctx["label"] = label
    # haven.{cove}.{domain} — the operator-owned Haven door (rides the *.{domain}
    # wildcard; no separate Caddy/DNS). Reserved label, never a real operator handle.
    if label == "haven":
        ctx["kind"] = "haven"
        return ctx
    managers = {
        ((cove.get("steward_channel") or {}).get("name") or "").strip().lower(),
        ((cove.get("merchant_channel") or {}).get("name") or "").strip().lower(),
    } - {""}
    ctx["kind"] = "manager" if label in managers else "handle"
    return ctx


def host_match(ctx: dict, presence: Optional[dict]) -> bool:
    """Does the authenticated session match the door the subdomain selects?"""
    kind = ctx.get("kind")
    if kind == "cove":
        return True
    if not presence:
        return False
    if kind == "handle":
        return (presence.get("username") or "").strip().lower() == (ctx.get("label") or "").strip().lower()
    if kind == "manager":
        return presence.get("cove_role") == "admin"
    if kind == "haven":
        # The Haven door belongs to the Cove admin (the operator who forms/manages it).
        return presence.get("cove_role") == "admin"
    return True
