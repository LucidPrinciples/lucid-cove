"""
Project routes — full CRUD for projects, tasks, and comments.

Provides the data and actions for the Projects tab:
  - Project list, create, update
  - Task list, create, update (status cycling, priority, assignee)
  - Task detail with sub-tasks, history, comments
  - Comment list and create (project-level or task-level)
  - Workflow state tracking and audit trail
"""

import logging
import os
from src.env import env
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import get_operator_name

router = APIRouter()
log = logging.getLogger(__name__)

COVE_MODE = env("COVE_MODE", "single")


async def _get_presence_row(request: Request):
    """Current presence dict in multi mode, else None."""
    if COVE_MODE != "multi":
        return None
    try:
        from src.dashboard.routes.presence import get_current_presence
        return await get_current_presence(request)
    except Exception:
        return None


async def _get_presence_id(request: Request):
    """Get presence_id from cookie in multi mode. Returns None in single mode."""
    presence = await _get_presence_row(request)
    return presence["id"] if presence else None


def _is_cove_admin(presence) -> bool:
    if not presence:
        return False
    role = (presence.get("cove_role") or "").strip().lower()
    return role in ("admin", "steward")


def _full_cove_board(request: Request, presence) -> bool:
    """True when this MC door should list the full Cove project board.

    Product rule (multi-presence):
      - Steward/merchant manager MC (manager host, ?as=<steward|merchant>)
        and Cove admin apex: all Cove rows (presence_id IS NULL), plus own personal.
      - Personal agent MC (presence handle door), even if cove_role=admin:
        own personal + Cove projects the presence is attached to only.

    Single-mode / no presence: full Cove board (legacy).
    """
    if COVE_MODE != "multi":
        return True
    if not presence:
        return True
    try:
        from src.config import load_cove_config
        from src.dashboard.host_context import resolve_host_context, request_host

        cove = load_cove_config()
        hc = resolve_host_context(request_host(request), cove)
        as_param = (request.query_params.get("as") or "").strip().lower()
        force_personal = False
        if as_param:
            own = (presence.get("username") or "").lstrip("@").strip().lower()
            role = (presence.get("cove_role") or "").strip().lower()
            mgrs = {
                ((cove.get("steward_channel") or {}).get("name") or "").strip().lower(),
                ((cove.get("merchant_channel") or {}).get("name") or "").strip().lower(),
            } - {""}
            if role == "admin" and as_param in mgrs:
                hc = {**hc, "kind": "manager", "label": as_param}
            elif as_param == own:
                force_personal = True
        if force_personal:
            return False
        kind = (hc.get("kind") or "").strip().lower()
        if kind == "manager":
            return True
        # Cove apex admin surface (not a personal handle door)
        if kind == "cove" and _is_cove_admin(presence):
            return True
        return False
    except Exception:
        # Fail closed: personal/member scope if door cannot be resolved.
        return False


def _presence_filter(presence_id, full_cove_board: bool = False):
    """SQL WHERE + params for project list.

    - Single / no presence: Cove board only (presence_id IS NULL).
    - Manager / Cove-admin door: Cove board + own personal rows.
    - Personal MC (any role): own personal + Cove projects they are attached to.
    """
    if not presence_id:
        return "p.presence_id IS NULL", ()
    if full_cove_board:
        return "(p.presence_id IS NULL OR p.presence_id = %s)", (presence_id,)
    return (
        "(p.presence_id = %s OR (p.presence_id IS NULL AND EXISTS ("
        "SELECT 1 FROM project_members pm "
        "WHERE pm.project_id = p.id AND pm.presence_id = %s)))",
        (presence_id, presence_id),
    )


def _task_scope(presence_id):
    """Extra WHERE fragment + params to scope a task query to one operator.
    Empty in single mode (presence_id None) so behavior is unchanged. (#191)"""
    if presence_id:
        return " AND presence_id = %s", [presence_id]
    return "", []


async def _owns_project(conn, project_id, presence_id, full_cove_board: bool = False):
    """True if caller may open this project on this MC door."""
    if not presence_id:
        return True
    if full_cove_board:
        r = await conn.execute(
            "SELECT 1 FROM projects WHERE id = %s AND (presence_id IS NULL OR presence_id = %s)",
            (project_id, presence_id),
        )
        return (await r.fetchone()) is not None
    r = await conn.execute(
        """SELECT 1 FROM projects p
           WHERE p.id = %s AND (
             p.presence_id = %s
             OR (p.presence_id IS NULL AND EXISTS (
               SELECT 1 FROM project_members pm
               WHERE pm.project_id = p.id AND pm.presence_id = %s
             ))
           )""",
        (project_id, presence_id, presence_id),
    )
    return (await r.fetchone()) is not None


