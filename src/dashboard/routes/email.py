"""
Brevo transactional email — sign-in links and contact list management.

Uses Brevo v3 REST API (no SDK dependency).
Env vars:
  BREVO_API_KEY         — v3 API key (xkeysib-...)
  BREVO_SENDER_EMAIL    — verified sender (e.g. broadcast@lucidtuner.com)
  BREVO_SENDER_NAME     — display name (e.g. Lucid Tuner)
  BREVO_LIST_ID         — list ID for new signup automation (e.g. 4)
"""

import os
from src.env import env, env_int
import logging

import httpx

log = logging.getLogger(__name__)

BREVO_API_KEY = env("BREVO_API_KEY")
BREVO_SENDER_EMAIL = env("BREVO_SENDER_EMAIL", "signin@lucidprinciples.com")
BREVO_SENDER_NAME = env("BREVO_SENDER_NAME", "Lucid Principles")
BREVO_LIST_ID = env_int("BREVO_LIST_ID", "4")
# The unified account brand (one Lucid Principles account across Tuner/Cove/etc).
EMAIL_PRODUCT_NAME = env("EMAIL_PRODUCT_NAME", "Lucid Principles")

_BREVO_BASE = "https://api.brevo.com/v3"


def _headers():
    return {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# =============================================================================
# Transactional Email — Sign-in Links
# =============================================================================

async def send_signin_link(email: str, signin_link: str, is_signup: bool = False) -> bool:
    """Send a passwordless sign-in link email via Brevo transactional API.

    Args:
        email: recipient address
        signin_link: the full https://... magic link URL
        is_signup: True for first-time signup, False for returning signin

    Returns True on success, False on failure (logs the error).
    """
    if not BREVO_API_KEY:
        log.warning("BREVO_API_KEY not set — skipping email send for %s", email)
        return False

    if is_signup:
        subject = f"Welcome to {EMAIL_PRODUCT_NAME} — your sign-in link"
        heading = f"Welcome to {EMAIL_PRODUCT_NAME}"
        body_text = (
            f"Your {EMAIL_PRODUCT_NAME} account is ready. Click below to sign in. "
            "The link is good for one use; once you're in, you'll stay signed in."
        )
        button_text = "Sign in"
    else:
        subject = f"Your {EMAIL_PRODUCT_NAME} sign-in link"
        heading = "Welcome back"
        body_text = (
            "Click below to sign in to your account. This link is good for one use and "
            "replaces any previous sign-in link."
        )
        button_text = "Sign in"

    html = _build_email_html(heading, body_text, button_text, signin_link)

    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": email}],
        "subject": subject,
        "htmlContent": html,
        "trackClicks": False,
        "trackOpens": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_BREVO_BASE}/smtp/email",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code in (200, 201):
                log.info("Magic link email sent to %s (signup=%s)", email, is_signup)
                return True
            else:
                log.error("Brevo send failed [%d]: %s", resp.status_code, resp.text[:300])
                return False
    except Exception as e:
        log.error("Brevo send error for %s: %s", email, e)
        return False


# =============================================================================
# Generic Transactional Send (owner notifications, etc.)
# =============================================================================

async def send_transactional(to_email: str, subject: str, html: str) -> bool:
    """Generic Brevo transactional send. Used by owner/admin notifications that supply their
    own HTML. Returns True on success, False on failure (logs the error)."""
    if not BREVO_API_KEY:
        log.warning("BREVO_API_KEY not set — skipping email send to %s", to_email)
        return False

    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
        "trackClicks": False,
        "trackOpens": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_BREVO_BASE}/smtp/email", headers=_headers(), json=payload,
            )
            if resp.status_code in (200, 201):
                log.info("Transactional email sent to %s (%s)", to_email, subject)
                return True
            log.error("Brevo transactional send failed [%d]: %s", resp.status_code, resp.text[:300])
            return False
    except Exception as e:
        log.error("Brevo transactional send error for %s: %s", to_email, e)
        return False


