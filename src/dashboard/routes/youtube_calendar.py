"""
YouTube queue ↔ Nextcloud calendar integration (CalDAV).

Creates and deletes calendar events when YouTube queue posts are
scheduled or cancelled. Calendar is informational — failures are
non-fatal and never block the upload pipeline — but they are REPORTED
(create/delete return bool) so callers can surface "scheduled, but no
calendar event" instead of a false green.

Credentials resolve at CALL time, per event owner:
  1. the owning presence's own NC account (multi mode — the event lands on
     THE CALENDAR THE OPERATOR ACTUALLY LOOKS AT),
  2. env NEXTCLOUD_USER/PASSWORD (legacy per-agent containers),
  3. the NC admin account (centralized founder fallback).
The old module-level env read meant the centralized stack (which sets only
NEXTCLOUD_ADMIN_*) silently skipped every event, forever.
"""

from src.env import env
from datetime import datetime

import httpx


def _yt_calendar_uid(queue_id: int) -> str:
    """Deterministic UID for a YouTube queue calendar event."""
    return f"yt-queue-{queue_id}@cove"


async def _nc_calendar_creds(presence_id: str | None = None) -> tuple[str, str, str] | None:
    """(nc_url, user, password) for the calendar write — see module docstring."""
    nc_url = (env("NEXTCLOUD_URL") or "").rstrip("/")
    if not nc_url:
        return None

    if presence_id:
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                r = await conn.execute(
                    "SELECT nc_username, nc_password FROM accounts WHERE id = %s",
                    (presence_id,))
                row = await r.fetchone()
            if row and row["nc_username"] and row["nc_password"]:
                return nc_url, row["nc_username"], row["nc_password"]
        except Exception:
            pass

    u, p = env("NEXTCLOUD_USER"), env("NEXTCLOUD_PASSWORD")
    if u and p:
        return nc_url, u, p

    try:
        from src.config import get_nc_admin_user, get_nc_admin_password
        au, ap = get_nc_admin_user(), get_nc_admin_password()
        if au and ap:
            return nc_url, au, ap
    except Exception:
        pass
    return None


async def create_youtube_calendar_event(
    queue_id: int, title: str, upload_date, publish_date, series: str = "",
    presence_id: str | None = None,
) -> bool:
    """Create a CalDAV event for a scheduled YouTube upload.

    Called when a post transitions draft → queued. Event appears on the
    owner's calendar at upload_date so the operator sees what's coming.
    Never raises; returns True on success so callers can surface failure.
    """
    creds = await _nc_calendar_creds(presence_id)
    if not creds:
        print(f"[youtube] Calendar event skipped for queue #{queue_id}: "
              "no Nextcloud credentials resolvable (presence/env/admin)")
        return False
    nc_url, nc_user, nc_pass = creds

    try:
        from datetime import timedelta, timezone

        # Get Cove timezone
        from src.utils.time_utils import app_tz
        tz = app_tz()
        tz_name = tz.key
        uid = _yt_calendar_uid(queue_id)

        # Convert dates to Presence local time for the calendar event
        from src.utils.time_utils import utc_to_local
        ud = utc_to_local(upload_date)

        # Format publish_date for description
        pub_str = ""
        if publish_date:
            pd = utc_to_local(publish_date)
            pub_str = pd.strftime("%b %d, %I:%M %p %Z")

        dtstart = ud.strftime("%Y%m%dT%H%M%S")
        dtend = (ud + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%S")
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        series_str = f"Series: {series}\\n" if series else ""
        description = (
            f"YouTube Short scheduled for upload.\\n"
            f"{series_str}"
            f"Publish date: {pub_str}\\n"
            f"Queue ID: {queue_id}"
        )

        ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Cove MC//YouTube Queue//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART;TZID={tz_name}:{dtstart}
DTEND;TZID={tz_name}:{dtend}
SUMMARY:📺 Upload: {title}
DESCRIPTION:{description}
BEGIN:VALARM
TRIGGER:-PT30M
ACTION:DISPLAY
DESCRIPTION:YouTube upload scheduled
END:VALARM
END:VEVENT
END:VCALENDAR"""

        url = f"{nc_url}/remote.php/dav/calendars/{nc_user}/personal/{uid}.ics"
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=10) as client:
            resp = await client.put(
                url,
                content=ical.encode("utf-8"),
                headers={"Content-Type": "text/calendar; charset=utf-8"},
            )
        if resp.status_code in (200, 201, 204):
            print(f"[youtube] Calendar event created for queue #{queue_id} (user={nc_user})")
            return True
        print(f"[youtube] Calendar event failed: HTTP {resp.status_code} (user={nc_user})")
        return False

    except Exception as e:
        print(f"[youtube] Calendar event error (non-fatal): {e}")
        return False


async def delete_youtube_calendar_event(queue_id: int,
                                        presence_id: str | None = None) -> bool:
    """Remove the CalDAV event for a YouTube queue item.

    Called when a post is cancelled or uploaded (no longer pending).
    Never raises; returns True when the event is gone (deleted or 404)."""
    creds = await _nc_calendar_creds(presence_id)
    if not creds:
        return False
    nc_url, nc_user, nc_pass = creds

    try:
        uid = _yt_calendar_uid(queue_id)
        url = f"{nc_url}/remote.php/dav/calendars/{nc_user}/personal/{uid}.ics"
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=10) as client:
            resp = await client.delete(url)
        if resp.status_code in (200, 204, 404):
            print(f"[youtube] Calendar event removed for queue #{queue_id}")
            return True
        print(f"[youtube] Calendar event delete failed: HTTP {resp.status_code}")
        return False

    except Exception as e:
        print(f"[youtube] Calendar event delete error (non-fatal): {e}")
        return False
