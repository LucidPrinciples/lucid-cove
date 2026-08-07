"""
Project Management Tools — task CRUD, project tracking, work logging.

All read operations are AUTO tier. Task/project creation is NOTIFY.
Task deletion is APPROVE.

All data is stored in PostgreSQL (same DB the dashboard reads from).

#PRJ1 — Presence isolation (JF4 recipe)
--------------------------------------
Two systems share the projects/tasks tables:
  - Cove board: presence_id IS NULL (Stuart/Mercer + build team)
  - Presence personal: presence_id = acting presence

Dashboard routes already split on cookie presence. The agent tool must too,
or presence-created rows land NULL (invisible on the presence Projects view
and leak into the Cove board). Bound request-scoped in chat.py alongside
links + quicklist.
"""

import contextvars as _ctxvars
import json
import re
from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import tool
from src.utils.settings import get_setting_sync


def _get_operator_id() -> str:
    """Get operator_id from settings (cached, sync-safe)."""
    return get_setting_sync("operator_id", "operator")

from src.tools.approval import auto, notify, approve
from src.memory.database import get_db
from src.env import env


def _slugify(name: str) -> str:
    """Convert project name to URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s[:60]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Presence scope
# =============================================================================

_prj_presence_ctx = _ctxvars.ContextVar("prj_presence", default=None)
_prj_agent_ctx = _ctxvars.ContextVar("prj_agent", default=None)
# Human logged into MC during a manager (Stuart/Mercer) turn — for auto-attach.
_prj_viewer_ctx = _ctxvars.ContextVar("prj_viewer", default=None)


def set_request_project_presence(presence_id: str, agent_id: str = ""):
    """Bind acting presence (+ optional personal agent id) for this request/task.

    Returns a pair of reset tokens (presence_token, agent_token).
    """
    ptok = _prj_presence_ctx.set(str(presence_id) if presence_id else None)
    atok = _prj_agent_ctx.set(str(agent_id) if agent_id else None)
    return ptok, atok


def set_request_project_viewer(viewer_presence_id: str):
    """Bind the human session on a manager channel (Cove board stays unbound).

    Returns a single reset token for _prj_viewer_ctx.
    """
    return _prj_viewer_ctx.set(str(viewer_presence_id) if viewer_presence_id else None)


def clear_request_project_presence(tokens) -> None:
    """Reset tokens from set_request_project_presence / set_request_project_viewer."""
    if tokens is None:
        return
    try:
        if isinstance(tokens, tuple):
            ptok = tokens[0] if len(tokens) > 0 else None
            atok = tokens[1] if len(tokens) > 1 else None
            if ptok is not None:
                try:
                    _prj_presence_ctx.reset(ptok)
                except Exception:
                    pass
            if atok is not None:
                try:
                    _prj_agent_ctx.reset(atok)
                except Exception:
                    pass
        else:
            try:
                _prj_viewer_ctx.reset(tokens)
            except Exception:
                try:
                    _prj_presence_ctx.reset(tokens)
                except Exception:
                    pass
    except Exception:
        pass


def _prj_scope(col: str = "presence_id"):
    """(sql_fragment, params) scoping rows to the acting presence (or NULL = Cove)."""
    pid = _prj_presence_ctx.get()
    if pid:
        return f"{col} = %s", (pid,)
    return f"{col} IS NULL", ()


def _acting_presence_id():
    return _prj_presence_ctx.get()


def _viewer_presence_id():
    return _prj_viewer_ctx.get()


def _default_agent_name() -> str:
    """Assignee/created_by default: presence personal agent when scoped, else stuart."""
    aid = _prj_agent_ctx.get()
    if aid:
        return str(aid)
    pid = _prj_presence_ctx.get()
    if pid:
        return str(pid)
    return "stuart"


def _safe_project_folder_name(display_name: str) -> str:
    name = (display_name or "").strip().strip("/")
    if not name or "/" in name or chr(92) in name or name in (".", ".."):
        return ""
    return name


async def _ensure_project_nc_folder(display_name: str) -> str:
    """Create Projects/{display_name}/ on the bound NC space (best-effort)."""
    name = _safe_project_folder_name(display_name)
    if not name:
        return ""
    try:
        import httpx
        from src.tools import nextcloud_tools as nc
    except Exception:
        return ""
    folder = f"Projects/{name}"
    try:
        denied = nc.check_nc_path_access(folder, write=True)
        if denied:
            return f" (NC folder skipped: path not writable for acting role)"
        async with httpx.AsyncClient(auth=nc._auth(), timeout=15) as client:
            for part in ("Projects", folder):
                url = nc._webdav_url(part)
                resp = await client.request("MKCOL", url)
                if resp.status_code not in (200, 201, 405, 301, 302):
                    return f" (NC folder {part!r}: HTTP {resp.status_code})"
        return f" — folder {folder}/ ready"
    except Exception as e:
        return f" (NC folder warn: {e})"


async def _share_project_folder_with_presence(display_name: str, presence_id: str) -> str:
    """OCS-share admin Projects/{name}/ to a presence NC user (RW). Best-effort."""
    name = _safe_project_folder_name(display_name)
    if not name or not presence_id:
        return ""
    try:
        import httpx
        from src.env import env
        from src.memory.database import get_db
        from src.dashboard.routes import nextcloud as ncr
    except Exception as e:
        return f" share-skip ({e})"

    async with get_db() as conn:
        r = await conn.execute(
            "SELECT nc_username, nc_password FROM accounts WHERE id = %s",
            (presence_id,),
        )
        row = await r.fetchone()
    if not row or not row.get("nc_username"):
        return " (no NC user for member — share skipped)"

    folder = f"Projects/{name}"
    admin_user = getattr(ncr, "NC_ADMIN_USER", None) or env("NEXTCLOUD_ADMIN_USER", "admin")
    admin_pass = getattr(ncr, "NC_ADMIN_PASSWORD", None) or env("NEXTCLOUD_ADMIN_PASSWORD", "")
    nc_url = env("NEXTCLOUD_URL") or getattr(ncr, "NC_URL", "")
    if not admin_pass or not nc_url:
        return " (admin NC not configured — share skipped)"

    from urllib.parse import quote
    dav = f"{nc_url}/remote.php/dav/files/{admin_user}"
    share_url = f"{nc_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for part in ("Projects", folder):
                await client.request(
                    "MKCOL",
                    f"{dav}/{quote(part, safe='/')}",
                    auth=(admin_user, admin_pass),
                )
            resp = await client.post(
                share_url,
                auth=(admin_user, admin_pass),
                headers={"OCS-APIRequest": "true"},
                data={
                    "path": f"/{folder}",
                    "shareType": 0,
                    "shareWith": row["nc_username"],
                    "permissions": 31,
                },
            )
            ocs = -1
            if resp.status_code == 200:
                try:
                    ocs = ncr._parse_ocs_status(resp.text)
                except Exception:
                    ocs = 100 if "ok" in (resp.text or "").lower() else -1
            if ocs in (100, 200, 403):
                return f" — shared {folder}/ with member NC"
            return f" (share HTTP {resp.status_code} OCS {ocs})"
    except Exception as e:
        return f" (share warn: {e})"


async def _attach_presence_to_project(
    project_id: int, presence_id: str, role: str = "work", display_name: str = ""
) -> str:
    """Insert project_members row + NC share. Returns short status."""
    role = (role or "work").strip().lower()
    if role not in ("view", "work", "admin"):
        role = "work"
    try:
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO project_members (project_id, presence_id, role)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (project_id, presence_id)
                   DO UPDATE SET role = EXCLUDED.role""",
                (project_id, presence_id, role),
            )
            if not display_name:
                r = await conn.execute(
                    "SELECT name FROM projects WHERE id = %s", (project_id,)
                )
                prow = await r.fetchone()
                display_name = (prow or {}).get("name") or ""
    except Exception as e:
        return f"attach DB error: {e}"
    share_note = await _share_project_folder_with_presence(display_name, presence_id)
    return f"attached{share_note}"


