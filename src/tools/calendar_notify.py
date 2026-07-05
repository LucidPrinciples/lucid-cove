"""
Calendar Notification Bridge — pushes approvals + notifications to operator calendars.

Architecture:
  The admin agent uses its Nextcloud account for system ops.
  Each operator has their own Nextcloud account + personal agent.
  When an agent needs to notify an operator, it reads THEIR credentials from
  family.yaml and writes to THEIR calendar. Scales to N operators.

When an APPROVE-tier request is queued, this creates a calendar event with
a VALARM (instant alarm) in the operator's calendar. Their phone picks it up
via DAVx5 CalDAV sync and fires a native notification.

Never raises — failures are logged only (approval system must not block on this).
"""

import logging
import os
from src.env import env
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
import yaml

logger = logging.getLogger("calendar_notify")

_FAMILY_CONFIG = Path(__file__).parent.parent.parent / "config" / "family.yaml"
NEXTCLOUD_URL = env("NEXTCLOUD_URL", "http://nextcloud:80")

# Default calendar name for notifications
NOTIFY_CALENDAR = env("NOTIFY_CALENDAR", "personal")


# =============================================================================
# Family credential lookup
# =============================================================================

def _load_family() -> dict:
    """Load family.yaml — cached per-process."""
    try:
        with open(_FAMILY_CONFIG) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_operator_nc_creds(operator_id: str = None) -> Optional[dict]:
    """Get Nextcloud credentials for a specific operator from family.yaml.

    If operator_id is None, returns the first active operator's creds.
    Returns dict with keys: username, password, caldav_url — or None if not found.
    """
    data = _load_family()
    members = data.get("members", [])

    for member in members:
        if operator_id and member.get("id") != operator_id:
            continue
        nc = member.get("nextcloud", {})
        username = nc.get("username")
        password = nc.get("app_password")
        if username and password:
            return {
                "username": username,
                "password": password,
                "caldav_url": nc.get("caldav_url", f"{NEXTCLOUD_URL}/remote.php/dav/calendars/{username}"),
            }
        # If no nextcloud block but member is active, skip
        if not operator_id:
            continue

    return None


def _get_all_active_operators() -> list[dict]:
    """Get NC creds for all active operators (for broadcast notifications)."""
    data = _load_family()
    members = data.get("members", [])
    results = []
    for member in members:
        if member.get("status") != "active":
            continue
        nc = member.get("nextcloud", {})
        username = nc.get("username")
        password = nc.get("app_password")
        if username and password:
            results.append({
                "id": member["id"],
                "username": username,
                "password": password,
                "caldav_url": nc.get("caldav_url", f"{NEXTCLOUD_URL}/remote.php/dav/calendars/{username}"),
            })
    return results


# =============================================================================
# iCal builders
# =============================================================================

def _build_approval_ical(
    *,
    uid: str,
    summary: str,
    description: str,
    timestamp: datetime,
) -> str:
    """Build iCal event with VALARM for an approval request."""
    dtstart = timestamp.strftime("%Y%m%dT%H%M%SZ")
    dtend = (timestamp + timedelta(minutes=5)).strftime("%Y%m%dT%H%M%SZ")

    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//ApprovalBridge//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstart}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{summary}
DESCRIPTION:{description}
CATEGORIES:APPROVAL
STATUS:TENTATIVE
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:{summary}
TRIGGER:PT0S
END:VALARM
END:VEVENT
END:VCALENDAR"""


# =============================================================================
# Public API — called by approval.py
# =============================================================================

async def push_approval_to_calendar(
    *,
    request_id: str,
    tool_name: str,
    description: str,
    operator_id: str = None,
) -> None:
    """Create a calendar event for a pending approval in the operator's calendar.

    If operator_id is None, notifies the first active operator.
    Never raises.
    """
    creds = _get_operator_nc_creds(operator_id)
    if not creds:
        logger.debug("[calendar_notify] No operator credentials found — skipping")
        return

    try:
        uid = f"approval-{request_id}-{uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        summary = f"APPROVE: {tool_name}"
        desc_short = description[:200] if description else tool_name

        ical = _build_approval_ical(
            uid=uid,
            summary=summary,
            description=desc_short,
            timestamp=now,
        )

        url = f"{creds['caldav_url']}{NOTIFY_CALENDAR}/{uid}.ics"
        # Ensure URL doesn't double-slash
        url = url.replace("//personal", "/personal")

        async with httpx.AsyncClient(
            auth=(creds["username"], creds["password"]),
            timeout=8,
        ) as client:
            resp = await client.put(
                url,
                content=ical,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
            )
            if resp.status_code in (201, 204):
                logger.info(f"[calendar_notify] Pushed approval '{tool_name}' to {creds['username']}'s calendar")
            elif resp.status_code == 404:
                logger.warning(f"[calendar_notify] Calendar '{NOTIFY_CALENDAR}' not found for {creds['username']}")
            else:
                logger.warning(f"[calendar_notify] Calendar push returned HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[calendar_notify] Failed to push approval: {e}")


async def push_notification_to_calendar(
    *,
    tool_name: str,
    description: str,
    operator_id: str = None,
    category: str = "NOTIFY",
) -> None:
    """Create a calendar event for a NOTIFY-tier action. Never raises."""
    creds = _get_operator_nc_creds(operator_id)
    if not creds:
        return

    try:
        uid = f"notify-{uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        summary = f"{tool_name}"
        desc_short = description[:200] if description else tool_name

        dtstart = now.strftime("%Y%m%dT%H%M%SZ")
        dtend = (now + timedelta(minutes=2)).strftime("%Y%m%dT%H%M%SZ")

        ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//NotifyBridge//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstart}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{summary}
DESCRIPTION:{desc_short}
CATEGORIES:{category}
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""

        url = f"{creds['caldav_url']}{NOTIFY_CALENDAR}/{uid}.ics"
        url = url.replace("//personal", "/personal")

        async with httpx.AsyncClient(
            auth=(creds["username"], creds["password"]),
            timeout=8,
        ) as client:
            await client.put(
                url,
                content=ical,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
            )
    except Exception as e:
        logger.debug(f"[calendar_notify] Notify push failed (non-fatal): {e}")


async def broadcast_to_all_operators(
    *,
    summary: str,
    description: str,
    category: str = "SYSTEM",
) -> None:
    """Push a notification to ALL active operators' calendars.

    Use for system-wide events (LTP failures, service outages, etc.).
    Never raises.
    """
    operators = _get_all_active_operators()
    for op in operators:
        try:
            uid = f"broadcast-{uuid4().hex[:8]}"
            now = datetime.now(timezone.utc)
            dtstart = now.strftime("%Y%m%dT%H%M%SZ")
            dtend = (now + timedelta(minutes=5)).strftime("%Y%m%dT%H%M%SZ")

            ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//Broadcast//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstart}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{summary}
DESCRIPTION:{description[:200]}
CATEGORIES:{category}
STATUS:TENTATIVE
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:{summary}
TRIGGER:PT0S
END:VALARM
END:VEVENT
END:VCALENDAR"""

            url = f"{op['caldav_url']}{NOTIFY_CALENDAR}/{uid}.ics"
            url = url.replace("//personal", "/personal")

            async with httpx.AsyncClient(
                auth=(op["username"], op["password"]),
                timeout=8,
            ) as client:
                await client.put(
                    url,
                    content=ical,
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                )
                logger.info(f"[calendar_notify] Broadcast to {op['id']}: {summary}")
        except Exception as e:
            logger.debug(f"[calendar_notify] Broadcast to {op['id']} failed: {e}")
