"""
Soren Verification Layer — continuous tool result verification.

Layer 1 of the accountability architecture. After every tool execution that
touches real state (file writes, memory saves, calendar events, list items),
a registered verifier checks whether the claimed action actually happened.

Design principles:
  - Blocking: verification runs before the agent sees the result. The agent
    never claims success for a failed action.
  - Per-tool: each verifiable tool registers a verifier function. Tools without
    a verifier pass through silently (reads, searches, etc.).
  - Logged: every verification attempt is recorded in verification_log.
    Patterns over time are the signal — individual failures are noise.
  - Escalation: failures increment a counter. Repeated failures for the same
    tool type within a window get flagged for operator attention.

Verifiers return (passed: bool, detail: str). The detail is human-readable
context about what was checked and what happened.

Adding a verifier for a new tool:
    @register_verifier("my_new_tool")
    async def verify_my_tool(args: dict, result: str) -> tuple[bool, str]:
        # Check whether the action claimed in `result` actually happened
        return (True, "Verified: thing exists")
"""

import logging
import os
from src.env import env
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# =============================================================================
# Verifier Registry
# =============================================================================

_VERIFIERS: dict[str, callable] = {}


def register_verifier(tool_name: str):
    """Decorator to register a verification function for a tool."""
    def wrapper(func):
        _VERIFIERS[tool_name] = func
        return func
    return wrapper


def has_verifier(tool_name: str) -> bool:
    """Check if a tool has a registered verifier."""
    return tool_name in _VERIFIERS


# =============================================================================
# Main Entry Point — called from tool_node
# =============================================================================

async def verify_and_log(
    tool_name: str,
    tool_args: dict,
    result: str,
    agent_id: str,
    channel: str = "",
) -> dict:
    """Verify a tool result and log the outcome.

    Returns dict with:
        verified: bool — whether verification was attempted
        passed: bool — whether verification passed (True if no verifier)
        detail: str — human-readable verification detail
        modified_result: str | None — if verification failed, a modified result
            string that includes the failure note. None if passed.
    """
    verifier = _VERIFIERS.get(tool_name)
    if not verifier:
        return {"verified": False, "passed": True, "detail": "", "modified_result": None}

    try:
        passed, detail = await verifier(tool_args, result)
    except Exception as e:
        passed = False
        detail = f"Verifier error: {e}"
        logger.error("Soren verification error for %s: %s", tool_name, e)

    # Log to database (fire-and-forget style but still awaited for reliability)
    try:
        await _log_verification(
            tool_name=tool_name,
            tool_args=tool_args,
            result_preview=result[:500] if result else "",
            passed=passed,
            detail=detail,
            agent_id=agent_id,
            channel=channel,
        )
    except Exception as e:
        logger.error("Soren log write failed: %s", e)

    # If failed, modify the result so the agent knows
    modified_result = None
    if not passed:
        modified_result = (
            f"{result}\n\n"
            f"⚠ VERIFICATION FAILED: {detail}\n"
            f"The action may not have completed as expected. "
            f"Check the state and retry if needed."
        )
        logger.warning(
            "Soren verification FAILED — tool=%s agent=%s detail=%s",
            tool_name, agent_id, detail,
        )

    return {
        "verified": True,
        "passed": passed,
        "detail": detail,
        "modified_result": modified_result,
    }


# =============================================================================
# Database Logging
# =============================================================================

async def _log_verification(
    tool_name: str,
    tool_args: dict,
    result_preview: str,
    passed: bool,
    detail: str,
    agent_id: str,
    channel: str = "",
) -> None:
    """Write a verification record to the verification_log table."""
    from src.memory.database import get_db
    import json

    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO verification_log
               (agent_id, channel, tool_name, tool_args, result_preview,
                passed, detail, verified_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                agent_id,
                channel,
                tool_name,
                json.dumps(tool_args, default=str),
                result_preview,
                passed,
                detail,
                datetime.now(timezone.utc),
            ),
        )
        await conn.commit()


# =============================================================================
# Verification Summary — for dashboards / Vera / operator review
# =============================================================================