# =============================================================================
# Task Operations
# =============================================================================

@auto
@tool
async def get_tasks(status: str = "", assignee: str = "", project: str = "") -> str:
    """List tasks with optional filters.

    Args:
        status: Filter by status: pending, in_progress, done, blocked (optional)
        assignee: Filter by assignee (optional)
        project: Project slug to attach to (optional)
    """
    try:
        scope_sql, scope_params = _prj_scope("t.presence_id")
        conditions = [scope_sql]
        params = list(scope_params)

        if status:
            conditions.append("t.status = %s")
            params.append(status)
        if assignee:
            conditions.append("t.assignee = %s")
            params.append(assignee)
        if project:
            conditions.append("p.slug = %s")
            params.append(project)

        where = " AND ".join(conditions)

        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT t.*, p.slug as project_slug, p.name as project_name
                    FROM tasks t
                    LEFT JOIN projects p ON t.project_id = p.id
                    WHERE {where}
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
                      t.created_at DESC
                    LIMIT 50""",
                tuple(params),
            )
            tasks = await result.fetchall()

        if not tasks:
            return "No tasks found matching filters."

        lines = [f"{'ID':>4} | {'Status':<12} | {'Priority':<8} | {'Assignee':<12} | {'Project':<20} | Title"]
        lines.append("-" * 100)
        for t in tasks:
            proj = t.get("project_slug") or "(none)"
            lines.append(
                f"{t['id']:>4} | {t.get('status',''):<12} | {t.get('priority','normal'):<8} | "
                f"{t.get('assignee',''):<12} | {proj:<20} | {t.get('title','')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing tasks: {e}"


@notify
@tool
async def create_task(title: str, assignee: str = "",
                      description: str = "", project: str = "",
                      priority: str = "normal",
                      source: str = "internal",
                      expected_by: str = "") -> str:
    """Create a new task.

    Args:
        title: Task title (clear and specific)
        assignee: Who does the work (default: acting presence agent, or stuart on Cove board)
        description: What needs to be done
        project: Project slug to attach to (optional)
        priority: low, normal, high, urgent
        source: Where this task came from — 'operator', 'agent', 'scheduled', or 'internal'
        expected_by: When this should be done (ISO datetime string, e.g. '2026-05-11T17:00:00'). Empty = no deadline.
    """
    try:
        if not assignee:
            assignee = _default_agent_name()
        created_by = _default_agent_name()
        presence_id = _acting_presence_id()

        async with get_db() as conn:
            # Look up project_id from slug if provided — scoped so a presence
            # cannot attach tasks to another presence's (or the Cove) project.
            project_id = None
            if project:
                p_scope, p_params = _prj_scope("presence_id")
                result = await conn.execute(
                    f"SELECT id FROM projects WHERE slug = %s AND {p_scope}",
                    (project, *p_params),
                )
                row = await result.fetchone()
                if row:
                    project_id = row["id"]
                else:
                    return f"Project '{project}' not found. Create the project first."

            # Parse expected_by if provided
            expected_by_val = None
            if expected_by:
                try:
                    from dateutil.parser import parse as parse_dt
                    expected_by_val = parse_dt(expected_by)
                    if expected_by_val.tzinfo is None:
                        from src.utils.time_utils import app_tz
                        expected_by_val = expected_by_val.replace(tzinfo=app_tz())
                except Exception:
                    pass  # If parsing fails, skip the deadline

            result = await conn.execute(
                """INSERT INTO tasks (project_id, title, description, priority, assignee,
                                      created_by, source, expected_by, presence_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, title, description, priority, assignee,
                 created_by, source, expected_by_val, presence_id),
            )
            row = await result.fetchone()

        deadline_note = f" (due: {expected_by})" if expected_by else ""
        source_note = f" [source: {source}]" if source != "internal" else ""
        return f"Task #{row['id']} created: {title} [{priority}] → {assignee}{source_note}{deadline_note}"
    except Exception as e:
        return f"Error creating task: {e}"


