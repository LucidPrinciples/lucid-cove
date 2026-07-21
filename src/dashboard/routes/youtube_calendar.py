"""
YouTube queue ↔ Nextcloud calendar integration (CalDAV).

#VP-CAL: scheduled posts land on a per-presence **video-pipeline** calendar
(not personal). The calendar is a monitoring projection of the queue — one
event per post that is UPDATED through the short's life and only removed when
the operator marks it published/cancelled on the Action Board (or the row is
cancelled before upload). Failures are non-fatal and never block the upload
pipeline, but create/update/delete return bool so callers can surface
"scheduled, but no calendar event" instead of a false green.

Credentials resolve at CALL time, per event owner:
  1. the owning presence's own NC account (multi mode — the event lands on
     THE CALENDAR THE OPERATOR ACTUALLY LOOKS AT),
  2. env NEXTCLOUD_USER/PASSWORD (legacy per-agent containers),
  3. the NC admin account (centralized founder fallback).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from src.env import env

# CalDAV collection name under each presence's NC calendars/
VIDEO_PIPELINE_CALENDAR = "video-pipeline"
VIDEO_PIPELINE_DISPLAY_NAME = "Video Pipeline"


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


def _cal_collection_url(nc_url: str, nc_user: str, calendar: str = VIDEO_PIPELINE_CALENDAR) -> str:
    return f"{nc_url}/remote.php/dav/calendars/{nc_user}/{calendar}"


def _event_url(nc_url: str, nc_user: str, uid: str,
               calendar: str = VIDEO_PIPELINE_CALENDAR) -> str:
    return f"{_cal_collection_url(nc_url, nc_user, calendar)}/{uid}.ics"


async def _ensure_video_pipeline_calendar(
    client: httpx.AsyncClient, nc_url: str, nc_user: str,
) -> bool:
    """MKCOL the video-pipeline calendar if missing. Idempotent.

    Nextcloud accepts a CalDAV MKCOL with the calendar resource type.
    201 = created, 405/409 = already exists — both fine.
    """
    url = _cal_collection_url(nc_url, nc_user)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:mkcol xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
        'xmlns:cs="http://calendarserver.org/ns/" '
        'xmlns:a="http://apple.com/ns/ical/">'
        "<d:set><d:prop>"
        "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
        f"<d:displayname>{VIDEO_PIPELINE_DISPLAY_NAME}</d:displayname>"
        "<a:calendar-color>#5ce1e6</a:calendar-color>"
        "</d:prop></d:set></d:mkcol>"
    )
    try:
        # PROPFIND first — cheap existence check
        head = await client.request(
            "PROPFIND", url,
            content=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                    b"<d:displayname/></d:prop></d:propfind>",
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
        )
        if head.status_code in (200, 207):
            return True
        resp = await client.request(
            "MKCOL", url,
            content=body.encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        if resp.status_code in (201, 200, 204, 405, 409):
            print(f"[youtube] video-pipeline calendar ready for {nc_user} "
                  f"(mkcol={resp.status_code})")
            return True
        print(f"[youtube] video-pipeline MKCOL failed: HTTP {resp.status_code} "
              f"for {nc_user}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[youtube] video-pipeline ensure error: {e}")
        return False


def _phase_for_status(status: str | None) -> str:
    s = (status or "queued").lower()
    if s in ("uploaded", "uploading"):
        return "uploaded"
    if s == "published":
        return "published"
    if s == "failed":
        return "failed"
    return "queued"


def _summary_for(title: str, status: str | None, is_short: bool | None = None) -> str:
    phase = _phase_for_status(status)
    kind = "Short" if is_short else "Video"
    if phase == "queued":
        return f"📺 Upload · YT · {title}"
    if phase == "uploaded":
        return f"🔒 Private · YT · {title}"
    if phase == "published":
        return f"✅ Live · YT · {title}"
    if phase == "failed":
        return f"⚠️ Failed · YT · {title}"
    return f"📺 {kind} · YT · {title}"


def _build_ical(
    *,
    uid: str,
    title: str,
    upload_date,
    publish_date=None,
    series: str = "",
    status: str | None = "queued",
    youtube_url: str | None = None,
    youtube_video_id: str | None = None,
    is_short: bool | None = None,
) -> str | None:
    """Build VEVENT ICS. Anchor time = upload while queued; publish once known after upload.

    One evolving event — same UID — rewritten on every lifecycle step.
    """
    from src.utils.time_utils import app_tz, utc_to_local

    if not upload_date and not publish_date:
        return None

    tz = app_tz()
    tz_name = tz.key
    phase = _phase_for_status(status)

    # Time anchor: before upload → upload_date; after upload → publish_date (public flip)
    anchor = upload_date
    if phase in ("uploaded", "published") and publish_date:
        anchor = publish_date
    if anchor is None:
        anchor = publish_date or upload_date
    local_anchor = utc_to_local(anchor)

    dtstart = local_anchor.strftime("%Y%m%dT%H%M%S")
    dtend = (local_anchor + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%S")
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    pub_str = ""
    if publish_date:
        pd = utc_to_local(publish_date)
        pub_str = pd.strftime("%b %d, %I:%M %p %Z")
    up_str = ""
    if upload_date:
        ud = utc_to_local(upload_date)
        up_str = ud.strftime("%b %d, %I:%M %p %Z")

    series_str = f"Series: {series}\\n" if series else ""
    kind = "Short" if is_short else "Video"
    studio = ""
    if youtube_video_id:
        studio = f"Studio: https://studio.youtube.com/video/{youtube_video_id}/edit\\n"
    watch = f"Watch: {youtube_url}\\n" if youtube_url else ""

    phase_line = {
        "queued": "Status: scheduled — waiting for private upload",
        "uploaded": "Status: uploaded private — waiting to go public",
        "published": "Status: marked published / live",
        "failed": "Status: upload failed — check Action Board",
    }.get(phase, f"Status: {phase}")

    description = (
        f"YouTube {kind} (Video Pipeline)\\n"
        f"{phase_line}\\n"
        f"{series_str}"
        f"Upload: {up_str}\\n"
        f"Public: {pub_str}\\n"
        f"{studio}{watch}"
        f"Queue ID: {uid.split('@')[0].replace('yt-queue-', '')}"
    )

    summary = _summary_for(title, status, is_short=is_short)
    # Escape commas/semicolons lightly for SUMMARY
    summary_esc = summary.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
    desc_esc = description.replace("\n", "\\n")

    alarm_desc = "YouTube upload scheduled" if phase == "queued" else "YouTube publish window"
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Cove MC//Video Pipeline//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART;TZID={tz_name}:{dtstart}
DTEND;TZID={tz_name}:{dtend}
SUMMARY:{summary_esc}
DESCRIPTION:{desc_esc}
BEGIN:VALARM
TRIGGER:-PT30M
ACTION:DISPLAY
DESCRIPTION:{alarm_desc}
END:VALARM
END:VEVENT
END:VCALENDAR"""


