"""
YouTube queue ↔ Nextcloud calendar integration (CalDAV).

Creates and deletes calendar events when YouTube queue posts are
scheduled or cancelled. Calendar is informational — failures are
non-fatal and never block the upload pipeline.
"""

import os
from src.env import env
from datetime import datetime

import httpx

_NC_URL = env("NEXTCLOUD_URL")
_NC_USER = env("NEXTCLOUD_USER")
_NC_PASS = env("NEXTCLOUD_PASSWORD")


def _yt_calendar_uid(queue_id: int) -> str:
    """Deterministic UID for a YouTube queue calendar event."""
    return f"yt-queue-{queue_id}@cove"


async def create_youtube_calendar_event(
    queue_id: int, title: str, upload_date, publish_date, series: str = ""
):
    """Create a CalDAV event for a scheduled YouTube upload.

    Called when a post transitions draft → queued. Event appears on the
    calendar at upload_date so the operator sees what's coming.
    Fails silently — calendar is informational, not critical path.
    """
    if not all([_NC_URL, _NC_USER, _NC_PASS]):
        return

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

        url = f"{_NC_URL}/remote.php/dav/calendars/{_NC_USER}/personal/{uid}.ics"
        async with httpx.AsyncClient(auth=(_NC_USER, _NC_PASS), timeout=10) as client:
            resp = await client.put(
                url,
                content=ical.encode("utf-8"),
                headers={"Content-Type": "text/calendar; charset=utf-8"},
            )
        if resp.status_code in (200, 201, 204):
            print(f"[youtube] Calendar event created for queue #{queue_id}")
        else:
            print(f"[youtube] Calendar event failed: HTTP {resp.status_code}")

    except Exception as e:
        print(f"[youtube] Calendar event error (non-fatal): {e}")


async def delete_youtube_calendar_event(queue_id: int):
    """Remove the CalDAV event for a YouTube queue item.

    Called when a post is cancelled or uploaded (no longer pending).
    Fails silently.
    """
    if not all([_NC_URL, _NC_USER, _NC_PASS]):
        return

    try:
        uid = _yt_calendar_uid(queue_id)
        url = f"{_NC_URL}/remote.php/dav/calendars/{_NC_USER}/personal/{uid}.ics"
        async with httpx.AsyncClient(auth=(_NC_USER, _NC_PASS), timeout=10) as client:
            resp = await client.delete(url)
        if resp.status_code in (200, 204, 404):
            print(f"[youtube] Calendar event removed for queue #{queue_id}")
        else:
            print(f"[youtube] Calendar event delete failed: HTTP {resp.status_code}")

    except Exception as e:
        print(f"[youtube] Calendar event delete error (non-fatal): {e}")
