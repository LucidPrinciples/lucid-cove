"""
Home tab routes — tasks, approvals, calendar, notifications.

Stuart-specific overlay routes that supplement cove-core.
These power the Home tab (home.js) and Overview tab (overview.js).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


async def _get_presence_id(request):
    """Operator id from session in multi mode; None in single mode (#191)."""
    from src.env import env
    if env("COVE_MODE", "single") != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
        return p["id"] if p else None
    except Exception:
        return None


def _parse_ical_dt(val: str, has_tzid: bool = False) -> str:
    """Convert iCal datetime to ISO string.

    If *has_tzid* is True the value already represents local time for a known
    timezone — return it WITHOUT a trailing ``Z`` so the frontend treats it as
    local rather than converting from UTC.  A trailing ``Z`` in the raw value
    means the event was stored as UTC and we keep that marker.
    """
    is_utc = val.strip().endswith("Z")
    val = val.strip().rstrip("Z")
    if len(val) == 8:
        return f"{val[:4]}-{val[4:6]}-{val[6:8]}"
    if len(val) >= 15:
        iso = f"{val[:4]}-{val[4:6]}-{val[6:8]}T{val[9:11]}:{val[11:13]}:{val[13:15]}"
        # Only append Z when the original value was explicitly UTC, not when
        # it carried a TZID (which means it's already in local time).
        if is_utc and not has_tzid:
            iso += "Z"
        return iso
    return val


# =============================================================================
# Tasks (GET)
# =============================================================================

@router.get("/api/tasks")
async def get_tasks(status: str = None, assignee: str = None, limit: int = 50, request: Request = None):
    """Task queue — filterable by status and/or assignee. Operator-scoped in multi mode."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request) if request else None
        async with get_db() as conn:
            conditions = []
            params = []
            if status:
                conditions.append("t.status = %s")
                params.append(status)
            else:
                conditions.append("t.status NOT IN ('done', 'cancelled')")
            if assignee:
                conditions.append("t.assignee = %s")
                params.append(assignee)
            if presence_id:
                conditions.append("t.presence_id = %s")
                params.append(presence_id)
            where = " AND ".join(conditions)
            params.append(limit)
            # For completed tasks, order by most recently completed
            order_clause = """t.completed_at DESC NULLS LAST""" if status == 'done' else """
                       CASE t.priority
                           WHEN 'urgent' THEN 1
                           WHEN 'high' THEN 2
                           WHEN 'normal' THEN 3
                           WHEN 'low' THEN 4
                           ELSE 5
                       END,
                       CASE t.status
                           WHEN 'in_progress' THEN 1
                           WHEN 'blocked' THEN 2
                           WHEN 'review' THEN 3
                           WHEN 'pending' THEN 4
                           ELSE 5
                       END,
                       t.updated_at DESC NULLS LAST"""
            result = await conn.execute(
                f"""SELECT t.id, t.title, t.description, t.status, t.priority, t.assignee,
                          t.project_id, t.due_date, t.notes, t.completed_at, t.created_at, t.updated_at,
                          p.name AS project_name
                   FROM tasks t
                   LEFT JOIN projects p ON t.project_id = p.id
                   WHERE {where}
                   ORDER BY {order_clause}
                   LIMIT %s""",
                tuple(params),
            )
            rows = await result.fetchall()
        return {"tasks": [dict(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Approvals — overview.js uses /api/approvals, home.js uses /api/bridge/approvals
# =============================================================================

@router.get("/api/approvals")
async def get_approvals():
    """Pending tool approval requests."""
    from src.tools.approval import get_pending_approvals
    pending = await get_pending_approvals()
    return {
        "approvals": [
            {
                "request_id": r.request_id,
                "tool_name": r.tool_name,
                "description": r.description,
                "args": r.args,
                "timestamp": r.timestamp,
                "status": r.status,
            }
            for r in pending
        ]
    }


@router.get("/api/bridge/approvals")
async def get_bridge_approvals():
    """Same as /api/approvals — alias for home.js compatibility."""
    return await get_approvals()


@router.get("/api/approvals/recent")
async def get_recent_approvals(limit: int = 20):
    """Recently resolved approvals with results — for agent context."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT request_id, tool_name, description, status, result,
                          resolved_at, resolved_by
                   FROM approval_requests
                   WHERE status IN ('approved', 'denied')
                   ORDER BY resolved_at DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = await result.fetchall()
        return {
            "approvals": [
                {
                    "request_id": r["request_id"],
                    "tool_name": r["tool_name"],
                    "description": r["description"],
                    "status": r["status"],
                    "result": r["result"],
                    "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
                    "resolved_by": r["resolved_by"],
                }
                for r in rows
            ]
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/approvals/{request_id}")
async def respond_to_approval(request_id: str, request: Request):
    """Approve or deny a pending tool request.

    Standard tools: re-runs the tool with stored args on approve.
    Site edits: merges the branch to main on approve (change already committed).
    Deny: marks denied + cleans up (deletes branch for site edits).

    #D14: After approval execution, inject a system message into the channel thread
    to trigger the agent's next turn automatically (the agent sees the resolution).
    """
    import json as _json
    from src.tools.approval import respond_to_approval as _respond, execute_approved_tool
    body = await request.json()
    approved = body.get("approved", False)

    # Load the approval record before responding (need tool_name + args + channel)
    from src.memory.database import get_db
    async with get_db() as conn:
        row = await (await conn.execute(
            "SELECT tool_name, args, channel FROM approval_requests WHERE request_id = %s",
            (request_id,),
        )).fetchone()

    if not row:
        return JSONResponse(
            {"error": f"No pending approval with id '{request_id}'"},
            status_code=404,
        )

    tool_name = row["tool_name"]
    tool_args = row["args"] if isinstance(row["args"], dict) else _json.loads(row["args"] or "{}")
    channel = row["channel"] or "day"
    is_site_edit = tool_name in ("site_edit_file", "site_create_file", "site_patch_file", "site_deploy")

    # Mark as approved/denied in DB
    success = await _respond(request_id, approved)
    if not success:
        return JSONResponse(
            {"error": f"No pending approval with id '{request_id}'"},
            status_code=404,
        )

    if approved:
        if is_site_edit:
            # Site edits: merge the branch (change is already committed)
            result = await _execute_site_approval(request_id, tool_args)
            exec_success = result.get("merged", False)
            exec_result_text = result.get("message", result.get("error", ""))
        else:
            # Standard tools: re-run with stored args
            exec_result = await execute_approved_tool(request_id)
            exec_success = exec_result.get("success", False)
            exec_result_text = exec_result.get("result", exec_result.get("error", ""))

        # #D14: Inject system message into channel thread to trigger agent continuation
        if channel:
            try:
                await _inject_approval_resolution_message(
                    channel=channel,
                    request_id=request_id,
                    tool_name=tool_name,
                    approved=True,
                    result=exec_result_text,
                    success=exec_success,
                )
            except Exception as e:
                # Best-effort: don't fail the approval if message injection fails
                import logging
                logging.getLogger("approval").warning(f"Failed to inject resolution message: {e}")

        if is_site_edit:
            return {
                "request_id": request_id,
                "status": "approved",
                "executed": exec_success,
                "result": exec_result_text,
                "site_edit": True,
            }
        else:
            return {
                "request_id": request_id,
                "status": "approved",
                "executed": exec_success,
                "result": exec_result_text,
            }

    # Denied
    if is_site_edit:
        # Clean up: delete the branch
        await _deny_site_approval(tool_args)

    # #D14: Also notify on denial so agent knows the operator declined
    if channel:
        try:
            await _inject_approval_resolution_message(
                channel=channel,
                request_id=request_id,
                tool_name=tool_name,
                approved=False,
                result="Operator denied the request",
                success=False,
            )
        except Exception as e:
            import logging
            logging.getLogger("approval").warning(f"Failed to inject denial message: {e}")

    return {"request_id": request_id, "status": "denied"}


async def _execute_site_approval(request_id: str, args: dict) -> dict:
    """Merge a site-edit branch to main after operator approval."""
    import logging
    log = logging.getLogger("approval.site")

    repo = args.get("repo", "")
    branch = args.get("branch", "")
    domain = args.get("domain", "")
    description = args.get("edit_description", args.get("description", "Site update"))

    if not repo or not branch:
        return {"merged": False, "error": "Missing repo or branch in approval args"}

    try:
        from src.config import get_feature_flags
        pat = get_feature_flags().get("github_pat", "")
        if not pat:
            return {"merged": False, "error": "GitHub PAT not configured"}

        from src.utils.github import github_merge_branch, github_delete_branch

        # Merge branch to main
        merge = await github_merge_branch(repo, "main", branch, f"Approved: {description}", pat)

        if merge.get("merged"):
            # Delete the feature branch
            await github_delete_branch(repo, branch, pat)

            # Save result to approval record
            from src.memory.database import get_db
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE approval_requests SET result = %s WHERE request_id = %s",
                    (f"Merged to main. Domain: {domain}. Cloudflare deploying.", request_id),
                )

            log.info(f"Site approval {request_id}: merged {branch} → main on {repo}")
            return {"merged": True, "message": f"Merged to main. {domain} deploying via Cloudflare."}
        else:
            error = merge.get("error", "Merge failed")
            log.warning(f"Site approval {request_id}: merge failed — {error}")
            return {"merged": False, "error": error}

    except Exception as e:
        log.error(f"Site approval {request_id} failed: {e}")
        return {"merged": False, "error": str(e)}


async def _deny_site_approval(args: dict) -> None:
    """Clean up a denied site edit — delete the feature branch."""
    repo = args.get("repo", "")
    branch = args.get("branch", "")
    if not repo or not branch:
        return

    try:
        from src.config import get_feature_flags
        pat = get_feature_flags().get("github_pat", "")
        if pat:
            from src.utils.github import github_delete_branch
            await github_delete_branch(repo, branch, pat)
    except Exception:
        pass  # Non-critical — branch cleanup is best-effort


async def _inject_approval_resolution_message(
    channel: str,
    request_id: str,
    tool_name: str,
    approved: bool,
    result: str,
    success: bool,
) -> None:
    """Inject a system message into the channel thread to trigger agent continuation.

    #D14: After approval resolution, the agent needs to know the result so it can
    continue its work. This injects a message as the "agent" node so the next
    turn sees the resolution context.
    """
    from datetime import datetime, timezone
    from langchain_core.messages import SystemMessage
    from src.memory.checkpointer import get_checkpointer
    from src.graphs.channels import get_channel_graph
    from src.memory.database import channel_db_scope
    from src.dashboard.routes.chat import _get_active_thread_id

    status = "approved and executed" if approved else "denied by operator"
    content = (
        f"[SYSTEM: Approval {request_id} for {tool_name} was {status}. "
        f"Result: {result[:200] if result else 'No result'}]"
    )

    async with channel_db_scope(channel):
        # Use a default thread ID for the channel (active thread)
        from fastapi import Request
        # Create a minimal request context to get the thread
        # This is best-effort; if we can't get the thread, we skip
        try:
            # Get the checkpointer and graph
            async with get_checkpointer() as checkpointer:
                graph = await get_channel_graph(channel, checkpointer)

                # #D23: the active thread(s) for this channel live in `chat_threads`, NOT
                # the nonexistent `thread_state` table. The old query raised
                # (relation "thread_state" does not exist) and the bare except swallowed
                # it, so #D14's auto-continuation was a SILENT no-op — approvals never
                # reached the agent. Same source of truth as delegation_tools._report_back.
                from src.memory.database import get_db
                async with get_db() as conn:
                    rows = await (await conn.execute(
                        """SELECT thread_id FROM chat_threads
                           WHERE channel = %s AND status = 'active'
                           ORDER BY created_at DESC LIMIT 5""",
                        (channel,),
                    )).fetchall()

                if not rows:
                    print(f"[approval-inject] no active thread for channel {channel!r}; "
                          f"resolution for {request_id} not delivered to the agent")
                    return

                # Inject into the active thread that actually holds this pending approval.
                for row in rows:
                    thread_id = row["thread_id"]
                    config = {"configurable": {"thread_id": thread_id}}
                    try:
                        state = await graph.aget_state(config)
                        if not state or not state.values:
                            continue
                        messages = list(state.values.get("messages") or [])
                        has_pending = any(
                            request_id in (getattr(m, "content", "") or "")
                            for m in messages[-10:]  # Check last 10 messages
                        )
                        if has_pending:
                            await graph.aupdate_state(
                                config,
                                {"messages": [SystemMessage(content=content)]},
                                as_node="agent",
                            )
                            return
                    except Exception as e:
                        print(f"[approval-inject] thread {thread_id} check failed: "
                              f"{type(e).__name__}: {e}")
                        continue

                print(f"[approval-inject] no active thread on {channel!r} referenced "
                      f"{request_id}; resolution not delivered to the agent")

        except Exception as e:
            # #D23: no longer a silent swallow — surface the failure so a broken inject is
            # visible in logs instead of approvals silently never reaching the agent.
            print(f"[approval-inject] failed for {request_id} on {channel!r}: "
                  f"{type(e).__name__}: {e}")


@router.post("/api/bridge/approvals/{request_id}")
async def respond_bridge_approval(request_id: str, request: Request):
    """Alias for home.js compatibility."""
    return await respond_to_approval(request_id, request)


# =============================================================================
# Calendar Events
# =============================================================================

async def _nc_creds(request: Request = None):
    """Get Nextcloud CalDAV credentials — per-user in multi mode, env vars in single."""
    from src.dashboard.routes.nextcloud import get_nc_creds
    return await get_nc_creds(request)


def _caldav_url(nc_url: str, nc_user: str, calendar: str = "personal") -> str:
    return f"{nc_url}/remote.php/dav/calendars/{nc_user}/{calendar}/"


def _parse_ical_event(cal_data: str) -> dict:
    """Parse a VCALENDAR string into an event dict."""
    ev = {}
    for line in cal_data.splitlines():
        if line.startswith("UID:"):
            ev["uid"] = line[4:]
        elif line.startswith("SUMMARY:"):
            ev["summary"] = line[8:]
        elif line.startswith("DTSTART"):
            # DTSTART may look like "DTSTART:20260518T160000Z" (UTC)
            # or "DTSTART;TZID=America/New_York:20260518T160000" (local).
            has_tzid = "TZID=" in line.split(":", 1)[0]
            val = line.split(":", 1)[-1]
            ev["start"] = _parse_ical_dt(val, has_tzid=has_tzid)
            ev["all_day"] = len(val.strip().rstrip("Z")) <= 8
        elif line.startswith("DTEND"):
            has_tzid = "TZID=" in line.split(":", 1)[0]
            val = line.split(":", 1)[-1]
            ev["end"] = _parse_ical_dt(val, has_tzid=has_tzid)
        elif line.startswith("LOCATION:"):
            ev["location"] = line[9:]
        elif line.startswith("DESCRIPTION:"):
            ev["description"] = line[12:]
    return ev


@router.get("/api/calendar/events")
async def get_calendar_events(request: Request, days: int = 14):
    """Upcoming calendar events from Nextcloud CalDAV."""
    import httpx
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone, timedelta

    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        return {"events": [], "error": "NEXTCLOUD_PASSWORD not configured"}

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    caldav = _caldav_url(nc_url, nc_user)

    report_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{now.strftime('%Y%m%dT%H%M%SZ')}" end="{end.strftime('%Y%m%dT%H%M%SZ')}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=30) as client:
            resp = await client.request(
                "REPORT", caldav,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=report_body,
            )

        if resp.status_code == 401:
            return {"events": [], "error": "Nextcloud auth failed (401). Check password — may need an app password from Settings > Security."}
        if resp.status_code != 207:
            return {"events": [], "error": f"CalDAV error: HTTP {resp.status_code}"}

        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
        events = []
        for response in root.findall(".//d:response", ns):
            # Extract href for edit/delete
            href = response.findtext("d:href", namespaces=ns) or ""
            cal_data = response.findtext(".//c:calendar-data", namespaces=ns)
            if not cal_data:
                continue
            ev = _parse_ical_event(cal_data)
            if ev.get("summary"):
                ev.setdefault("calendar", "personal")
                ev["href"] = href
                events.append(ev)

        events.sort(key=lambda e: e.get("start", ""))
        return {"events": events[:50]}

    except Exception as e:
        return {"events": [], "error": f"Error fetching calendar: {e}"}


