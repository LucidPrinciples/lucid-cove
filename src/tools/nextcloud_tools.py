"""
Nextcloud Tools — file operations and calendar/contacts via Nextcloud APIs.

File ops use WebDAV (standard, stable).
Calendar uses CalDAV (Nextcloud Calendar app).
Contacts uses CardDAV (Nextcloud Contacts app).

Approval tiers:
  AUTO   — list, read, search (non-destructive reads)
  NOTIFY — upload, mkdir, create event (writes, logged to MC)
  APPROVE — delete, overwrite existing files

Environment variables required (in docker/.env):
  NEXTCLOUD_URL      — e.g. http://nextcloud-app (internal docker network)
                       or http://100.65.124.97:8080 (host access)
  NEXTCLOUD_USER     — Nextcloud username for this agent
  NEXTCLOUD_PASSWORD — Nextcloud app password (generate in Nextcloud Security settings)
"""

import os
import unicodedata
from src.env import env
from typing import Optional
from urllib.parse import quote, unquote

import httpx
from langchain_core.tools import tool

from src.tools.approval import auto, notify

# =============================================================================
# Config
# =============================================================================

NEXTCLOUD_URL = env("NEXTCLOUD_URL", "http://nextcloud:80")
NEXTCLOUD_USER = env("NEXTCLOUD_USER")
NEXTCLOUD_PASSWORD = env("NEXTCLOUD_PASSWORD")

_OCS_BASE = f"{NEXTCLOUD_URL}/ocs/v2.php"

# ---------------------------------------------------------------------------
# Per-presence credential resolution (CF-57 / punch #3).
#
# These tools historically built every WebDAV/CalDAV URL from the module-global
# NEXTCLOUD_USER env var. That is correct for a single-user (legacy per-container)
# Cove, but on a centralized MULTI-presence Cove there is no single global NC user
# — each presence has its own nc_username/nc_password in the accounts table, so a
# global (often empty) user makes the agent calendar/file tools 500.
#
# A request-scoped ContextVar carries the REQUESTING presence's creds, set at the
# same chat/flow chokepoints that already inject BYOK model creds (chat.py /
# flow_chat.py via get_nc_creds(request)). When the var is unset — the scheduler,
# single-user installs, any non-request path — every resolver falls back to the
# env globals, so single-user behavior is byte-for-byte unchanged. This mirrors
# the proven request-scoped BYOK ContextVar in src/models/provider.py, so the
# same async-context propagation guarantees apply.
# ---------------------------------------------------------------------------
import contextvars as _ctxvars

_nc_creds_ctx: "_ctxvars.ContextVar" = _ctxvars.ContextVar("nc_creds", default=None)


def set_request_nc_creds(url: str, user: str, password: str):
    """Bind the acting presence's NC creds for this request/task. Returns a token
    to pass to clear_request_nc_creds() in a finally block."""
    return _nc_creds_ctx.set((url or NEXTCLOUD_URL, user, password))


def clear_request_nc_creds(token) -> None:
    """Reset the request-scoped NC creds (best-effort)."""
    try:
        _nc_creds_ctx.reset(token)
    except Exception:
        pass


def _current_creds() -> tuple[str, str, str]:
    """(url, user, password) for the acting presence: the request-scoped ctx when a
    non-empty user is bound, else the module env globals (behavior-preserving)."""
    c = _nc_creds_ctx.get()
    if c and c[1]:
        return c
    return (NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASSWORD)


def _nc_url() -> str:
    return _current_creds()[0]


def _nc_user() -> str:
    return _current_creds()[1]


def _webdav_base() -> str:
    return f"{_nc_url()}/remote.php/dav/files/{_nc_user()}"


def _caldav_base() -> str:
    return f"{_nc_url()}/remote.php/dav/calendars/{_nc_user()}"


def _auth() -> tuple[str, str]:
    _, user, password = _current_creds()
    return (user, password)


def _webdav_url(path: str) -> str:
    """Build WebDAV URL for a file/folder path. Path should start with /."""
    path = path.lstrip("/")
    return f"{_webdav_base()}/{quote(path, safe='/')}"


def _norm_ws(s: str) -> str:
    """Collapse every Unicode space separator (regular, U+00A0, and the U+202F
    narrow no-break space macOS screenshot filenames embed) to a plain space, so
    a name a model retyped with an ordinary space still matches the real file."""
    return "".join(" " if unicodedata.category(c) == "Zs" else c for c in (s or ""))


