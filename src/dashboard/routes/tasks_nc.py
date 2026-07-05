"""
Tasks routes — personal task list with Nextcloud Tasks sync.

Two sources of truth are reconciled:
  1. Local postgres tasks table (offline-capable, Atlas-created tasks)
  2. Nextcloud Tasks app (CalDAV VTODO — synced to Pixel via DAVx5)

Sync strategy: Nextcloud is the authoritative source for tasks created
on the Pixel. Local DB is the authoritative source for tasks created by
Atlas or through this dashboard.
"""

import os
from src.env import env
from datetime import datetime, timezone
from fastapi import APIRouter
from src.memory.database import get_db
from src.utils.time_utils import ts_log

router = APIRouter()

NC_URL = env("NEXTCLOUD_URL")
NC_USER = env("NEXTCLOUD_USER")
NC_PASS = env("NEXTCLOUD_PASSWORD")


def _caldav_client():
    import caldav
    return caldav.DAVClient(
        url=f"{NC_URL}/remote.php/dav/",
        username=NC_USER,
        password=NC_PASS,
    )


async def _fetch_nc_tasks():
    """Fetch VTODO items (tasks) from Nextcloud via CalDAV."""
    try:
        client = _caldav_client()
        principal = client.principal()
        # Nextcloud Tasks app uses a calendar named "Tasks" or similar
        calendars = principal.calendars()
        tasks = []
        for cal in calendars:
            try:
                todos = cal.todos()
                for todo in todos:
                    try:
                        comp = todo.vobject_instance.vtodo
                        summary = str(getattr(comp, 'summary', None) and comp.summary.value or "Untitled")
                        status = str(getattr(comp, 'status', None) and comp.status.value or "NEEDS-ACTION")
                        due = getattr(comp, 'due', None)
                        due_str = due.value.isoformat() if due and hasattr(due.value, 'isoformat') else None
                        priority = getattr(comp, 'priority', None)
                        prio_val = int(priority.value) if priority else 5

                        # Map iCal priority (1=high, 5=medium, 9=low) to our scale
                        if prio_val <= 2:
                            prio = "high"
                        elif prio_val >= 8:
                            prio = "low"
                        else:
                            prio = "normal"

                        tasks.append({
                            "id": todo.id,
                            "source": "nextcloud",
                            "calendar": cal.name,
                            "title": summary,
                            "status": "done" if status == "COMPLETED" else "pending",
                            "priority": prio,
                            "due_date": due_str,
                            "nc_task_id": todo.id,
                        })
                    except Exception:
                        continue
            except Exception:
                continue
        return tasks
    except Exception as e:
        print(f"{ts_log()} [tasks] Nextcloud fetch failed: {e}")
        return []


@router.get("/api/tasks/nextcloud")
async def get_nc_tasks(status: str = "all"):
    """Get tasks from Nextcloud only — for sync/merge operations."""
    nc_tasks = await _fetch_nc_tasks()
    if status != "all":
        nc_tasks = [t for t in nc_tasks if t["status"] == status]
    return {"tasks": nc_tasks, "count": len(nc_tasks)}


@router.post("/api/tasks/sync-nc")
async def sync_nc_tasks():
    """Pull Nextcloud tasks and merge into local DB (dedup by nc_task_id)."""
    try:
        nc_tasks = await _fetch_nc_tasks()
        if not nc_tasks:
            return {"success": True, "synced": 0, "message": "No Nextcloud tasks found"}

        synced = 0
        async with get_db() as conn:
            for t in nc_tasks:
                nc_id = t.get("nc_task_id")
                if not nc_id:
                    continue
                # Check if already exists locally
                result = await conn.execute(
                    "SELECT id FROM tasks WHERE nc_task_id = $1", nc_id
                )
                existing = await result.fetchone()
                if not existing:
                    await conn.execute(
                        """INSERT INTO tasks (title, status, priority, due_date, source, nc_task_id)
                           VALUES ($1, $2, $3, $4, 'nextcloud', $5)""",
                        t.get("title", "Untitled"),
                        t.get("status", "pending"),
                        t.get("priority", "normal"),
                        t.get("due_date"),
                        nc_id,
                    )
                    synced += 1
            await conn.commit()
        return {"success": True, "synced": synced, "total_nc": len(nc_tasks)}
    except Exception as e:
        return {"success": False, "error": str(e)}