@notify
@tool
async def update_task(task_id: int, status: str = "", notes: str = "",
                      priority: str = "", assignee: str = "",
                      title: str = "", expected_by: str = "") -> str:
    """Update a task's status, notes, priority, assignee, title, or due date.

    Args:
        task_id: Task ID to update
        status: New status: pending, in_progress, done, blocked, review
        notes: Progress notes
        priority: New priority
        assignee: Reassign to someone
        title: New title (rename the task)
        expected_by: New due datetime (ISO, e.g. '2026-07-19T19:00:00').
                     Pass 'clear' to remove the deadline.
    """
    try:
        set_parts = []
        values = []
        updates = []

        if title:
            clean_title = title.strip()
            if not clean_title:
                return "Title cannot be empty."
            set_parts.append("title = %s")
            values.append(clean_title[:500])
            updates.append(f"title={clean_title[:80]}")
        if status:
            set_parts.append("status = %s")
            values.append(status)
            updates.append(f"status={status}")
            if status == "done":
                set_parts.append("completed_at = NOW()")
            else:
                set_parts.append("completed_at = NULL")
        if notes:
            set_parts.append("notes = %s")
            values.append(notes)
            updates.append("notes updated")
        if priority:
            set_parts.append("priority = %s")
            values.append(priority)
            updates.append(f"priority={priority}")
        if assignee:
            set_parts.append("assignee = %s")
            values.append(assignee)
            updates.append(f"assignee={assignee}")
        if expected_by:
            # 'clear' / empty-after-strip removes the deadline; otherwise parse ISO/dateutil.
            raw = expected_by.strip()
            if raw.lower() in ("clear", "none", "null", "-"):
                set_parts.append("expected_by = NULL")
                updates.append("expected_by=cleared")
            else:
                try:
                    from dateutil.parser import parse as parse_dt
                    expected_by_val = parse_dt(raw)
                    if expected_by_val.tzinfo is None:
                        from src.utils.time_utils import app_tz
                        expected_by_val = expected_by_val.replace(tzinfo=app_tz())
                except Exception:
                    return (
                        f"Could not parse expected_by '{expected_by}'. "
                        "Use ISO like '2026-07-19T19:00:00' or 'clear' to remove."
                    )
                set_parts.append("expected_by = %s")
                values.append(expected_by_val)
                updates.append(f"expected_by={raw}")

        if not set_parts:
            return (
                "No fields to update. Provide title, status, notes, priority, "
                "assignee, or expected_by."
            )

        set_parts.append("updated_at = NOW()")
        scope_sql, scope_params = _prj_scope("presence_id")
        values.append(task_id)

        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE tasks SET {', '.join(set_parts)} "
                f"WHERE id = %s AND {scope_sql} RETURNING id, title",
                tuple(values) + tuple(scope_params),
            )
            row = await result.fetchone()

        if not row:
            return f"Task #{task_id} not found."
        return f"Task #{task_id} updated: {', '.join(updates)}"
    except Exception as e:
        return f"Error updating task: {e}"