async def _find_sibling_by_ws(path: str) -> Optional[str]:
    """On a 404, find the real sibling of `path` whose filename matches after
    whitespace normalization. macOS screenshots ("...at 3.40.42<U+202F>PM.png")
    fail otherwise because the model passes back a normal space. Returns the
    corrected path or None. Only called on the miss path, so no extra latency
    on the common case."""
    p = (path or "").rstrip("/")
    parent, _, base = p.rpartition("/")
    if not base:
        return None
    want = _norm_ws(base)
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
            '<d:prop><d:displayname/></d:prop></d:propfind>')
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "PROPFIND", _webdav_url(parent or "/"),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=body)
        if resp.status_code != 207:
            return None
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:"}
        for r in root.findall(".//d:response", ns):
            href = r.findtext("d:href", namespaces=ns) or ""
            name = unquote(href.rstrip("/").split("/")[-1])
            if name and name != base and _norm_ws(name) == want:
                return f"{parent}/{name}" if parent else f"/{name}"
    except Exception:
        return None
    return None


# =============================================================================
# File Tools — AUTO (reads)
# =============================================================================

@auto
@tool
async def nextcloud_list(path: str = "/") -> str:
    """List files and folders in a Nextcloud directory.

    Args:
        path: Directory path (e.g. '/', '/Ideas', '/Business Docs')
    """
    url = _webdav_url(path)
    propfind_body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:displayname/>
    <d:getcontenttype/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "PROPFIND", url,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=propfind_body
            )
        if resp.status_code == 207:
            # Parse XML to extract names and types
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = {"d": "DAV:"}
            items = []
            for response in root.findall(".//d:response", ns):
                href = response.findtext("d:href", namespaces=ns) or ""
                name = unquote(href.rstrip("/").split("/")[-1])
                if not name:
                    continue
                resource_type = response.find(".//d:resourcetype/d:collection", ns)
                kind = "📁" if resource_type is not None else "📄"
                size_el = response.findtext(".//d:getcontentlength", namespaces=ns)
                size = f" ({int(size_el):,} bytes)" if size_el else ""
                items.append(f"{kind} {name}{size}")
            return f"Contents of {path}:\n" + "\n".join(items[1:]) if len(items) > 1 else f"{path} is empty"
        return f"Error listing {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@auto
@tool
async def nextcloud_read(path: str) -> str:
    """Read the contents of a text file from Nextcloud.

    Args:
        path: File path (e.g. '/Ideas/my-note.md')
    """
    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                alt = await _find_sibling_by_ws(path)
                if alt:
                    resp = await client.get(_webdav_url(alt)); path = alt
        if resp.status_code == 200:
            content = resp.text
            if len(content) > 8000:
                content = content[:8000] + "\n\n[... truncated at 8000 chars]"
            return f"FILE: {path}\n\n{content}"
        return f"Error reading {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@auto
@tool
async def nextcloud_search(query: str, path: str = "/") -> str:
    """Search for files in Nextcloud by name.

    Args:
        query: Search term
        path: Directory to search in (default: all files)
    """
    nc_user = _nc_user()
    url = f"{_nc_url()}/remote.php/dav"
    search_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:basicsearch>
    <d:select><d:prop><d:displayname/><d:getcontenttype/></d:prop></d:select>
    <d:from><d:scope><d:href>/files/{nc_user}</d:href><d:depth>infinity</d:depth></d:scope></d:from>
    <d:where>
      <d:like><d:prop><d:displayname/></d:prop><d:literal>%{query}%</d:literal></d:like>
    </d:where>
    <d:limit><d:nresults>20</d:nresults></d:limit>
  </d:basicsearch>
