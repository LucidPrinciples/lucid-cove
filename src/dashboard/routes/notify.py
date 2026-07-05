"""
Owner / admin notifications (#167 interim hosted-Cove fulfillment).

When Socrates records a hosted-Cove purchase, it POSTs here so the owner gets an email and
can provision the Cove by hand. Secret-gated with SHARED_CONTAINER_SECRET; allowlisted in
app.py PUBLIC_PREFIXES (/api/notify/) because the route enforces the secret itself.
Socrates has no mailer of its own, so all email goes through this (the shared container's Brevo).
"""

import hmac
import html as _html
import os
from src.env import env

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.dashboard.routes.email import send_transactional

router = APIRouter(prefix="/api/notify", tags=["notify"])

SHARED_CONTAINER_SECRET = env("SHARED_CONTAINER_SECRET")


def _ok_secret(body: dict, request: Request) -> bool:
    if not SHARED_CONTAINER_SECRET:
        return False
    supplied = request.headers.get("X-Shared-Secret", "") or str(body.get("secret") or "")
    return bool(supplied) and hmac.compare_digest(supplied, SHARED_CONTAINER_SECRET)


@router.post("/owner-order")
async def owner_order(request: Request):
    """Email the owner that a hosted Cove was purchased (provision-by-hand alert)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not _ok_secret(body, request):
        return JSONResponse(status_code=403, content={"ok": False, "error": "forbidden"})

    owner = body.get("owner_email") or env("OWNER_EMAIL")
    if not owner:
        return JSONResponse(status_code=400, content={"ok": False, "error": "no owner_email"})

    f = {k: _html.escape(str(body.get(k, "") or "")) for k in
         ("customer_email", "cove_name", "handle", "region", "team", "referred_by",
          "plan_type", "session_id")}
    cove = body.get("cove_name") or "(unnamed)"
    region = body.get("region") or "?"
    subject = f"New hosted Cove order — {cove} ({region})"

    rows = "".join(
        f'<tr><td style="padding:4px 14px 4px 0;color:#888;font-size:13px;white-space:nowrap;">{label}</td>'
        f'<td style="padding:4px 0;color:#e8e8ef;font-size:13px;">{f[key] or "—"}</td></tr>'
        for label, key in [
            ("Customer", "customer_email"), ("Cove name", "cove_name"),
            ("Handle", "handle"), ("Region", "region"), ("Config", "team"),
            ("Referred by", "referred_by"), ("Plan", "plan_type"),
            ("Stripe session", "session_id"),
        ])
    html_body = (
        '<!DOCTYPE html><html><body style="margin:0;background:#0e0e14;'
        'font-family:system-ui,-apple-system,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#0e0e14;padding:40px 20px;">'
        '<tr><td align="center">'
        '<table width="540" cellpadding="0" cellspacing="0" style="background:#16161e;border-radius:12px;padding:32px;">'
        '<tr><td style="padding-bottom:8px;"><h1 style="margin:0;font-size:19px;color:#5ce1e6;">'
        'New hosted Cove order</h1></td></tr>'
        f'<tr><td style="padding-bottom:18px;"><p style="margin:0;font-size:14px;color:#9a9aaf;line-height:1.6;">'
        f'Provision this Cove in <b style="color:#e8e8ef;">{f["region"] or "the chosen region"}</b>, '
        'then send the customer their claim link. Recorded in hosted_orders (status: pending).</p></td></tr>'
        f'<tr><td><table cellpadding="0" cellspacing="0">{rows}</table></td></tr>'
        '</table></td></tr></table></body></html>'
    )

    sent = await send_transactional(owner, subject, html_body)
    return JSONResponse(content={"ok": bool(sent), "sent": bool(sent)})