async def _owns_task(conn, task_id, presence_id, full_cove_board: bool = False):
    """True if the task is visible to this operator on this MC door."""
    if not presence_id:
        return True
    # Task on an accessible project, or legacy task presence_id match
    r = await conn.execute(
        """SELECT t.id, t.presence_id, t.project_id FROM tasks t WHERE t.id = %s""",
        (task_id,),
    )
    row = await r.fetchone()
    if not row:
        return False
    if row.get("presence_id") and str(row["presence_id"]) == str(presence_id):
        return True
    if row.get("project_id"):
        return await _owns_project(conn, row["project_id"], presence_id, full_cove_board=full_board)
    return full_cove_board


# =============================================================================
# CalDAV sync helpers — push task due_dates to Nextcloud as VEVENT entries
# =============================================================================

async def _nc_creds(request=None):
    """Get Nextcloud credentials — per-user in multi mode, env vars in single."""
    from src.dashboard.routes.nextcloud import get_nc_creds
    return await get_nc_creds(request)


def _caldav_url(nc_url: str, nc_user: str, calendar: str = "personal") -> str:
    return f"{nc_url}/remote.php/dav/calendars/{nc_user}/{calendar}/"


async def _caldav_create_event(summary: str, date_str: str, description: str = "", request=None) -> str | None:
    """Create an all-day CalDAV event in Nextcloud. Returns UID or None on failure."""
    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        log.warning("CalDAV sync skipped — Nextcloud not configured")
        return None

    uid = str(uuid.uuid4())
    end_date = (datetime.strptime(date_str[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    vcal = "\n".join(line for line in f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now_stamp}
DTSTART;VALUE=DATE:{date_str[:10].replace('-', '')}
DTEND;VALUE=DATE:{end_date}
SUMMARY:{summary}
{f'DESCRIPTION:{description}' if description else ''}
END:VEVENT
END:VCALENDAR""".splitlines() if line.strip())

    event_url = f"{_caldav_url(nc_url, nc_user)}{uid}.ics"
    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=15) as client:
            resp = await client.put(
                event_url,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
                content=vcal,
            )
        if resp.status_code in (201, 204):
            log.info("CalDAV event created: %s → %s", uid, summary)
            return uid
        log.error("CalDAV create failed %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.error("CalDAV create error: %s", e)
    return None


async def _caldav_update_event(uid: str, summary: str, date_str: str, description: str = "", request=None) -> bool:
    """Update an existing CalDAV event (full replacement). Returns success."""
    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        return False

    end_date = (datetime.strptime(date_str[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    vcal = "\n".join(line for line in f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MC Dashboard//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now_stamp}
DTSTART;VALUE=DATE:{date_str[:10].replace('-', '')}
DTEND;VALUE=DATE:{end_date}
SUMMARY:{summary}
{f'DESCRIPTION:{description}' if description else ''}
END:VEVENT
END:VCALENDAR""".splitlines() if line.strip())

    event_url = f"{_caldav_url(nc_url, nc_user)}{uid}.ics"
    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=15) as client:
            resp = await client.put(
                event_url,
                headers={"Content-Type": "text/calendar; charset=utf-8"},
                content=vcal,
            )
        if resp.status_code in (201, 204):
            log.info("CalDAV event updated: %s", uid)
            return True
        log.error("CalDAV update failed %d", resp.status_code)
    except Exception as e:
        log.error("CalDAV update error: %s", e)
    return False


async def _caldav_delete_event(uid: str, request=None) -> bool:
    """Delete a CalDAV event by UID. Returns success."""
    nc_url, nc_user, nc_pass = await _nc_creds(request)
    if not nc_pass:
        return False

    event_url = f"{_caldav_url(nc_url, nc_user)}{uid}.ics"
    try:
        async with httpx.AsyncClient(auth=(nc_user, nc_pass), timeout=15) as client:
            resp = await client.delete(event_url)
        if resp.status_code in (200, 204, 404):
            log.info("CalDAV event deleted: %s", uid)
            return True
        log.error("CalDAV delete failed %d", resp.status_code)
    except Exception as e:
        log.error("CalDAV delete error: %s", e)
    return False


async def _sync_task_caldav(task_id: int, task_title: str, project_id: int | None,
                            due_date: str | None, old_due_date: str | None = None, request=None):
    """Sync a task's due_date to CalDAV. Handles create, update, and delete.

    - due_date set, no existing link → create CalDAV event + event_link
    - due_date changed, existing link → update CalDAV event
    - due_date removed, existing link → delete CalDAV event + event_link
    """
    from src.memory.database import get_db

    # Look up existing event_link for this task
    existing_uid = None
    try:
        async with get_db() as conn:
            result = await conn.execute(
                "SELECT event_uid FROM event_links WHERE task_id = %s", (task_id,)
            )
            row = await result.fetchone()
            if row:
                existing_uid = dict(row)["event_uid"]
    except Exception as e:
        log.error("event_links lookup failed: %s", e)

    desc = f"MC Task #{task_id} deadline"

    if due_date and not existing_uid:
        # Create new CalDAV event
        uid = await _caldav_create_event(f"📋 {task_title}", due_date, desc, request=request)
        if uid:
            try:
                async with get_db() as conn:
                    await conn.execute(
                        """INSERT INTO event_links (event_uid, task_id, project_id, updated_at)
                           VALUES (%s, %s, %s, NOW())
                           ON CONFLICT (event_uid) DO UPDATE
                           SET task_id = EXCLUDED.task_id,
                               project_id = EXCLUDED.project_id,
                               updated_at = NOW()""",
                        (uid, task_id, project_id),
                    )
            except Exception as e:
                log.error("event_link insert failed: %s", e)

    elif due_date and existing_uid:
        # Update existing CalDAV event (date or title may have changed)
        await _caldav_update_event(existing_uid, f"📋 {task_title}", due_date, desc, request=request)
        # Also update project_id in event_link if changed
        try:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE event_links SET project_id = %s, updated_at = NOW() WHERE event_uid = %s",
                    (project_id, existing_uid),
                )
        except Exception as e:
            log.error("event_link update failed: %s", e)

    elif not due_date and existing_uid:
        # Due date removed — delete CalDAV event and link
        await _caldav_delete_event(existing_uid, request=request)
        try:
            async with get_db() as conn:
                await conn.execute("DELETE FROM event_links WHERE event_uid = %s", (existing_uid,))
        except Exception as e:
            log.error("event_link delete failed: %s", e)