# =============================================================================
# Contact List — Add to signup automation list
# =============================================================================

async def add_to_brevo_list(email: str, display_name: str = "") -> bool:
    """Add a contact to the Brevo signup list (triggers onboarding automation).

    Only called for genuinely new signups, not migrated users or re-logins.
    """
    if not BREVO_API_KEY:
        log.warning("BREVO_API_KEY not set — skipping list add for %s", email)
        return False

    # Brevo: create or update contact, then add to list
    payload = {
        "email": email,
        "listIds": [BREVO_LIST_ID],
        "updateEnabled": True,
    }
    if display_name:
        payload["attributes"] = {"FIRSTNAME": display_name}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_BREVO_BASE}/contacts",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code in (200, 201, 204):
                log.info("Added %s to Brevo list %d", email, BREVO_LIST_ID)
                return True
            else:
                log.error("Brevo list add failed [%d]: %s", resp.status_code, resp.text[:300])
                return False
    except Exception as e:
        log.error("Brevo list add error for %s: %s", email, e)
        return False


# =============================================================================
# Migration — "We've moved" email for existing users
# =============================================================================

async def send_migration_email(email: str, signin_link: str) -> bool:
    """Send the 'we've moved' email to migrated users with their login link."""
    if not BREVO_API_KEY:
        log.warning("BREVO_API_KEY not set — skipping migration email for %s", email)
        return False

    heading = "Lucid Tuner has a new home"
    body_text = (
        "We've rebuilt Lucid Tuner from the ground up. Your tuning history "
        "is already waiting for you at the new address. Click below to log in "
        "and pick up where you left off."
    )
    html = _build_email_html(heading, body_text, "Go to Lucid Tuner", signin_link)

    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": email}],
        "subject": "Lucid Tuner has moved — your new login link",
        "htmlContent": html,
        "trackClicks": False,
        "trackOpens": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_BREVO_BASE}/smtp/email",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code in (200, 201):
                log.info("Migration email sent to %s", email)
                return True
            else:
                log.error("Brevo migration send failed [%d]: %s", resp.status_code, resp.text[:300])
                return False
    except Exception as e:
        log.error("Brevo migration send error for %s: %s", email, e)
        return False


# =============================================================================
# Email HTML Template
# =============================================================================

def _build_email_html(heading: str, body_text: str, button_text: str, button_url: str) -> str:
    """Build a simple, clean email HTML template — Lucid Principles account branding."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#0e0e14;font-family:system-ui,-apple-system,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0e0e14;padding:40px 20px;">
<tr><td align="center">
<table width="480" cellpadding="0" cellspacing="0" style="background:#16161e;border-radius:12px;padding:40px 36px;">

<tr><td style="padding-bottom:24px;text-align:center;">
  <img src="https://audio.lucidprinciples.com/assets/LP_MARK.png" width="48" height="48"
       alt="Lucid Principles"
       style="display:inline-block;border:0;outline:none;text-decoration:none;">
</td></tr>

<tr><td style="padding-bottom:16px;">
  <h1 style="margin:0;font-size:22px;font-weight:600;color:#e8e8ef;text-align:center;">
    {heading}
  </h1>
</td></tr>

<tr><td style="padding-bottom:28px;">
  <p style="margin:0;font-size:15px;line-height:1.6;color:#9a9aaf;text-align:center;">
    {body_text}
  </p>
</td></tr>

<tr><td align="center" style="padding-bottom:28px;">
  <a href="{button_url}"
     style="display:inline-block;padding:14px 36px;background:#4a9eff;color:#fff;
            text-decoration:none;border-radius:8px;font-size:15px;font-weight:600;">
    {button_text}
  </a>
</td></tr>

<tr><td>
  <p style="margin:0;font-size:12px;color:#555;text-align:center;line-height:1.5;">
    This link is for one-time use. If you didn't request this, you can safely ignore it.
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