@approve
@tool
async def delete_task(task_id: int) -> str:
    """Delete a task permanently. Requires approval.

    Args:
        task_id: Task ID to delete
    """
    try:
        scope_sql, scope_params = _prj_scope("presence_id")
        async with get_db() as conn:
            result = await conn.execute(
                f"DELETE FROM tasks WHERE id = %s AND {scope_sql} RETURNING id, title",
                (task_id, *scope_params),
            )
            row = await result.fetchone()
        if not row:
            return f"Task #{task_id} not found."
        return f"Task #{task_id} deleted: {row['title']}"
    except Exception as e:
        return f"Error deleting task: {e}"


# =============================================================================
# Project Operations
# =============================================================================

@auto
@tool
async def get_projects() -> str:
    """List all projects with their task counts."""
    try:
        pid = _acting_presence_id()
        if pid:
            where = (
                "(p.presence_id = %s OR (p.presence_id IS NULL AND EXISTS ("
                "SELECT 1 FROM project_members pm "
                "WHERE pm.project_id = p.id AND pm.presence_id = %s)))"
            )
            scope_params = (pid, pid)
        else:
            where = "p.presence_id IS NULL"
            scope_params = ()
        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT p.*,
                          COUNT(t.id) FILTER (WHERE t.status NOT IN ('done', 'cancelled')) as open_tasks,
                          COUNT(t.id) as total_tasks
                   FROM projects p
                   LEFT JOIN tasks t ON t.project_id = p.id
                   WHERE p.status NOT IN ('archived', 'cancelled')
                     AND {where}
                   GROUP BY p.id
                   ORDER BY p.created_at""",
                scope_params,
            )
            projects = await result.fetchall()

        if not projects:
            return "No projects found."

        lines = [f"{'ID':>4} | {'Slug':<20} | {'Status':<10} | {'Open':>5} | {'Total':>5} | Name"]
        lines.append("-" * 80)
        for p in projects:
            lines.append(
                f"{p['id']:>4} | {p['slug']:<20} | {p.get('status','active'):<10} | "
                f"{p.get('open_tasks', 0):>5} | {p.get('total_tasks', 0):>5} | {p.get('name','')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing projects: {e}"


@notify
@tool
async def create_project(name: str, description: str = "",
                         goals: str = "") -> str:
    """Create a new project.

    Manager channels (Stuart/Mercer): always a Cove project (presence_id NULL),
    folder on admin NC under Projects/{name}/. The human logged into MC is
    auto-attached (their Projects list + NC share). Personal agent channels:
    project stays that presence only.

    Args:
        name: Human-readable project name
        description: What this project is about
        goals: What success looks like
    """
    try:
        slug = _slugify(name)
        presence_id = _acting_presence_id()  # None on manager channels = Cove
        async with get_db() as conn:
            scope_sql, scope_params = _prj_scope("presence_id")
            result = await conn.execute(
                f"SELECT id FROM projects WHERE slug = %s AND {scope_sql}",
                (slug, *scope_params),
            )
            if await result.fetchone():
                return f"Project '{slug}' already exists."

            result = await conn.execute(
                """INSERT INTO projects (presence_id, slug, name, description, owner, goals)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, slug, name""",
                (presence_id, slug, name, description, _get_operator_id(), goals),
            )
            row = await result.fetchone()
        nc_note = await _ensure_project_nc_folder(name)
        attach_note = ""
        viewer = _viewer_presence_id()
        if presence_id is None and viewer:
            attach_note = " | " + await _attach_presence_to_project(
                int(row["id"]), str(viewer), "work", name
            )
        scope_label = "Cove" if presence_id is None else "personal"
        return (
            f"Project created ({scope_label}): {row['slug']} (ID: {row['id']}) — {name}"
            f"{nc_note}{attach_note}. "
            f"Operator-facing files: Projects/{name}/"
        )
    except Exception as e:
        return f"Error creating project: {e}"



@notify
@tool
async def update_project(
    project: str,
    name: str = "",
    description: str = "",
    status: str = "",
    goals: str = "",
) -> str:
    """Update a project's name, description, status, or goals.

    Args:
        project: Project slug or numeric ID.
        name: New display name (optional).
        description: New description (optional).
        status: New status — active, on_hold, archived, cancelled (optional).
        goals: New goals text (optional).
    """
    try:
        scope_sql, scope_params = _prj_scope("presence_id")
        updates = []
        values = []
        changed = []

        if name and name.strip():
            updates.append("name = %s")
            values.append(name.strip())
            changed.append(f"name='{name.strip()}'")
        if description != "":
            updates.append("description = %s")
            values.append(description)
            changed.append("description updated")
        if status and status.strip():
            st = status.strip().lower()
            allowed = {"active", "on_hold", "archived", "cancelled"}
            if st not in allowed:
                return f"Invalid status '{status}'. Use one of: {', '.join(sorted(allowed))}."
            updates.append("status = %s")
            values.append(st)
            changed.append(f"status={st}")
        if goals != "":
            updates.append("goals = %s")
            values.append(goals)
            changed.append("goals updated")

        if not updates:
            return "Nothing to update. Provide name, description, status, and/or goals."

        updates.append("updated_at = NOW()")

        async with get_db() as conn:
            # Resolve project by id or slug within scope
            proj_row = None
            try:
                pid = int(project)
                result = await conn.execute(
                    f"SELECT id, slug, name FROM projects WHERE id = %s AND {scope_sql}",
                    (pid, *scope_params),
                )
                proj_row = await result.fetchone()
            except (TypeError, ValueError):
                result = await conn.execute(
                    f"SELECT id, slug, name FROM projects WHERE slug = %s AND {scope_sql}",
                    (project, *scope_params),
                )
                proj_row = await result.fetchone()

            if not proj_row:
                return f"Project '{project}' not found."

            values.append(proj_row["id"])
            result = await conn.execute(
                f"UPDATE projects SET {', '.join(updates)} "
                f"WHERE id = %s AND {scope_sql} RETURNING id, slug, name, status",
                tuple(values) + tuple(scope_params),
            )
            row = await result.fetchone()

        if not row:
            return f"Project '{project}' not found."
        return (
            f"Project {row['slug']} (ID {row['id']}) updated: "
            + ", ".join(changed)
        )
    except Exception as e:
        return f"Error updating project: {e}"


@notify
@tool
async def archive_project(project: str) -> str:
    """Archive a project so it leaves the active projects list.

    Soft-archive via status='archived'. Tasks are left in place.

    Args:
        project: Project slug or numeric ID.
    """
    try:
        scope_sql, scope_params = _prj_scope("presence_id")
        async with get_db() as conn:
            proj_row = None
            try:
                pid = int(project)
                result = await conn.execute(
                    f"SELECT id, slug, name, status FROM projects WHERE id = %s AND {scope_sql}",
                    (pid, *scope_params),
                )
                proj_row = await result.fetchone()
            except (TypeError, ValueError):
                result = await conn.execute(
                    f"SELECT id, slug, name, status FROM projects WHERE slug = %s AND {scope_sql}",
                    (project, *scope_params),
                )
                proj_row = await result.fetchone()

            if not proj_row:
                return f"Project '{project}' not found."
            if (proj_row.get("status") or "") == "archived":
                return f"Project '{proj_row['slug']}' is already archived."

            result = await conn.execute(
                f"""UPDATE projects SET status = 'archived', updated_at = NOW()
                   WHERE id = %s AND {scope_sql}
                   RETURNING id, slug, name""",
                (proj_row["id"], *scope_params),
            )
            row = await result.fetchone()

        return (
            f"Archived project: {row['name']} ({row['slug']}, ID {row['id']}). "
            "It no longer appears in active projects."
        )
    except Exception as e:
        return f"Error archiving project: {e}"


# =============================================================================
# Work Logging
# =============================================================================

@notify
@tool
async def log_work(summary: str, project: str = "", task_id: int = 0,
                   details: str = "") -> str:
    """Log a work entry. Builds a running history of what the acting agent has done.

    Args:
        summary: One-line summary of what was done
        project: Project slug (optional)
        task_id: Related task ID (optional)
        details: Longer description (optional)
    """
    try:
        author = _default_agent_name()
        async with get_db() as conn:
            # Find project_id from slug if provided (scoped)
            project_id = None
            if project:
                scope_sql, scope_params = _prj_scope("presence_id")
                result = await conn.execute(
                    f"SELECT id FROM projects WHERE slug = %s AND {scope_sql}",
                    (project, *scope_params),
                )
                row = await result.fetchone()
                if row:
                    project_id = row["id"]

            # Add as a project comment if we have a project
            if project_id:
                await conn.execute(
                    """INSERT INTO project_comments (project_id, author, content)
                       VALUES (%s, %s, %s)""",
                    (project_id, author, f"[WORK LOG] {summary}" + (f"\n{details}" if details else "")),
                )

            # If task_id provided, update its notes (scoped)
            if task_id:
                scope_sql, scope_params = _prj_scope("presence_id")
                await conn.execute(
                    f"""UPDATE tasks SET notes = COALESCE(notes, '') || %s, updated_at = NOW()
                       WHERE id = %s AND {scope_sql}""",
                    (f"\n[{_now()[:16]}] {summary}", task_id, *scope_params),
                )

        return f"Logged: {summary}"
    except Exception as e:
        return f"Error logging work: {e}"


@auto
@tool
async def get_work_log(days: int = 7, project: str = "") -> str:
    """Get recent work log entries.

    Args:
        days: How many days back to look (default 7)
        project: Filter by project slug (optional)
    """
    try:
        author = _default_agent_name()
        scope_sql, scope_params = _prj_scope("p.presence_id")
        async with get_db() as conn:
            if project:
                result = await conn.execute(
                    f"""SELECT pc.*, p.slug as project_slug
                       FROM project_comments pc
                       JOIN projects p ON pc.project_id = p.id
                       WHERE pc.author = %s
                         AND pc.content LIKE '[WORK LOG]%%'
                         AND p.slug = %s
                         AND {scope_sql}
                         AND pc.created_at > NOW() - make_interval(days => %s)
                       ORDER BY pc.created_at DESC LIMIT 50""",
                    (author, project, *scope_params, days),
                )
            else:
                result = await conn.execute(
                    f"""SELECT pc.*, p.slug as project_slug
                       FROM project_comments pc
                       JOIN projects p ON pc.project_id = p.id
                       WHERE pc.author = %s
                         AND pc.content LIKE '[WORK LOG]%%'
                         AND {scope_sql}
                         AND pc.created_at > NOW() - make_interval(days => %s)
                       ORDER BY pc.created_at DESC LIMIT 50""",
                    (author, *scope_params, days),
                )
            entries = await result.fetchall()

        if not entries:
            return "No work log entries found."

        lines = ["WORK LOG:"]
        for e in entries:
            ts = str(e["created_at"])[:16] if e.get("created_at") else "?"
            proj = f"[{e.get('project_slug', '?')}] " if e.get("project_slug") else ""
            content = e.get("content", "").replace("[WORK LOG] ", "", 1)
            lines.append(f"  {ts} — {proj}{content}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading work log: {e}"


# =============================================================================
# Workflow Execution
# =============================================================================

@notify
@tool
async def run_workflow(task_id: int, pattern: str = "") -> str:
    """Execute a multi-agent workflow for a task.

    Runs the task through its assigned workflow pattern (build, report, research,
    comms, knowledge, commerce). Each step is executed by the appropriate team
    agent, with Vera auditing where required.

    Args:
        task_id: The task to execute through the workflow
        pattern: Workflow pattern to use. If empty, reads from the task's workflow_pattern field.
                 Options: build, report, research, comms, knowledge, commerce
    """
    try:
        # Presence agents do not drive Cove multi-agent workflows.
        if _acting_presence_id():
            return (
                "run_workflow is a Cove-team tool (Stuart/build team). "
                "Personal presence tasks stay on the presence Projects board."
            )
        from src.graphs.task_graph import start_workflow
        result = await start_workflow(task_id, pattern=pattern if pattern else None)
        return result
    except Exception as e:
        return f"Error running workflow: {e}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_PROJECT_TOOLS = [
    get_tasks, create_task, update_task, delete_task,
    get_projects, create_project, update_project, archive_project,
    log_work, get_work_log,
    run_workflow,
]
TOOLS = ALL_PROJECT_TOOLS  # alias for cove-core channels.py loader


@notify
@tool
async def attach_project_member(
    project: str,
    member: str,
    role: str = "work",
) -> str:
    """Attach a Cove presence to a Cove (manager) project so their MC and NC see it.

    Only valid for Cove projects (presence_id NULL). Personal projects stay private.

    Args:
        project: Project slug or numeric id
        member: Presence username/handle or account UUID
        role: view | work | admin (default work)
    """
    try:
        async with get_db() as conn:
            proj = None
            try:
                pid = int(project)
                r = await conn.execute(
                    "SELECT id, name, presence_id FROM projects WHERE id = %s",
                    (pid,),
                )
                proj = await r.fetchone()
            except (TypeError, ValueError):
                r = await conn.execute(
                    "SELECT id, name, presence_id FROM projects WHERE slug = %s "
                    "AND presence_id IS NULL",
                    (project,),
                )
                proj = await r.fetchone()
                if not proj:
                    r = await conn.execute(
                        "SELECT id, name, presence_id FROM projects WHERE slug = %s",
                        (project,),
                    )
                    proj = await r.fetchone()
            if not proj:
                return f"Project '{project}' not found."
            if proj.get("presence_id") is not None:
                return (
                    "That project is personal to one presence — attach is only for "
                    "Cove projects (created with Stuart or Mercer)."
                )
            m = (member or "").strip()
            r = await conn.execute(
                "SELECT id, username, display_name FROM accounts "
                "WHERE id::text = %s OR lower(username) = lower(%s) "
                "OR lower(display_name) = lower(%s) LIMIT 1",
                (m, m, m),
            )
            acc = await r.fetchone()
            if not acc:
                return f"No presence matched '{member}'."
        note = await _attach_presence_to_project(
            int(proj["id"]), str(acc["id"]), role, proj.get("name") or ""
        )
        label = acc.get("username") or acc.get("display_name") or member
        return f"Member {label} on project {proj['name']}: {note}"
    except Exception as e:
        return f"Error attaching member: {e}"
