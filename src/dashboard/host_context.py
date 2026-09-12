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


PUBLIC_TUNER_HOST = "app.lucidtuner.com"


def request_host(request) -> str:
    """The external host for this request (respects Caddy's X-Forwarded-Host)."""
    h = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return h.split(",")[0].split(":")[0].strip().lower()


def is_public_tuner_host(host: str) -> bool:
    """True only for the public Lucid Tuner hostname (not marketing, not Cove)."""
    h = (host or "").split(":")[0].strip().lower()
    return h == PUBLIC_TUNER_HOST


def brand_public_tuner_landing(html: str) -> str:
    """First-paint Lucid Tuner chrome on the signed-out Tuner Host landing."""
    html = html.replace("<title>Lucid Cove</title>", "<title>Lucid Tuner</title>", 1)
    html = html.replace(
        '<img src="/static/lp-mark.png" alt="Lucid Principles" class="logo-icon">',
        '<img src="/static/mark-tuner.png" alt="Lucid Tuner" class="logo-icon">',
        1,
    )
    html = html.replace(
        '<div class="logo-mark">Lucid Principles</div>',
        '<div class="logo-mark">Lucid Tuner</div>',
        1,
    )
    html = html.replace(
        '<div class="tagline">One account for the whole system.<br>Start with the Tuner, grow into a Cove.</div>',
        '<div class="tagline">Align Your Broadcast.<br>Daily Drop, music, one account.</div>',
        1,
    )
    html = html.replace(
        """  <div class="features">
    <div class="features-title">Free with every Lucid Principles account</div>
    <div class="feature-row"><span class="feature-dot"></span> Daily tuning from 22 Lucid Principles</div>
    <div class="feature-row"><span class="feature-dot"></span> Music player — 20+ genres, 7 Signals</div>
    <div class="feature-row"><span class="feature-dot"></span> The full Canon</div>
    <div class="feature-row"><span class="feature-dot"></span> Action board and Quick Lists</div>
    <div class="feature-row"><span class="feature-dot"></span> Tuning mirrors</div>
    <div class="feature-row"><span class="feature-dot"></span> Earn with referrals</div>
  </div>""",
        """  <div class="features">
    <div class="features-title">Free Lucid Tuner</div>
    <div class="feature-row"><span class="feature-dot"></span> One Tune a day — Pro is unlimited</div>
    <div class="feature-row"><span class="feature-dot"></span> Music player and the daily Drop</div>
    <div class="feature-row"><span class="feature-dot"></span> The full Canon</div>
    <div class="feature-row"><span class="feature-dot"></span> One Lucid Tuner account</div>
    <div class="feature-row"><span class="feature-dot"></span> Lucid Cove on Hermes: install on this computer, then paste your connect key from Settings</div>
  </div>""",
        1,
    )
    html = html.replace(
        '<a href="https://lucidcove.org">Learn more at lucidcove.org</a>',
        '<a href="https://lucidtuner.com">lucidtuner.com</a>',
        1,
    )
    return html


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
