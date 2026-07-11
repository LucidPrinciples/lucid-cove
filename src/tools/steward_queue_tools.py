"""
Steward queue tools — how the steward runs the Cove-level execution queue.

Steward-unit spec Pillar 1. The operator's backlog board is INTAKE (a pre-sort
inbox, kept close to empty); this queue is EXECUTION, and the steward owns it.
Items arrive via the board's "→ Team" button (or the API); the steward lists,
takes, and updates them here. The operator stays the merge gate: a ticket is
`done` only after its PR is MERGED by the operator and deployed — "pushed a
branch" or even "PR open" is NOT done.

Status flow: queued → assigned → in_review → done (dropped from any state).
  queued     nobody owns it yet
  assigned   an agent owns it (set assignee when you take it)
  in_review  a PR exists and awaits operator review/merge — set pr_url
  done       operator merged + deployed (terminal)
  dropped    intentionally not doing it (terminal)

Tool ordering that works: queue_list → queue_take(id, your agent id) → build on
a branch → gated git_push → create_github_pr → queue_update(id, status
'in_review', pr_url) → operator merges → queue_update(id, 'done').
"""

import asyncio
import json
import logging

from langchain_core.tools import tool
from src.tools.approval import auto, notify

# Transition matrix — the ONE definition, shared with the dashboard route so
# the steward's tools and the operator's board can never disagree.
VALID_STATUSES = ("queued", "assigned", "in_review", "done", "dropped")
_TRANSITIONS = {
    "queued":    {"assigned", "in_review", "done", "dropped"},
    "assigned":  {"queued", "in_review", "done", "dropped"},
    "in_review": {"assigned", "done", "dropped"},
    "done":      set(),      # terminal
    "dropped":   set(),      # terminal
}


def can_transition(current: str, new: str) -> bool:
    """Is `current` → `new` a legal queue move? Pure — unit-testable.
    Same-state 'moves' are allowed (updating assignee/pr_url/notes only)."""
    if new == current:
        return True
    return new in _TRANSITIONS.get(current, set())


def _fmt_row(r) -> str:
    parts = [f"[{r['id']}] ({r['status']}) {r['title']}"]
    if r["source"]:
        parts.append(f"source={r['source']}")
    if r["assignee"]:
        parts.append(f"assignee={r['assignee']}")
    if r["pr_url"]:
        parts.append(f"pr={r['pr_url']}")
    return " · ".join(parts)


@auto
@tool
async def queue_list(include_closed: bool = False) -> str:
    """List the steward execution queue (Cove-level tickets the team owns).

    Args:
        include_closed: also show recent done/dropped items (default: open only)
    """
    from src.memory.database import get_db
    async with get_db() as conn:
        if include_closed:
            r = await conn.execute(
                "SELECT id, source, title, status, assignee, pr_url FROM steward_queue "
                "ORDER BY (status IN ('done','dropped')), updated_at DESC LIMIT 50")
        else:
            r = await conn.execute(
                "SELECT id, source, title, status, assignee, pr_url FROM steward_queue "
                "WHERE status NOT IN ('done','dropped') ORDER BY created_at LIMIT 50")
        rows = await r.fetchall()
    if not rows:
        return "Queue is empty — nothing assigned to the team right now."
    return "\n".join(_fmt_row(r) for r in rows)


@notify
@tool
async def queue_take(item_id: int, assignee: str) -> str:
    """Take a queued ticket: set it to `assigned` with an owner.

    Args:
        item_id: the queue item id (from queue_list)
        assignee: who owns it — your agent id, or a team agent you're assigning
    """
    return await _update(item_id, status="assigned", assignee=assignee)


@notify
@tool
async def queue_update(item_id: int, status: str = "", pr_url: str = "",
                       note: str = "") -> str:
    """Update a queue ticket — move status, attach the PR, or add a note.

    Set status 'in_review' WITH pr_url once the PR exists. Set 'done' ONLY
    after the operator has merged the PR and it is deployed — a pushed branch
    or an open PR is not done.

    Args:
        item_id: the queue item id (from queue_list)
        status: new status (queued/assigned/in_review/done/dropped) — optional
        pr_url: the PR link once one exists — optional
        note: append a short progress note — optional
    """
    return await _update(item_id, status=status, pr_url=pr_url, note=note)


async def _sync_board_on_done(source: str, pr_url: str):
    """When a queue item reaches done, sync the board: move to COMPLETED,
    mark done, add PR ref. Fire-and-forget: failures are logged, not fatal."""
    if not source.startswith("board:"):
        return  # only board-sourced items sync back
    ticket = source[6:]  # strip 'board:' prefix
    try:
        from src.tools.backlog_tools import _board_get, _board_put
        from src.tools.backlog_tools import move_ticket_lane, mark_ticket_done, annotate_ticket
        text, label = await _board_get()
        # Move to COMPLETED lane
        text, msg1 = move_ticket_lane(text, ticket, "completed")
        # Mark checkbox done
        text, msg2 = mark_ticket_done(text, ticket)
        # Annotate with PR ref
        if pr_url:
            text, msg3 = annotate_ticket(text, ticket, f"merged {pr_url}")
        else:
            msg3 = ""
        await _board_put(text)
    except Exception as e:
        # Log but don't fail the queue update — board sync is best-effort
        logging.getLogger(__name__).warning(
            f"Board sync failed for {ticket}: {e}")


async def _update(item_id: int, status: str = "", assignee: str = "",
                  pr_url: str = "", note: str = "") -> str:
    from src.memory.database import get_db
    status = (status or "").strip().lower()
    if status and status not in VALID_STATUSES:
        return f"Invalid status '{status}'. Valid: {', '.join(VALID_STATUSES)}"
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT id, status, notes, source, pr_url FROM steward_queue WHERE id = %s", (item_id,))
        row = await r.fetchone()
        if not row:
            return f"No queue item with id {item_id} — run queue_list first."
        if status and not can_transition(row["status"], status):
            return (f"Illegal move: {row['status']} → {status}. "
                    f"Items in '{row['status']}' can move to: "
                    f"{', '.join(sorted(_TRANSITIONS[row['status']])) or 'nothing (terminal)'}.")
        sets, args = ["updated_at = NOW()"], []
        if status:
            sets.append("status = %s"); args.append(status)
            if status == "done":
                sets.append("done_at = NOW()")
        if assignee:
            sets.append("assignee = %s"); args.append(assignee)
        if pr_url:
            sets.append("pr_url = %s"); args.append(pr_url)
        if note:
            new_notes = ((row["notes"] or "") + ("\n" if row["notes"] else "") + note)[-2000:]
            sets.append("notes = %s"); args.append(new_notes)
        args.append(item_id)
        await conn.execute(
            f"UPDATE steward_queue SET {', '.join(sets)} WHERE id = %s", tuple(args))
        r = await conn.execute(
            "SELECT id, source, title, status, assignee, pr_url FROM steward_queue WHERE id = %s",
            (item_id,))
        updated = await r.fetchone()
    # Fire board sync when reaching done (best-effort, non-blocking)
    if status == "done":
        asyncio.create_task(_sync_board_on_done(row["source"], row["pr_url"] or pr_url))
    return f"Updated: {_fmt_row(updated)}"


ALL_STEWARD_QUEUE_TOOLS = [queue_list, queue_take, queue_update]
TOOLS = ALL_STEWARD_QUEUE_TOOLS  # channel loader entry point (_load_tools)