async def get_verification_summary(
    agent_id: Optional[str] = None,
    days: int = 7,
) -> dict:
    """Get verification stats for dashboard display.

    Returns:
        total: int — total verifications in window
        passed: int — verifications that passed
        failed: int — verifications that failed
        failure_rate: float — 0.0 to 1.0
        recent_failures: list[dict] — last 10 failures with detail
    """
    from src.memory.database import get_db

    agent_filter = "AND agent_id = %s" if agent_id else ""
    params = [days]
    if agent_id:
        params.append(agent_id)

    async with get_db() as conn:
        # Totals
        row = await conn.execute(
            f"""SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE passed = TRUE) AS passed,
                    COUNT(*) FILTER (WHERE passed = FALSE) AS failed
                FROM verification_log
                WHERE verified_at > NOW() - INTERVAL '%s days'
                {agent_filter}""",
            params,
        )
        totals = await row.fetchone()

        # Recent failures
        fail_params = [days]
        if agent_id:
            fail_params.append(agent_id)
        rows = await conn.execute(
            f"""SELECT agent_id, tool_name, detail, verified_at
                FROM verification_log
                WHERE passed = FALSE
                  AND verified_at > NOW() - INTERVAL '%s days'
                  {agent_filter}
                ORDER BY verified_at DESC
                LIMIT 10""",
            fail_params,
        )
        failures = await rows.fetchall()

    total = totals["total"] if totals else 0
    passed_count = totals["passed"] if totals else 0
    failed_count = totals["failed"] if totals else 0

    return {
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "failure_rate": failed_count / total if total > 0 else 0.0,
        "recent_failures": [dict(f) for f in failures],
    }


# =============================================================================
# Nextcloud Helpers (shared by file verifiers)
# =============================================================================

def _nc_auth() -> tuple[str, str]:
    user = env("NEXTCLOUD_USER")
    pw = env("NEXTCLOUD_PASSWORD")
    return (user, pw)


def _nc_webdav_url(path: str) -> str:
    base_url = env("NEXTCLOUD_URL", "http://nextcloud:80")
    user = env("NEXTCLOUD_USER")
    from urllib.parse import quote
    path = path.lstrip("/")
    return f"{base_url}/remote.php/dav/files/{user}/{quote(path, safe='/')}"


# =============================================================================
# Verifiers — Nextcloud File Operations
# =============================================================================

@register_verifier("nextcloud_upload")
async def verify_upload(args: dict, result: str) -> tuple[bool, str]:
    """Verify file was actually created/updated at the claimed path."""
    path = args.get("path", "")
    if not path:
        return (False, "No path in tool args")

    # Check if result indicates success
    if not any(kw in result.lower() for kw in ("created", "updated")):
        # Tool itself reported an error — skip verification
        return (True, "Tool reported failure — no verification needed")

    url = _nc_webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_nc_auth(), timeout=10) as client:
            resp = await client.request("HEAD", url)
        if resp.status_code == 200:
            return (True, f"Verified: file exists at {path}")
        return (False, f"File NOT found at {path} — HEAD returned {resp.status_code}")
    except Exception as e:
        return (False, f"Verification check failed: {e}")


@register_verifier("nextcloud_mkdir")
async def verify_mkdir(args: dict, result: str) -> tuple[bool, str]:
    """Verify folder was actually created."""
    path = args.get("path", "")
    if not path:
        return (False, "No path in tool args")

    if "already exists" in result.lower():
        return (True, "Folder already existed — no action needed")
    if "error" in result.lower() and "created" not in result.lower():
        return (True, "Tool reported failure — no verification needed")

    url = _nc_webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_nc_auth(), timeout=10) as client:
            resp = await client.request(
                "PROPFIND", url,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                content='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>',
            )
        if resp.status_code == 207:
            return (True, f"Verified: folder exists at {path}")
        return (False, f"Folder NOT found at {path} — PROPFIND returned {resp.status_code}")
    except Exception as e:
        return (False, f"Verification check failed: {e}")


@register_verifier("nextcloud_download")
async def verify_download(args: dict, result: str) -> tuple[bool, str]:
    """Verify downloaded file exists locally."""
    local_path = args.get("local_path", "")
    if not local_path:
        return (False, "No local_path in tool args")

    if "error" in result.lower() and "downloaded" not in result.lower():
        return (True, "Tool reported failure — no verification needed")

    if os.path.exists(local_path):
        size = os.path.getsize(local_path)
        if size > 0:
            return (True, f"Verified: file exists at {local_path} ({size:,} bytes)")
        return (False, f"File exists at {local_path} but is 0 bytes")
    return (False, f"File NOT found at {local_path}")