# =============================================================================
# Calendar — Create Event
# =============================================================================

@router.post("/api/calendar/events")
async def create_calendar_event(request: Request):
    """Create a new Nextcloud CalDAV event.

    Body: { summary, start, end?, all_day?, location?, description?, calendar? }
    start/end: ISO date (YYYY-MM-DD) for all-day, or ISO datetime (YYYY-MM-DDTHH:MM)
    """
    import httpx
    import uuid
    from datetime import datetime

    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    body = await request.json()
    summary = body.get("summary", "").strip()
    if not summary:
        return JSONResponse({"error": "summary is required"}, status_code=400)

    start_str = body.get("start", "")
    end_str = body.get("end", "")
    all_day = body.get("all_day", len(start_str) <= 10)
    location = body.get("location", "")
    description = body.get("description", "")
    calendar = body.get("calendar", "personal")

    uid = str(uuid.uuid4())

    # Build iCal
    if all_day:
        dtstart = f"DTSTART;VALUE=DATE:{start_str.replace('-', '')}"
        if end_str:
            dtend = f"DTEND;VALUE=DATE:{end_str.replace('-', '')}"
        else:
            # All-day events: end = start + 1 day
            from datetime import timedelta
            d = datetime.strptime(start_str[:10], "%Y-%m-%d") + timedelta(days=1)
            dtend = f"DTEND;VALUE=DATE:{d.strftime('%Y%m%d')}"
    else:
        # Timed event — convert to UTC format
        s = start_str.replace("-", "").replace(":", "").replace("T", "T")
        if len(s) == 13:  # YYYYMMDDTHHM M
            s += "00"
        dtstart = f"DTSTART:{s}00Z" if not s.endswith("Z") and len(s) < 16 else f"DTSTART:{s}"
        if end_str:
            e = end_str.replace("-", "").replace(":", "").replace("T", "T")
            if len(e) == 13:
                e += "00"
            dtend = f"DTEND:{e}00Z" if not e.endswith("Z") and len(e) < 16 else f"DTEND:{e}"
        else:
            dtend = ""

    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    vcal = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now_stamp}
{dtstart}
{dtend}
SUMMARY:{summary}
{f'LOCATION:{location}' if location else ''}
{f'DESCRIPTION:{description}' if description else ''}
END:VEVENT
END:VCALENDAR"""

    # Clean empty lines
    vcal = "\n".join(line for line in vcal.splitlines() if line.strip())

    caldav = _caldav_url(nc_url, nc_user, calendar)
    event_url = f"{caldav}{uid}.ics"

    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=30) as client:
            resp = await client.put(
                event_url,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
                content=vcal,
            )

        if resp.status_code in (201, 204):
            return {"ok": True, "uid": uid}
        return JSONResponse({"error": f"CalDAV PUT returned {resp.status_code}: {resp.text[:200]}"}, status_code=resp.status_code)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Calendar — Update Event
# =============================================================================

@router.put("/api/calendar/events/{uid}")
async def update_calendar_event(uid: str, request: Request):
    """Update an existing Nextcloud CalDAV event by UID.

    Body: same as create — full replacement.
    """
    import httpx
    from datetime import datetime

    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    body = await request.json()
    summary = body.get("summary", "").strip()
    if not summary:
        return JSONResponse({"error": "summary is required"}, status_code=400)

    start_str = body.get("start", "")
    end_str = body.get("end", "")
    all_day = body.get("all_day", len(start_str) <= 10)
    location = body.get("location", "")
    description = body.get("description", "")
    calendar = body.get("calendar", "personal")

    if all_day:
        dtstart = f"DTSTART;VALUE=DATE:{start_str.replace('-', '')}"
        if end_str:
            dtend = f"DTEND;VALUE=DATE:{end_str.replace('-', '')}"
        else:
            from datetime import timedelta
            d = datetime.strptime(start_str[:10], "%Y-%m-%d") + timedelta(days=1)
            dtend = f"DTEND;VALUE=DATE:{d.strftime('%Y%m%d')}"
    else:
        s = start_str.replace("-", "").replace(":", "")
        dtstart = f"DTSTART:{s}Z" if not s.endswith("Z") else f"DTSTART:{s}"
        if end_str:
            e = end_str.replace("-", "").replace(":", "")
            dtend = f"DTEND:{e}Z" if not e.endswith("Z") else f"DTEND:{e}"
        else:
            dtend = ""

    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    vcal = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now_stamp}
{dtstart}
{dtend}
SUMMARY:{summary}
{f'LOCATION:{location}' if location else ''}
{f'DESCRIPTION:{description}' if description else ''}
END:VEVENT
END:VCALENDAR"""

    vcal = "\n".join(line for line in vcal.splitlines() if line.strip())

    # Use href from frontend if available (handles NC-created events with non-UID filenames)
    href = body.get("href", "")
    if href:
        event_url = f"{nc_url}{href}"
    else:
        caldav = _caldav_url(nc_url, nc_user, calendar)
        event_url = f"{caldav}{uid}.ics"

    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=30) as client:
            resp = await client.put(
                event_url,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
                content=vcal,
            )

        if resp.status_code in (200, 201, 204):
            return {"ok": True, "uid": uid}
        return JSONResponse({"error": f"CalDAV PUT returned {resp.status_code}"}, status_code=resp.status_code)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Calendar — Delete Event
