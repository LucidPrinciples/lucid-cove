"""
Project Management Tools — task CRUD, project tracking, work logging.

All read operations are AUTO tier. Task/project creation is NOTIFY.
Task deletion is APPROVE.

All data is stored in PostgreSQL (same DB the dashboard reads from).
"""

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
# Task Operations
# =============================================================================

@auto
@tool
async def get_tasks(status: str = "", assignee: str = "", project: str = "") -> str:
    """List tasks with optional filters.

    Args:
        status: Filter by status: pending, in_progress, done, blocked (optional)
        assignee: Filter by assignee (optional)
        project: Filter by project slug (optional)
    """
    try:
        conditions = ["1=1"]
        params = []

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
async def create_task(title: str, assignee: str = "stuart",
                      description: str = "", project: str = "",
                      priority: str = "normal",
                      source: str = "internal",
                      expected_by: str = "") -> str:
    """Create a new task.

    Args:
        title: Task title (clear and specific)
        assignee: Who does the work (default: stuart)
        description: What needs to be done
        project: Project slug to attach to (optional)
        priority: low, normal, high, urgent
        source: Where this task came from — 'operator', 'agent', 'scheduled', or 'internal'
        expected_by: When this should be done (ISO datetime string, e.g. '2026-05-11T17:00:00'). Empty = no deadline.
    """
    try:
        async with get_db() as conn:
            # Look up project_id from slug if provided
            project_id = None
            if project:
                result = await conn.execute(
                    "SELECT id FROM projects WHERE slug = %s", (project,)
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
                    from datetime import datetime, timezone
                    from dateutil.parser import parse as parse_dt
                    expected_by_val = parse_dt(expected_by)
                    if expected_by_val.tzinfo is None:
                        # C2: config cascade, not the legacy-only env stamp.
                        from src.utils.time_utils import app_tz
                        expected_by_val = expected_by_val.replace(tzinfo=app_tz())
                except Exception:
                    pass  # If parsing fails, skip the deadline

            result = await conn.execute(
                """INSERT INTO tasks (project_id, title, description, priority, assignee, created_by, source, expected_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, title, description, priority, assignee, "stuart", source, expected_by_val),
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
                      priority: str = "", assignee: str = "") -> str:
    """Update a task's status, notes, priority, or assignee.

    Args:
        task_id: Task ID to update
        status: New status: pending, in_progress, done, blocked, review
        notes: Progress notes
        priority: New priority
        assignee: Reassign to someone
    """
    try:
        set_parts = []
        values = []
        updates = []

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

        if not set_parts:
            return "No fields to update. Provide status, notes, priority, or assignee."

        set_parts.append("updated_at = NOW()")
        values.append(task_id)

        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s RETURNING id, title",
                tuple(values),
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
        async with get_db() as conn:
            result = await conn.execute(
                "DELETE FROM tasks WHERE id = %s RETURNING id, title", (task_id,)
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
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT p.*,
                          COUNT(t.id) FILTER (WHERE t.status NOT IN ('done', 'cancelled')) as open_tasks,
                          COUNT(t.id) as total_tasks
                   FROM projects p
                   LEFT JOIN tasks t ON t.project_id = p.id
                   WHERE p.status NOT IN ('archived', 'cancelled')
                   GROUP BY p.id
                   ORDER BY p.created_at"""
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

    Args:
        name: Human-readable project name
        description: What this project is about
        goals: What success looks like
    """
    try:
        slug = _slugify(name)
        async with get_db() as conn:
            # Check for duplicate
            result = await conn.execute(
                "SELECT id FROM projects WHERE slug = %s", (slug,)
            )
            if await result.fetchone():
                return f"Project '{slug}' already exists."

            result = await conn.execute(
                """INSERT INTO projects (slug, name, description, owner, goals)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, slug""",
                (slug, name, description, _get_operator_id(), goals),
            )
            row = await result.fetchone()
        return f"Project created: {row['slug']} (ID: {row['id']}) — {name}"
    except Exception as e:
        return f"Error creating project: {e}"


# =============================================================================
# Work Logging
# =============================================================================

@notify
@tool
async def log_work(summary: str, project: str = "", task_id: int = 0,
                   details: str = "") -> str:
    """Log a work entry. Builds a running history of what Stuart has done.

    Args:
        summary: One-line summary of what was done
        project: Project slug (optional)
        task_id: Related task ID (optional)
        details: Longer description (optional)
    """
    try:
        async with get_db() as conn:
            # Find project_id from slug if provided
            project_id = None
            if project:
                result = await conn.execute(
                    "SELECT id FROM projects WHERE slug = %s", (project,)
                )
                row = await result.fetchone()
                if row:
                    project_id = row["id"]

            # Add as a project comment if we have a project
            if project_id:
                await conn.execute(
                    """INSERT INTO project_comments (project_id, author, content)
                       VALUES (%s, %s, %s)""",
                    (project_id, "stuart", f"[WORK LOG] {summary}" + (f"\n{details}" if details else "")),
                )

            # If task_id provided, update its notes
            if task_id:
                await conn.execute(
                    """UPDATE tasks SET notes = COALESCE(notes, '') || %s, updated_at = NOW()
                       WHERE id = %s""",
                    (f"\n[{_now()[:16]}] {summary}", task_id),
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
        async with get_db() as conn:
            if project:
                result = await conn.execute(
                    """SELECT pc.*, p.slug as project_slug
                       FROM project_comments pc
                       JOIN projects p ON pc.project_id = p.id
                       WHERE pc.author = 'stuart'
                         AND pc.content LIKE '[WORK LOG]%%'
                         AND p.slug = %s
                         AND pc.created_at > NOW() - INTERVAL '%s days'
                       ORDER BY pc.created_at DESC LIMIT 50""",
                    (project, days),
                )
            else:
                result = await conn.execute(
                    """SELECT pc.*, p.slug as project_slug
                       FROM project_comments pc
                       JOIN projects p ON pc.project_id = p.id
                       WHERE pc.author = 'stuart'
                         AND pc.content LIKE '[WORK LOG]%%'
                         AND pc.created_at > NOW() - INTERVAL '%s days'
                       ORDER BY pc.created_at DESC LIMIT 50""",
                    (days,),
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
    get_projects, create_project,
    log_work, get_work_log,
    run_workflow,
]
TOOLS = ALL_PROJECT_TOOLS  # alias for cove-core channels.py loader