# =============================================================================
# Verifiers — Memory Operations
# =============================================================================

@register_verifier("save_memory")
async def verify_save_memory(args: dict, result: str) -> tuple[bool, str]:
    """Verify memory was actually persisted to database."""
    # Extract memory ID from result: "Memory saved (#42): ..."
    match = re.search(r"#(\d+)", result)
    if not match:
        if "error" in result.lower():
            return (True, "Tool reported failure — no verification needed")
        return (False, f"Could not parse memory ID from result: {result[:100]}")

    memory_id = int(match.group(1))

    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            row = await conn.execute(
                "SELECT id, is_active, content FROM agent_memory WHERE id = %s",
                (memory_id,),
            )
            record = await row.fetchone()

        if record and record["is_active"]:
            return (True, f"Verified: memory #{memory_id} exists and is active")
        if record and not record["is_active"]:
            return (False, f"Memory #{memory_id} exists but is_active=FALSE")
        return (False, f"Memory #{memory_id} NOT found in database")
    except Exception as e:
        return (False, f"Verification check failed: {e}")


# =============================================================================
# Verifiers — Calendar Operations
# =============================================================================

@register_verifier("calendar_create_event")
async def verify_calendar_event(args: dict, result: str) -> tuple[bool, str]:
    """Verify calendar event was created.

    CalDAV doesn't give us the UID back easily in the tool result,
    so we verify by checking the calendar for an event matching the title
    on the specified date.
    """
    title = args.get("title", "")
    date = args.get("date", "")

    if not title or not date:
        return (False, "Missing title or date in tool args")

    if "error" in result.lower() and "created" not in result.lower():
        return (True, "Tool reported failure — no verification needed")

    # Query CalDAV for events on the target date
    calendar = args.get("calendar", "personal")
    base_url = env("NEXTCLOUD_URL", "http://nextcloud:80")
    user = env("NEXTCLOUD_USER")
    caldav_url = f"{base_url}/remote.php/dav/calendars/{user}/{calendar}/"

    try:
        start = f"{date}T000000Z"
        end_date = date  # same day
        # Simple: just check if any event on that date has our title
        report_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{date.replace('-','')}T000000Z" end="{date.replace('-','')}T235959Z"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

        async with httpx.AsyncClient(auth=_nc_auth(), timeout=10) as client:
            resp = await client.request(
                "REPORT", caldav_url,
                headers={"Content-Type": "application/xml", "Depth": "1"},
                content=report_body,
            )

        if resp.status_code in (200, 207):
            # Check if title appears in any returned event
            if title.lower() in resp.text.lower():
                return (True, f"Verified: event '{title}' found on {date}")
            return (False, f"Event '{title}' NOT found on {date} in calendar '{calendar}'")
        return (False, f"Calendar query failed: HTTP {resp.status_code}")
    except Exception as e:
        return (False, f"Verification check failed: {e}")


# =============================================================================
# Verifiers — Quick List Operations
# =============================================================================

@register_verifier("add_list_items")
async def verify_add_list_items(args: dict, result: str) -> tuple[bool, str]:
    """Verify items were added to the quick list."""
    # Result format varies — check if "added" appears
    if "error" in result.lower() and "added" not in result.lower():
        return (True, "Tool reported failure — no verification needed")

    # Extract list name or ID from args
    list_name = args.get("list_name", "")
    items_text = args.get("items", "")

    if not list_name:
        return (False, "No list_name in tool args")

    from src.memory.database import get_db
    try:
        async with get_db() as conn:
            # Find the list
            row = await conn.execute(
                "SELECT id FROM quick_lists WHERE LOWER(name) = LOWER(%s)",
                (list_name,),
            )
            list_record = await row.fetchone()
            if not list_record:
                return (False, f"Quick list '{list_name}' not found in database")

            # Count items — just verify the list isn't empty
            row = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM quick_list_items WHERE list_id = %s",
                (list_record["id"],),
            )
            count = await row.fetchone()
            if count and count["cnt"] > 0:
                return (True, f"Verified: list '{list_name}' has {count['cnt']} items")
            return (False, f"List '{list_name}' exists but has 0 items after add")
    except Exception as e:
        return (False, f"Verification check failed: {e}")