async def _put_event(
    nc_url: str, nc_user: str, nc_pass: str, uid: str, ical: str,
) -> bool:
    async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=15) as client:
        ok = await _ensure_video_pipeline_calendar(client, nc_url, nc_user)
        if not ok:
            return False
        url = _event_url(nc_url, nc_user, uid)
        resp = await client.put(
            url,
            content=ical.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        if resp.status_code in (200, 201, 204):
            return True
        print(f"[youtube] Calendar PUT failed: HTTP {resp.status_code} "
              f"(user={nc_user}): {resp.text[:200]}")
        return False


async def create_youtube_calendar_event(
    queue_id: int, title: str, upload_date, publish_date, series: str = "",
    presence_id: str | None = None,
    status: str | None = "queued",
    youtube_url: str | None = None,
    youtube_video_id: str | None = None,
    is_short: bool | None = None,
) -> bool:
    """Create or update the single lifecycle event for a scheduled YouTube post.

    Same UID every time (PUT overwrite). Called on draft→queued, date edits,
    and post-upload (status uploaded + Studio/watch links). Never raises.
    """
    creds = await _nc_calendar_creds(presence_id)
    if not creds:
        print(f"[youtube] Calendar event skipped for queue #{queue_id}: "
              "no Nextcloud credentials resolvable (presence/env/admin)")
        return False
    nc_url, nc_user, nc_pass = creds

    try:
        uid = _yt_calendar_uid(queue_id)
        ical = _build_ical(
            uid=uid,
            title=title,
            upload_date=upload_date,
            publish_date=publish_date,
            series=series or "",
            status=status,
            youtube_url=youtube_url,
            youtube_video_id=youtube_video_id,
            is_short=is_short,
        )
        if not ical:
            print(f"[youtube] Calendar event skipped for queue #{queue_id}: no dates")
            return False
        ok = await _put_event(nc_url, nc_user, nc_pass, uid, ical)
        if ok:
            print(f"[youtube] Calendar event upserted for queue #{queue_id} "
                  f"(cal={VIDEO_PIPELINE_CALENDAR}, user={nc_user}, status={status})")
        return ok
    except Exception as e:
        print(f"[youtube] Calendar event error (non-fatal): {e}")
        return False


async def update_youtube_calendar_event(
    queue_id: int, title: str, upload_date, publish_date, series: str = "",
    presence_id: str | None = None,
    status: str | None = "queued",
    youtube_url: str | None = None,
    youtube_video_id: str | None = None,
    is_short: bool | None = None,
) -> bool:
    """Alias — create is already an idempotent PUT. Kept for call-site clarity."""
    return await create_youtube_calendar_event(
        queue_id, title, upload_date, publish_date, series=series,
        presence_id=presence_id, status=status,
        youtube_url=youtube_url, youtube_video_id=youtube_video_id,
        is_short=is_short,
    )


async def delete_youtube_calendar_event(queue_id: int,
                                        presence_id: str | None = None) -> bool:
    """Remove the CalDAV event for a YouTube queue item.

    Called when a post is cancelled OR when the operator marks it published
    on the Action Board (calendar stays until then). Never raises.
    """
    creds = await _nc_calendar_creds(presence_id)
    if not creds:
        return False
    nc_url, nc_user, nc_pass = creds

    try:
        uid = _yt_calendar_uid(queue_id)
        # Prefer video-pipeline; also try personal for legacy events written
        # before #VP-CAL so old rows don't leave ghosts on personal.
        urls = [
            _event_url(nc_url, nc_user, uid, VIDEO_PIPELINE_CALENDAR),
            _event_url(nc_url, nc_user, uid, "personal"),
        ]
        any_gone = False
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=10) as client:
            for url in urls:
                resp = await client.delete(url)
                if resp.status_code in (200, 204, 404):
                    any_gone = True
                else:
                    print(f"[youtube] Calendar delete HTTP {resp.status_code} for {url}")
        if any_gone:
            print(f"[youtube] Calendar event removed for queue #{queue_id}")
        return any_gone
    except Exception as e:
        print(f"[youtube] Calendar event delete error (non-fatal): {e}")
        return False