# =============================================================================

@router.delete("/api/calendar/events/{uid}")
async def delete_calendar_event(uid: str, request: Request, calendar: str = "personal", href: str = ""):
    """Delete a Nextcloud CalDAV event by UID."""
    import httpx

    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        return JSONResponse({"error": "Nextcloud not configured"}, status_code=500)

    if href:
        event_url = f"{nc_url}{href}"
    else:
        caldav = _caldav_url(nc_url, nc_user, calendar)
        event_url = f"{caldav}{uid}.ics"

    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=30) as client:
            resp = await client.delete(event_url)

        if resp.status_code in (200, 204):
            return {"ok": True}
        if resp.status_code == 404:
            return JSONResponse({"error": "Event not found"}, status_code=404)
        return JSONResponse({"error": f"CalDAV DELETE returned {resp.status_code}"}, status_code=resp.status_code)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Event Links — associate calendar events with tasks/projects (local DB)
# =============================================================================

@router.get("/api/calendar/links")
async def get_event_links(uids: str = "", project_id: int = None, request: Request = None):
    """Batch-fetch event-to-task/project links by comma-separated UIDs or by project_id."""
    try:
        from src.memory.database import get_db

        presence_id = await _get_presence_id(request) if request else None

        # Build WHERE clause based on what's provided
        conditions = []
        params = []

        if uids:
            uid_list = [u.strip() for u in uids.split(",") if u.strip()]
            if uid_list:
                placeholders = ",".join(["%s"] * len(uid_list))
                conditions.append(f"el.event_uid IN ({placeholders})")
                params.extend(uid_list)

        if project_id is not None:
            conditions.append("el.project_id = %s")
            params.append(project_id)

        if not conditions:
            return {"links": {}}

        base = " OR ".join(conditions) if uids and project_id else " AND ".join(conditions)
        where = f"({base})"
        if presence_id:
            where += " AND el.presence_id = %s"
            params.append(presence_id)

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT el.event_uid, el.task_id, el.project_id,
                           t.title AS task_title, p.name AS project_name
                    FROM event_links el
                    LEFT JOIN tasks t ON el.task_id = t.id
                    LEFT JOIN projects p ON el.project_id = p.id
                    WHERE {where}""",
                tuple(params),
            )
            rows = await result.fetchall()
        links = {}
        for r in rows:
            d = dict(r)
            links[d["event_uid"]] = {
                "task_id": d.get("task_id"),
                "task_title": d.get("task_title") or "",
                "project_id": d.get("project_id"),
                "project_name": d.get("project_name") or "",
            }
        return {"links": links}
    except Exception as e:
        return {"links": {}, "error": str(e)}


@router.put("/api/calendar/links/{uid}")
async def upsert_event_link(uid: str, request: Request):
    """Create or update a calendar event's task/project link."""
    try:
        from src.memory.database import get_db
        body = await request.json()
        task_id = body.get("task_id")
        project_id = body.get("project_id")
        presence_id = await _get_presence_id(request)

        async with get_db() as conn:
            if task_id is None and project_id is None:
                # Both null — remove the link (scoped to the operator in multi mode)
                if presence_id:
                    await conn.execute(
                        "DELETE FROM event_links WHERE event_uid = %s AND presence_id = %s",
                        (uid, presence_id),
                    )
                else:
                    await conn.execute("DELETE FROM event_links WHERE event_uid = %s", (uid,))
            else:
                await conn.execute(
                    """INSERT INTO event_links (event_uid, task_id, project_id, presence_id, updated_at)
                       VALUES (%s, %s, %s, %s, NOW())
                       ON CONFLICT (event_uid) DO UPDATE
                       SET task_id = EXCLUDED.task_id,
                           project_id = EXCLUDED.project_id,
                           presence_id = EXCLUDED.presence_id,
                           updated_at = NOW()""",
                    (uid, task_id, project_id, presence_id),
                )
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/calendar/links/{uid}")
async def delete_event_link(uid: str, request: Request = None):
    """Remove a calendar event's task/project link."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request) if request else None
        async with get_db() as conn:
            if presence_id:
                await conn.execute(
                    "DELETE FROM event_links WHERE event_uid = %s AND presence_id = %s",
                    (uid, presence_id),
                )
            else:
                await conn.execute("DELETE FROM event_links WHERE event_uid = %s", (uid,))
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Notifications
# =============================================================================

@router.get("/api/notifications")
async def get_notifications():
    """Recent notifications from Stuart (NOTIFY tier actions)."""
    from src.tools.approval import get_notifications
    return {"notifications": get_notifications(clear=False)}


@router.post("/api/notifications/clear")
async def clear_notifications():
    """Clear the notification queue."""
    from src.tools.approval import get_notifications
    cleared = get_notifications(clear=True)
    return {"cleared": len(cleared)}