def _slugify(name: str) -> str:
    """Convert project name to URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s[:60]


def _serialize(row):
    """Convert a DB row to a JSON-safe dict."""
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
    return d


# =============================================================================
# Projects
# =============================================================================

@router.get("/api/projects")
async def list_projects(request: Request):
    """All active projects with task counts, scoped by presence in multi mode."""
    try:
        from src.memory.database import get_db
        presence = await _get_presence_row(request)
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)
        where_clause, where_params = _presence_filter(
            presence_id, full_cove_board=full_board
        )

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT p.*,
                          COUNT(t.id) FILTER (WHERE t.status != 'done') as open_tasks,
                          COUNT(t.id) FILTER (WHERE t.status = 'done') as done_tasks,
                          COUNT(t.id) as total_tasks,
                          MIN(CASE t.priority
                              WHEN 'urgent' THEN 1
                              WHEN 'high' THEN 2
                              WHEN 'normal' THEN 3
                              WHEN 'low' THEN 4
                              ELSE 5 END)
                          FILTER (WHERE t.status NOT IN ('done', 'cancelled')) as priority_rank,
                          (array_agg(t.priority ORDER BY CASE t.priority
                              WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
                              WHEN 'normal' THEN 3 WHEN 'low' THEN 4 ELSE 5 END)
                          FILTER (WHERE t.status NOT IN ('done', 'cancelled')))[1] as top_priority
                   FROM projects p
                   LEFT JOIN tasks t ON t.project_id = p.id
                   WHERE p.status NOT IN ('archived', 'cancelled')
                     AND {where_clause}
                   GROUP BY p.id
                   ORDER BY COALESCE(MIN(CASE t.priority
                              WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
                              WHEN 'normal' THEN 3 WHEN 'low' THEN 4 ELSE 5 END)
                          FILTER (WHERE t.status NOT IN ('done', 'cancelled')), 99),
                          p.updated_at DESC""",
                where_params,
            )
            rows = await result.fetchall()
        return {"projects": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/projects")
async def create_project(request: Request):
    """Create a project.

    Admin/steward UI creates Cove projects (presence_id NULL) by default.
    Members create personal projects. body.scope='cove' is ignored for members
    (use chat with Stuart + attach instead).
    """
    try:
        from src.memory.database import get_db
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "Project name required"}, status_code=400)

        description = body.get("description", "")
        slug = _slugify(name)
        owner = body.get("owner", get_operator_name().lower())
        goals = body.get("goals", "")
        presence = await _get_presence_row(request)
        presence_id = presence["id"] if presence else None
        # Manager / Cove-admin door → Cove board; personal MC → personal
        full_board = _full_cove_board(request, presence)
        if full_board or not presence_id:
            insert_presence = None
        else:
            insert_presence = presence_id

        async with get_db() as conn:
            result = await conn.execute(
                """INSERT INTO projects (presence_id, slug, name, description, owner, goals)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (insert_presence, slug, name, description, owner, goals),
            )
            row = await result.fetchone()

        # Parity with agent create_project: every project gets a linked Plan.
        try:
            from src.dashboard.routes import briefs as br

            br.ensure_project_plan(
                name=name,
                project_slug=slug,
                description=description or "",
                goals=goals or "",
                published_by="ui_create_project",
            )
        except Exception as e:
            log.warning("ui create_project plan ensure failed: %s", e)

        # Cove-board creates: seed NC folder + attach the signed-in presence.
        if insert_presence is None:
            try:
                from src.tools.nextcloud_tools import (
                    set_team_nc_creds,
                    clear_request_nc_creds,
                )
                from src.tools.project_tools import (
                    _ensure_project_nc_folder,
                    _attach_presence_to_project,
                )

                nc_tok = set_team_nc_creds()
                try:
                    await _ensure_project_nc_folder(name)
                    if presence_id:
                        await _attach_presence_to_project(
                            int(row["id"]), str(presence_id), "work", name
                        )
                finally:
                    try:
                        clear_request_nc_creds(nc_tok)
                    except Exception:
                        pass
            except Exception as e:
                log.warning("ui create_project NC side effects: %s", e)

        return _serialize(row)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/projects/{project_id}")
async def update_project(project_id: int, request: Request):
    """Update project fields (name, description, status, goals, team)."""
    try:
        from src.memory.database import get_db
        body = await request.json()

        allowed = {"name", "description", "status", "goals", "team", "owner"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            return JSONResponse({"error": "No valid fields to update"}, status_code=400)

        # Build SET clause
        set_parts = []
        values = []
        for k, v in updates.items():
            set_parts.append(f"{k} = %s")
            values.append(v)
        set_parts.append("updated_at = NOW()")
        values.append(project_id)

        presence = await _get_presence_row(request)
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)

        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id, full_cove_board=full_board):
                return JSONResponse({"error": "Project not found"}, status_code=404)
            result = await conn.execute(
                f"UPDATE projects SET {', '.join(set_parts)} WHERE id = %s RETURNING *",
                tuple(values),
            )
            row = await result.fetchone()
        if not row:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return _serialize(row)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/projects/{project_id}")
async def get_project_detail(project_id: int, request: Request = None):
    """Full project detail with tasks, comments, and activity."""
    try:
        from src.memory.database import get_db
        presence = await _get_presence_row(request) if request else None
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)
        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id, full_cove_board=full_board):
                return JSONResponse({"error": "Project not found"}, status_code=404)
            result = await conn.execute(
                "SELECT * FROM projects WHERE id = %s", (project_id,)
            )
            project = await result.fetchone()
            if not project:
                return JSONResponse({"error": "Project not found"}, status_code=404)

            # Tasks (with sub-task counts)
            result = await conn.execute(
                """SELECT t.*,
                          COUNT(sub.id) FILTER (WHERE sub.status != 'done') as open_subtasks,
                          COUNT(sub.id) FILTER (WHERE sub.status = 'done') as done_subtasks,
                          COUNT(sub.id) as total_subtasks
                   FROM tasks t
                   LEFT JOIN tasks sub ON sub.parent_task_id = t.id
                   WHERE t.project_id = %s AND t.parent_task_id IS NULL
                   GROUP BY t.id
                   ORDER BY
                     CASE t.status
                       WHEN 'in_progress' THEN 0
                       WHEN 'blocked' THEN 1
                       WHEN 'pending' THEN 2
                       WHEN 'review' THEN 3
                       WHEN 'done' THEN 4
                     END,
                     CASE t.priority
                       WHEN 'urgent' THEN 0
                       WHEN 'high' THEN 1
                       WHEN 'normal' THEN 2
                       WHEN 'low' THEN 3
                     END,
                     t.created_at DESC""",
                (project_id,),
            )
            tasks = await result.fetchall()

            # Comments
            result = await conn.execute(
                """SELECT * FROM project_comments
                   WHERE project_id = %s
                   ORDER BY created_at DESC LIMIT 50""",
                (project_id,),
            )
            comments = await result.fetchall()

        proj = _serialize(project)
        brief_meta = None
        try:
            from src.dashboard.routes import briefs as br

            linked = br.brief_for_project(proj.get("slug") or "")
            if not linked:
                # Backfill starter plan for projects created before plan-on-create
                # or when publish failed silently (agent/UI paths now share ensure).
                meta, status = br.ensure_project_plan(
                    name=proj.get("name") or proj.get("slug") or "",
                    project_slug=proj.get("slug") or "",
                    description=proj.get("description") or "",
                    goals=proj.get("goals") or "",
                    published_by="project_detail_backfill",
                )
                if meta:
                    linked = meta
                    log.info(
                        "backfilled plan for project %s (%s)",
                        proj.get("slug"),
                        status,
                    )
            if linked:
                body = br._read_body(linked)
                brief_meta = {
                    "slug": linked.get("slug"),
                    "title": linked.get("title"),
                    "kind": linked.get("kind"),
                    "status": linked.get("status"),
                    "summary": linked.get("summary") or "",
                    "url": br.reader_url(linked.get("slug") or ""),
                    "updated_at": linked.get("updated_at") or "",
                    "html": br._render_html(body),
                }
        except Exception:
            brief_meta = None

        return {
            "project": proj,
            "tasks": [_serialize(t) for t in tasks],
            "comments": [_serialize(c) for c in comments],
            "brief": brief_meta,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/projects/by-slug/{slug}")
async def get_project_by_slug(slug: str, request: Request):
    """Get project by slug — used for URL routing. Scoped by presence + attach."""
    try:
        from src.memory.database import get_db
        presence = await _get_presence_row(request)
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)
        where, params = _presence_filter(presence_id, full_cove_board=full_board)
        async with get_db() as conn:
            result = await conn.execute(
                f"SELECT p.id FROM projects p WHERE p.slug = %s AND {where}",
                (slug, *params),
            )
            row = await result.fetchone()
        if not row:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return await get_project_detail(dict(row)["id"], request)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Tasks
# =============================================================================

@router.post("/api/tasks")
async def create_task(request: Request):
    """Create a new task."""
    try:
        from src.memory.database import get_db
        body = await request.json()
        title = body.get("title", "").strip()
        if not title:
            return JSONResponse({"error": "Task title required"}, status_code=400)

        due_date = body.get("due_date") or None
        project_id = body.get("project_id")
        parent_task_id = body.get("parent_task_id") or None
        workflow_pattern = body.get("workflow_pattern") or None

        presence_id = await _get_presence_id(request)
        async with get_db() as conn:
            result = await conn.execute(
                """INSERT INTO tasks (project_id, parent_task_id, title, description,
                   priority, assignee, created_by, due_date, workflow_pattern, presence_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    project_id,
                    parent_task_id,
                    title,
                    body.get("description", ""),
                    body.get("priority", "normal"),
                    body.get("assignee", "stuart"),
                    body.get("created_by", get_operator_name().lower()),
                    due_date,
                    workflow_pattern,
                    presence_id,
                ),
            )
            row = await result.fetchone()

        task = _serialize(row)

        # Sync due_date to Nextcloud CalDAV
        if due_date:
            task_id = dict(row)["id"] if row else None
            if task_id:
                try:
                    await _sync_task_caldav(task_id, title, project_id, due_date, request=request)
                except Exception as e:
                    log.error("CalDAV sync on create failed: %s", e)

        return task
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, request: Request):
    """Update task fields. Auto-logs every change to task_history."""
    try:
        from src.memory.database import get_db
        body = await request.json()

        allowed = {
            "status", "title", "description", "priority", "assignee",
            "due_date", "notes", "project_id", "parent_task_id",
            "workflow_pattern", "workflow_state", "audit_verdict", "audit_count",
        }
        updates = {k: v for k, v in body.items() if k in allowed}
        changed_by = body.get("changed_by", "system")
        if not updates:
            return JSONResponse({"error": "No valid fields to update"}, status_code=400)

        # Auto-set completed_at
        if updates.get("status") == "done":
            updates["completed_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat()
        elif "status" in updates and updates["status"] != "done":
            updates["completed_at"] = None

        # Fetch old task state (for history logging + CalDAV sync), operator-scoped
        presence_id = await _get_presence_id(request)
        scope, scope_params = _task_scope(presence_id)
        async with get_db() as conn:
            result = await conn.execute(
                f"SELECT * FROM tasks WHERE id = %s{scope}",
                tuple([task_id] + scope_params),
            )
            old_row = await result.fetchone()
        if not old_row:
            return JSONResponse({"error": "Task not found"}, status_code=404)
        old_data = dict(old_row)

        # Build update
        set_parts = []
        values = []
        for k, v in updates.items():
            set_parts.append(f"{k} = %s")
            values.append(v)
        set_parts.append("updated_at = NOW()")
        values.append(task_id)

        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s{scope} RETURNING *",
                tuple(values + scope_params),
            )
            row = await result.fetchone()

            # Auto-log changes to task_history
            for field, new_val in updates.items():
                if field == "completed_at":
                    continue  # auto-derived, not user action
                old_val = old_data.get(field)
                old_str = str(old_val) if old_val is not None else None
                new_str = str(new_val) if new_val is not None else None
                if old_str != new_str:
                    try:
                        await conn.execute(
                            """INSERT INTO task_history
                               (task_id, field_changed, old_value, new_value, changed_by)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (task_id, field, old_str, new_str, changed_by),
                        )
                    except Exception as he:
                        log.error("task_history insert failed: %s", he)

        task_data = dict(row)

        # CalDAV sync — push due_date changes to Nextcloud
        needs_caldav_sync = "due_date" in updates or "title" in updates or "project_id" in updates
        if needs_caldav_sync:
            old_due_date = str(old_data["due_date"]) if old_data.get("due_date") else None
            old_title = old_data.get("title", "")
            old_project_id = old_data.get("project_id")
            new_due_date = str(task_data["due_date"]) if task_data.get("due_date") else None
            new_title = task_data.get("title", old_title)
            new_project_id = task_data.get("project_id", old_project_id)
            date_changed = new_due_date != old_due_date
            meta_changed = (new_title != old_title or new_project_id != old_project_id) and new_due_date
            if date_changed or meta_changed:
                try:
                    await _sync_task_caldav(task_id, new_title, new_project_id,
                                            new_due_date, old_due_date, request=request)
                except Exception as e:
                    log.error("CalDAV sync on update failed: %s", e)

        return _serialize(row)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/tasks/{task_id}/detail")
async def get_task_detail(task_id: int, request: Request = None):
    """Full task detail: task fields, sub-tasks, history, comments."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request) if request else None
        scope, scope_params = _task_scope(presence_id)
        async with get_db() as conn:
            # Task itself (operator-scoped in multi mode)
            result = await conn.execute(
                f"SELECT * FROM tasks WHERE id = %s{scope}",
                tuple([task_id] + scope_params),
            )
            task = await result.fetchone()
            if not task:
                return JSONResponse({"error": "Task not found"}, status_code=404)

            # Sub-tasks
            result = await conn.execute(
                """SELECT * FROM tasks WHERE parent_task_id = %s
                   ORDER BY
                     CASE status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1
                       WHEN 'pending' THEN 2 WHEN 'review' THEN 3 WHEN 'done' THEN 4
                     END, created_at""",
                (task_id,),
            )
            subtasks = await result.fetchall()

            # History (audit trail)
            result = await conn.execute(
                """SELECT * FROM task_history WHERE task_id = %s
                   ORDER BY changed_at DESC LIMIT 100""",
                (task_id,),
            )
            history = await result.fetchall()

            # Comments attached to this task
            result = await conn.execute(
                """SELECT * FROM project_comments WHERE task_id = %s
                   ORDER BY created_at DESC LIMIT 50""",
                (task_id,),
            )
            comments = await result.fetchall()

            # Parent task info (if this is a sub-task)
            parent = None
            task_dict = dict(task)
            if task_dict.get("parent_task_id"):
                result = await conn.execute(
                    "SELECT id, title, status FROM tasks WHERE id = %s",
                    (task_dict["parent_task_id"],),
                )
                parent = await result.fetchone()

            # Project info
            project = None
            if task_dict.get("project_id"):
                result = await conn.execute(
                    "SELECT id, name, slug FROM projects WHERE id = %s",
                    (task_dict["project_id"],),
                )
                project = await result.fetchone()

        return {
            "task": _serialize(task),
            "subtasks": [_serialize(s) for s in subtasks],
            "history": [_serialize(h) for h in history],
            "comments": [_serialize(c) for c in comments],
            "parent": _serialize(parent) if parent else None,
            "project": _serialize(project) if project else None,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/tasks/{task_id}/subtasks")
async def get_task_subtasks(task_id: int, request: Request = None):
    """Sub-tasks of a parent task."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request) if request else None
        scope, scope_params = _task_scope(presence_id)
        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT * FROM tasks WHERE parent_task_id = %s{scope}
                   ORDER BY
                     CASE status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1
                       WHEN 'pending' THEN 2 WHEN 'review' THEN 3 WHEN 'done' THEN 4
                     END, created_at""",
                tuple([task_id] + scope_params),
            )
            rows = await result.fetchall()
        return {"subtasks": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/tasks/{task_id}/history")
async def get_task_history(task_id: int, request: Request = None):
    """Audit trail for a task — all field changes."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request) if request else None
        async with get_db() as conn:
            if not await _owns_task(conn, task_id, presence_id):
                return {"history": []}
            result = await conn.execute(
                """SELECT * FROM task_history WHERE task_id = %s
                   ORDER BY changed_at DESC LIMIT 200""",
                (task_id,),
            )
            rows = await result.fetchall()
        return {"history": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/tasks/{task_id}/comments")
async def add_task_comment(task_id: int, request: Request):
    """Add a comment to a specific task."""
    try:
        from src.memory.database import get_db
        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return JSONResponse({"error": "Comment content required"}, status_code=400)

        author = body.get("author", get_operator_name().lower())

        # Get the task's project_id for the FK (operator-scoped in multi mode)
        presence_id = await _get_presence_id(request)
        scope, scope_params = _task_scope(presence_id)
        async with get_db() as conn:
            result = await conn.execute(
                f"SELECT project_id FROM tasks WHERE id = %s{scope}",
                tuple([task_id] + scope_params),
            )
            task_row = await result.fetchone()
            if not task_row:
                return JSONResponse({"error": "Task not found"}, status_code=404)

            project_id = dict(task_row).get("project_id")
            if not project_id:
                return JSONResponse({"error": "Task has no project"}, status_code=400)

            result = await conn.execute(
                """INSERT INTO project_comments (project_id, task_id, author, content)
                   VALUES (%s, %s, %s, %s)
                   RETURNING *""",
                (project_id, task_id, author, content),
            )
            row = await result.fetchone()
        return _serialize(row)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/projects/{project_id}/tasks")
async def get_project_tasks(project_id: int, status: str = None, request: Request = None):
    """Tasks for a specific project (all tasks once the project is accessible)."""
    try:
        from src.memory.database import get_db
        presence = await _get_presence_row(request) if request else None
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)
        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id, full_cove_board=full_board):
                return JSONResponse({"error": "Project not found"}, status_code=404)
            if status:
                result = await conn.execute(
                    """SELECT * FROM tasks WHERE project_id = %s AND status = %s
                       ORDER BY created_at DESC""",
                    (project_id, status),
                )
            else:
                result = await conn.execute(
                    """SELECT * FROM tasks WHERE project_id = %s
                       ORDER BY
                         CASE status
                           WHEN 'in_progress' THEN 0
                           WHEN 'blocked' THEN 1
                           WHEN 'pending' THEN 2
                           WHEN 'review' THEN 3
                           WHEN 'done' THEN 4
                         END,
                         created_at DESC""",
                    (project_id,),
                )
            rows = await result.fetchall()
        return {"tasks": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/tasks/misc")
async def get_misc_tasks(request: Request):
    """Tasks not attached to any project. In multi mode, returns empty
    (all tasks should belong to a project for proper scoping)."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request)

        # In multi mode, misc tasks have no project FK to scope through.
        # Return empty — multi-mode users should use projects.
        if presence_id:
            return {"tasks": []}

        async with get_db() as conn:
            result = await conn.execute(
                """SELECT * FROM tasks WHERE project_id IS NULL
                   AND status NOT IN ('done', 'cancelled')
                   ORDER BY created_at DESC LIMIT 50"""
            )
            rows = await result.fetchall()
        return {"tasks": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Comments
# =============================================================================

@router.get("/api/projects/{project_id}/comments")
async def get_project_comments(project_id: int, request: Request = None):
    """Comments for a project."""
    try:
        from src.memory.database import get_db
        presence_id = await _get_presence_id(request) if request else None
        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id):
                return {"comments": []}
            result = await conn.execute(
                """SELECT * FROM project_comments
                   WHERE project_id = %s
                   ORDER BY created_at DESC LIMIT 100""",
                (project_id,),
            )
            rows = await result.fetchall()
        return {"comments": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/projects/{project_id}/comments")
async def add_project_comment(project_id: int, request: Request):
    """Add a comment to a project."""
    try:
        from src.memory.database import get_db
        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return JSONResponse({"error": "Comment content required"}, status_code=400)

        author = body.get("author", get_operator_name().lower())
        presence_id = await _get_presence_id(request)

        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id):
                return JSONResponse({"error": "Project not found"}, status_code=404)
            result = await conn.execute(
                """INSERT INTO project_comments (project_id, author, content)
                   VALUES (%s, %s, %s)
                   RETURNING *""",
                (project_id, author, content),
            )
            row = await result.fetchone()
        return _serialize(row)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# Project members (Cove attach)
# =============================================================================

@router.get("/api/projects/{project_id}/members")
async def list_project_members(project_id: int, request: Request):
    """List presences attached to a Cove project."""
    try:
        from src.memory.database import get_db
        presence = await _get_presence_row(request)
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)
        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id, full_cove_board=full_board):
                return JSONResponse({"error": "Project not found"}, status_code=404)
            result = await conn.execute(
                """SELECT pm.presence_id, pm.role, pm.created_at,
                          a.username, a.display_name
                   FROM project_members pm
                   LEFT JOIN accounts a ON a.id = pm.presence_id
                   WHERE pm.project_id = %s
                   ORDER BY pm.created_at""",
                (project_id,),
            )
            rows = await result.fetchall()
        return {"members": [_serialize(r) for r in rows]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/projects/{project_id}/members")
async def add_project_member(project_id: int, request: Request):
    """Attach a presence to a Cove project (admin or existing work/admin member)."""
    try:
        from src.memory.database import get_db
        from src.tools.project_tools import _attach_presence_to_project
        body = await request.json()
        member = (body.get("member") or body.get("username") or body.get("presence_id") or "").strip()
        role = (body.get("role") or "work").strip().lower()
        if not member:
            return JSONResponse({"error": "member required"}, status_code=400)
        presence = await _get_presence_row(request)
        presence_id = presence["id"] if presence else None
        full_board = _full_cove_board(request, presence)
        async with get_db() as conn:
            if not await _owns_project(conn, project_id, presence_id, full_cove_board=full_board):
                return JSONResponse({"error": "Project not found"}, status_code=404)
            prow = await (await conn.execute(
                "SELECT id, name, presence_id FROM projects WHERE id = %s", (project_id,)
            )).fetchone()
            if not prow or prow.get("presence_id") is not None:
                return JSONResponse(
                    {"error": "Only Cove projects support members"}, status_code=400
                )
            r = await conn.execute(
                "SELECT id, username, display_name FROM accounts "
                "WHERE id::text = %s OR lower(username) = lower(%s) "
                "OR lower(display_name) = lower(%s) LIMIT 1",
                (member, member, member),
            )
            acc = await r.fetchone()
            if not acc:
                return JSONResponse({"error": "Presence not found"}, status_code=404)
        note = await _attach_presence_to_project(
            project_id, str(acc["id"]), role, prow.get("name") or ""
        )
        return {
            "ok": True,
            "presence_id": str(acc["id"]),
            "username": acc.get("username"),
            "role": role,
            "detail": note,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