</d:searchrequest>"""
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "SEARCH", url,
                headers={"Content-Type": "application/xml"},
                content=search_body
            )
        if resp.status_code in (200, 207):
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = {"d": "DAV:"}
            results = []
            for response in root.findall(".//d:response", ns):
                href = response.findtext("d:href", namespaces=ns) or ""
                name = href.rstrip("/").split("/")[-1]
                if name:
                    # Strip the WebDAV prefix to get readable path
                    readable = unquote(href.replace(f"/remote.php/dav/files/{nc_user}", ""))
                    results.append(readable)
            if results:
                return f"Search results for '{query}':\n" + "\n".join(results)
            return f"No files found matching '{query}'"
        return f"Search error: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# File Tools — NOTIFY (writes)
# =============================================================================

@notify
@tool
async def nextcloud_upload(path: str, content: str, overwrite: bool = False) -> str:
    """Upload text content as a file to Nextcloud.

    Args:
        path: Destination path including filename (e.g. '/Ideas/note.md')
        content: Text content to write
        overwrite: Whether to overwrite if file exists (default False)
    """
    url = _webdav_url(path)
    try:
        if not overwrite:
            # Check if file exists
            async with httpx.AsyncClient(auth=_auth(), timeout=10) as client:
                head = await client.head(url)
            if head.status_code == 200:
                return f"File already exists at {path}. Use overwrite=True to replace."

        async with httpx.AsyncClient(auth=_auth(), timeout=30) as client:
            resp = await client.put(url, content=content.encode("utf-8"),
                                    headers={"Content-Type": "text/plain; charset=utf-8"})
        if resp.status_code in (200, 201, 204):
            action = "Updated" if resp.status_code == 204 else "Created"
            return f"{action}: {path}"
        return f"Upload failed for {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@notify
@tool
async def nextcloud_mkdir(path: str) -> str:
    """Create a folder in Nextcloud.

    Args:
        path: Folder path to create (e.g. '/Ideas/Projects/NewFolder')
    """
    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=10) as client:
            resp = await client.request("MKCOL", url, auth=_auth())
        if resp.status_code in (200, 201):
            return f"Created folder: {path}"
        if resp.status_code == 405:
            return f"Folder already exists: {path}"
        return f"Error creating folder {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


@notify
@tool
async def nextcloud_download(path: str, local_path: str) -> str:
    """Download a file from Nextcloud to the local filesystem (host).

    Args:
        path: Nextcloud file path (e.g. '/Business Docs/report.pdf')
        local_path: Local destination path (e.g. '/app/data/downloads/report.pdf')
    """
    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=60) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                alt = await _find_sibling_by_ws(path)
                if alt:
                    resp = await client.get(_webdav_url(alt)); path = alt
        if resp.status_code == 200:
            import os
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return f"Downloaded {path} → {local_path} ({len(resp.content):,} bytes)"
        return f"Download failed for {path}: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Calendar Tools — CalDAV
# =============================================================================

@auto
@tool
async def calendar_list_events(calendar: str = "personal", days_ahead: int = 7) -> str:
    """List upcoming calendar events from Nextcloud Calendar.

    Args:
        calendar: Calendar name (default 'personal')
        days_ahead: How many days ahead to look (default 7)
    """
    from datetime import datetime, timezone, timedelta
    import xml.etree.ElementTree as ET

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    time_range_start = now.strftime("%Y%m%dT%H%M%SZ")
    time_range_end = end.strftime("%Y%m%dT%H%M%SZ")

    url = f"{_caldav_base()}/{calendar}/"
    report_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{time_range_start}" end="{time_range_end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.request(
                "REPORT", url,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=report_body
            )
        if resp.status_code == 207:
            # Parse VCALENDAR data from response
            root = ET.fromstring(resp.text)
            ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
            events = []
            for response in root.findall(".//d:response", ns):
                cal_data = response.findtext(".//c:calendar-data", namespaces=ns)
                if cal_data:
                    event = _parse_vevent(cal_data)
                    if event:
                        events.append(event)
            if not events:
                return f"No events in the next {days_ahead} days."
            events.sort(key=lambda e: e.get("dtstart", ""))
            lines = [f"Upcoming events ({days_ahead} days):"]
            for e in events:
                lines.append(f"  • {e.get('dtstart', '?')} — {e.get('summary', '(no title)')}")
                if e.get("description"):
                    lines.append(f"    {e['description'][:80]}")
            return "\n".join(lines)
        if resp.status_code == 404:
            return f"Calendar '{calendar}' not found. Create it in Nextcloud Calendar first."
        return f"Error fetching calendar: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


def _parse_vevent(ical_text: str) -> Optional[dict]:
    """Extract basic event fields from VEVENT ical text."""
    event = {}
    in_vevent = False
    for line in ical_text.splitlines():
        if line.strip() == "BEGIN:VEVENT":
            in_vevent = True
        elif line.strip() == "END:VEVENT":
            break
        elif in_vevent:
            if ":" in line:
                key, _, val = line.partition(":")
                # Extract TZID param if present (e.g., DTSTART;TZID=America/New_York)
                tzid = None
                if ";" in key:
                    parts = key.split(";")
                    key = parts[0]
                    for p in parts[1:]:
                        if p.startswith("TZID="):
                            tzid = p[5:]
                if key == "SUMMARY":
                    event["summary"] = val
                elif key in ("DTSTART", "DTEND"):
                    try:
                        if "T" in val:
                            from datetime import datetime
                            raw = val.rstrip("Z")[:15]
                            dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
                            # If UTC (Z suffix), convert to local for display
                            if val.endswith("Z") and not tzid:
                                from zoneinfo import ZoneInfo
                                from datetime import timezone
                                dt = dt.replace(tzinfo=timezone.utc)
                                try:
                                    from src.config import get_instance
                                    local_tz = get_instance().get("timezone", "America/New_York")
                                except Exception:
                                    local_tz = "America/New_York"
                                dt = dt.astimezone(ZoneInfo(local_tz))
                            if key == "DTSTART":
                                event["dtstart"] = dt.strftime("%a %b %d %H:%M")
                        else:
                            from datetime import datetime
                            dt = datetime.strptime(val[:8], "%Y%m%d")
                            if key == "DTSTART":
                                event["dtstart"] = dt.strftime("%a %b %d")
                    except Exception:
                        if key == "DTSTART":
                            event["dtstart"] = val
                elif key == "DESCRIPTION":
                    event["description"] = val
    return event if event else None


def _resolve_relative_date(date_str: str, tz_name: str = "America/New_York") -> str:
    """Resolve relative date strings to YYYY-MM-DD.

    Supports: 'today', 'tomorrow', 'monday'-'sunday' (next occurrence),
    'next monday'-'next sunday', '+N days', and passthrough for YYYY-MM-DD.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    s = date_str.strip().lower()

    # Already YYYY-MM-DD — pass through
    if len(s) == 10 and s[4] == '-' and s[7] == '-':
        return date_str

    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()

    today = now.date()

    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    # +N days
    if s.startswith("+") and "day" in s:
        import re
        m = re.search(r'\+\s*(\d+)', s)
        if m:
            return (today + timedelta(days=int(m.group(1)))).isoformat()

    # Day names: 'monday', 'next monday', etc.
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    check = s.replace("next ", "")
    if check in day_names:
        target_dow = day_names.index(check)
        current_dow = today.weekday()
        days_ahead = (target_dow - current_dow) % 7
        if days_ahead == 0:
            days_ahead = 7  # next week if today is that day
        return (today + timedelta(days=days_ahead)).isoformat()

    # Fallback — return as-is and let strptime handle it
    return date_str


@notify
@tool
async def calendar_create_event(
    title: str,
    date: str,
    start_time: str = "09:00",
    duration_minutes: int = 60,
    description: str = "",
    calendar: str = "personal",
    alarm_minutes_before: int = 15
) -> str:
    """Create a calendar event in Nextcloud Calendar.

    Args:
        title: Event title
        date: Date in YYYY-MM-DD format, OR relative like 'today', 'tomorrow', 'monday', 'next tuesday', '+3 days'
        start_time: Start time in HH:MM (24hr) format (default 09:00)
        duration_minutes: Event duration in minutes (default 60)
        description: Optional event description
        calendar: Calendar name (default 'personal')
        alarm_minutes_before: Minutes before event to fire a notification (default 15, set to -1 to disable)
    """
    import uuid
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    # Get the agent's configured timezone for proper iCal TZID
    try:
        from src.config import get_instance
        tz_name = get_instance().get("timezone", "America/New_York")
    except Exception:
        tz_name = "America/New_York"

    # ── Resolve relative dates so the model doesn't have to calculate ──
    date = _resolve_relative_date(date, tz_name)

    try:
        start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        uid = str(uuid.uuid4())
        dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
        dtend = end_dt.strftime("%Y%m%dT%H%M%S")
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Compute UTC equivalents for verification
        tz = ZoneInfo(tz_name)
        start_aware = start_dt.replace(tzinfo=tz)
        start_utc = start_aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_aware = end_dt.replace(tzinfo=tz)
        end_utc = end_aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Build VALARM block for notifications
        alarm_block = ""
        if alarm_minutes_before >= 0:
            alarm_block = f"""
BEGIN:VALARM
TRIGGER:-PT{alarm_minutes_before}M
ACTION:DISPLAY
DESCRIPTION:Reminder
END:VALARM"""

        ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now}
DTSTART;TZID={tz_name}:{dtstart}
DTEND;TZID={tz_name}:{dtend}
SUMMARY:{title}
DESCRIPTION:{description}{alarm_block}
END:VEVENT
END:VCALENDAR"""

        url = f"{_caldav_base()}/{calendar}/{uid}.ics"
        async with httpx.AsyncClient(auth=_auth(), timeout=15) as client:
            resp = await client.put(
                url,
                content=ical.encode("utf-8"),
                headers={"Content-Type": "text/calendar; charset=utf-8"}
            )
        if resp.status_code in (200, 201, 204):
            return f"Created event: '{title}' on {date} at {start_time} ({duration_minutes}min) in '{calendar}'"
        if resp.status_code == 404:
            return f"Calendar '{calendar}' not found. Create it in Nextcloud Calendar first."
        return f"Error creating event: HTTP {resp.status_code} — {resp.text[:200]}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_NEXTCLOUD_TOOLS = [
    nextcloud_list,
    nextcloud_read,
    nextcloud_search,
    nextcloud_upload,
    nextcloud_mkdir,
    nextcloud_download,
    calendar_list_events,
    calendar_create_event,
]
TOOLS = ALL_NEXTCLOUD_TOOLS  # alias for cove-core channels.py loader
