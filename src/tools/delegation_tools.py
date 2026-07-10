"""
Delegation — the steward hands work to a team agent (steward-unit Pillar 2).

The 07-09 dry-run proved the steward's JUDGMENT (Stuart picked the right ticket
and wrote a solid brief for Archimedes unprompted); this is the missing
MECHANISM. delegate_task:

  1. creates a tracked task assigned to the team agent (the task system),
  2. links the steward-queue ticket (status → assigned) when a ref is given,
  3. delivers the brief INTO THE AGENT'S OWN day channel as a working turn, and
  4. starts that agent's turn in the background (fire-and-forget with a
     timeout) — delegation begins work; it doesn't wait for a human poke.

Boundaries (locked 2026-07-10): the operator keeps ALL GitHub-facing approvals
— a delegated agent's push/PR still lands as cards in the operator's MC. The
steward reviews the branch (git_diff_branch) before the PR ask; the operator
stays the merge gate. Steward-only tool (lives in the steward toolset).
"""

import asyncio
import logging

from langchain_core.tools import tool
from src.tools.approval import notify
from src.utils.time_utils import ts_log

log = logging.getLogger("delegation")


def _say(msg: str) -> None:
    """Docker-visible progress line (the app's loggers filter INFO; print is
    the house style — see scheduler.py)."""
    print(f"{ts_log()} [delegation] {msg}")

DELEGATION_TURN_TIMEOUT = 900  # seconds — a background turn gets 15 minutes
DELEGATION_RECURSION_LIMIT = 100  # graph super-steps (~50 tool rounds); the
                                  # wall-clock timeout is the real runaway bound


def resolve_agent(name: str, known: set[str]) -> str | None:
    """Match a steward-supplied agent name to an agents-config key. Pure.

    Accepts exact keys ('archimedes'), registry-style suffixed names
    ('archimedes-clearfield'), or dotted handles ('archimedes.clearfield')."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if n in known:
        return n
    for sep in ("-", "."):
        base = n.split(sep)[0]
        if base in known:
            return base
    return None


def compose_brief(agent: str, brief: str, ticket_ref: str, task_id: int) -> str:
    """The delegation message the target agent receives. Pure."""
    ref = f" ({ticket_ref})" if ticket_ref else ""
    return (
        f"[DELEGATION from the steward]{ref}\n\n{brief}\n\n"
        f"Ground rules: work this in your own channel on a branch named "
        f"{agent}/... — branch → commit → git_push (gated) → create_github_pr "
        f"(gated). The operator approves pushes and PRs and is the merge gate; "
        f"a pushed branch is NOT done. Track progress on task #{task_id} with "
        f"update_task (note the branch and, once it exists, the PR number). "
        f"If the work turns out to exceed your scope, say so on the task "
        f"instead of guessing."
    )


async def _run_agent_turn(channel: str, message: str, agent_label: str,
                          task_id: int = 0) -> None:
    """Background: deliver the brief as a human turn in the agent's channel and
    run the agent's graph once, then REPORT BACK — the agent's reply lands in
    the task notes and in the steward's channel (spec Pillar 2 completion:
    the steward monitors with eyes, not guesses). Best-effort — on any failure
    the brief is still queued as a task and the channel history explains."""
    try:
        from langchain_core.messages import HumanMessage
        from datetime import datetime, timezone
        from src.memory.checkpointer import get_checkpointer
        from src.memory.database import get_db
        from src.graphs.channels import get_channel_graph
        from src.memory.threads import create_thread

        # Prefer the channel's existing active thread (the one the operator's
        # MC tab shows); create one only if the channel has never been opened.
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT thread_id, agent_id FROM chat_threads "
                "WHERE channel = %s AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1", (channel,))
            row = await r.fetchone()
        if row:
            thread_id, agent_id = row["thread_id"], row["agent_id"]
        else:
            created = await create_thread(channel, title=f"Delegated work — {agent_label}")
            thread_id = created["thread_id"]
            async with get_db() as conn:
                r = await conn.execute(
                    "SELECT agent_id FROM chat_threads WHERE thread_id = %s",
                    (thread_id,))
                row2 = await r.fetchone()
            agent_id = row2["agent_id"] if row2 else None

        now_iso = datetime.now(timezone.utc).isoformat()
        graph_input = {
            "messages": [HumanMessage(content=message,
                                      additional_kwargs={"created_at": now_iso,
                                                         "input_mode": "text"})],
            "agent_id": agent_id,
            "channel": channel,
            "input_mode": "text",
        }
        # #D30: mark the task in_progress so a restart-orphaned turn is detectable.
        # With done/blocked set on every normal exit below, 'in_progress' means exactly
        # "running now, or crashed mid-turn" — the boot sweep + watcher key off that.
        await _set_task_status(task_id, "in_progress")
        _say(f"{agent_label} starting delegated turn on {channel} (thread {thread_id})")
        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(channel, checkpointer)
            # A delegated dev turn legitimately runs MANY agent->tool rounds
            # (investigate, read, edit, commit). LangGraph's default
            # recursion_limit of 25 super-steps guillotined Archimedes' first
            # clean run mid-work ("Recursion limit of 25 reached", 2026-07-10)
            # — there was no loop, just real work hitting a default ceiling.
            # The wall-clock timeout stays the true bound on a runaway turn.
            cfg = {"configurable": {"thread_id": thread_id},
                   "recursion_limit": DELEGATION_RECURSION_LIMIT}
            result = await asyncio.wait_for(graph.ainvoke(graph_input, config=cfg),
                                            timeout=DELEGATION_TURN_TIMEOUT)

        # Extract the agent's final reply for the report-back.
        reply = ""
        try:
            msgs = (result or {}).get("messages") or []
            for m in reversed(msgs):
                if getattr(m, "type", "") == "ai" and (getattr(m, "content", "") or "").strip():
                    reply = m.content.strip()
                    break
        except Exception:
            pass

        # Keep the thread's stats honest (message_count feeds the MC thread list).
        try:
            from src.memory.threads import update_thread_stats
            msgs = (result or {}).get("messages") or []
            await update_thread_stats(thread_id, message_count=len(msgs))
        except Exception:
            pass

        await _set_task_status(task_id, "done")   # #D30: turn finished cleanly
        await _report_back(agent_label, task_id, reply)
        _say(f"{agent_label} completed a delegated turn on {channel} "
             f"({len(reply)} chars reply, task #{task_id} noted)")
    except asyncio.TimeoutError:
        await _set_task_status(task_id, "blocked")  # #D30: didn't finish → terminal, not in_progress
        _say(f"{agent_label}'s turn TIMED OUT on {channel} — brief is in the "
             f"channel; it resumes on the agent's next turn")
        await _report_back(agent_label, task_id,
                           "(turn timed out — brief delivered, work resumes next turn)")
    except Exception as e:
        await _set_task_status(task_id, "blocked")  # #D30: failed → terminal, not in_progress
        _say(f"background turn FAILED for {agent_label} on {channel}: {e} — "
             f"the task row still tracks the work")
        await _report_back(agent_label, task_id, f"(delegated turn failed: {e})")


async def _report_back(agent_label: str, task_id: int, reply: str) -> None:
    """Land the delegate's reply where the steward can SEE it: the task notes
    + a system note in the steward's own day channel. Best-effort each."""
    summary = (reply or "(no reply captured)").strip()
    clipped = summary[:800] + ("…" if len(summary) > 800 else "")

    if task_id:
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE tasks SET notes = left(coalesce(notes,'') || %s, 4000), "
                    "updated_at = NOW() WHERE id = %s",
                    (f"\n[{agent_label} reply] {clipped}", task_id))
        except Exception as e:
            _say(f"task-note report-back failed: {e}")

    # Steward channel note — same injection pattern as #D14 (as_node='agent').
    try:
        from langchain_core.messages import SystemMessage
        from src.config import get_steward_channel_config
        from src.memory.checkpointer import get_checkpointer
        from src.memory.database import get_db
        from src.graphs.channels import get_channel_graph

        sc = get_steward_channel_config()
        if not sc:
            return
        steward_channel = f"{(sc.get('name') or 'stuart').lower()}-day"
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT thread_id FROM chat_threads WHERE channel = %s "
                "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
                (steward_channel,))
            row = await r.fetchone()
        if not row:
            return
        note = (f"[SYSTEM: delegation report — {agent_label} replied on their "
                f"delegated task #{task_id}: {clipped}]")
        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(steward_channel, checkpointer)
            await graph.aupdate_state(
                {"configurable": {"thread_id": row["thread_id"]}},
                {"messages": [SystemMessage(content=note)]},
                as_node="agent")
        _say(f"report-back posted to {steward_channel}")
    except Exception as e:
        _say(f"steward-channel report-back failed: {e}")


async def _set_task_status(task_id: int, status: str) -> None:
    """Best-effort delegated-task status transition (#D30). Scoped to source='agent'
    so it can only ever touch delegated task rows."""
    if not task_id:
        return
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                "UPDATE tasks SET status = %s, updated_at = NOW() "
                "WHERE id = %s AND source = 'agent'",
                (status, task_id))
    except Exception as e:
        _say(f"task status update ({status}) failed: {e}")


async def sweep_orphaned_delegations() -> int:
    """Boot-time recovery (#D30). A delegated background turn killed by a container
    restart can't file its own failure report — its task row is left 'in_progress'
    forever and looks alive. On startup, mark every agent-sourced in_progress task as
    'blocked' (interrupted by restart) with a note, and best-effort report-back to the
    steward channel so the loop closes. Returns the count swept. Never raises."""
    from src.memory.database import get_db
    note = ("\n[system] interrupted by a restart — the delegated turn did not finish; "
            "status set to blocked. Re-delegate to resume.")
    try:
        async with get_db() as conn:
            r = await conn.execute(
                "UPDATE tasks SET status = 'blocked', "
                "notes = left(coalesce(notes,'') || %s, 4000), updated_at = NOW() "
                "WHERE source = 'agent' AND status = 'in_progress' "
                "RETURNING id, assignee, title",
                (note,))
            rows = [dict(x) for x in await r.fetchall()]
    except Exception as e:
        _say(f"orphaned-delegation sweep failed: {e}")
        return 0
    for row in rows:
        try:
            await _report_back(
                row.get("assignee") or "agent", row["id"],
                "(interrupted by a restart — task marked blocked; re-delegate to resume)")
        except Exception:
            pass
    if rows:
        _say(f"swept {len(rows)} restart-orphaned delegation(s) → blocked")
    return len(rows)


@notify
@tool
async def delegate_task(agent: str, brief: str, ticket_ref: str = "") -> str:
    """Delegate work to a team agent: creates a tracked task, links the queue
    ticket, delivers your brief into THEIR channel, and starts them working.

    Write the brief the way the 07-09 dry-run did it: what to build, where the
    code lives (file paths), what done looks like, and any constraints. The
    operator still approves the agent's pushes and PRs — delegation never
    bypasses the gate.

    Args:
        agent: team agent to delegate to (e.g. 'archimedes')
        brief: the work brief — specific, source-verifiable, scoped
        ticket_ref: queue/board ref this fulfills (e.g. '#D15') — links the
                    steward-queue item and shows on the board
    """
    from src.agents.identity import load_agents_config
    from src.memory.database import get_db

    known = set(load_agents_config().keys())
    target = resolve_agent(agent, known)
    if not target:
        return (f"Unknown agent '{agent}'. Team agents: "
                f"{', '.join(sorted(known)) or 'none configured'}")

    brief = (brief or "").strip()
    if len(brief) < 40:
        return ("Brief too thin to delegate — include what to build, where the "
                "code lives, and what done looks like (the dry-run standard).")

    # 1. Tracked task
    async with get_db() as conn:
        r = await conn.execute(
            """INSERT INTO tasks (title, description, priority, assignee, created_by, source)
               VALUES (%s, %s, 'normal', %s, 'steward', 'agent') RETURNING id""",
            (f"[delegated{' ' + ticket_ref if ticket_ref else ''}] {brief[:70]}",
             brief, target))
        task_id = (await r.fetchone())["id"]

        # 2. Queue link (best-effort): a ref like '#D15' matches steward_queue.source
        queue_note = ""
        if ticket_ref:
            try:
                r = await conn.execute(
                    "UPDATE steward_queue SET status = 'assigned', assignee = %s, "
                    "updated_at = NOW() WHERE source = %s "
                    "AND status IN ('queued','assigned') RETURNING id",
                    (target, ticket_ref.strip()))
                qrow = await r.fetchone()
                if qrow:
                    queue_note = f" Queue item [{qrow['id']}] → assigned."
            except Exception:
                pass

    # 3+4. Deliver the brief + start the turn, in the background.
    channel = f"{target}-day"
    message = compose_brief(target, brief, ticket_ref, task_id)
    asyncio.get_event_loop().create_task(
        _run_agent_turn(channel, message, target, task_id))

    return (f"Delegated to {target}: task #{task_id} created, brief delivered "
            f"to {channel}, and {target} is starting now (background turn, "
            f"{DELEGATION_TURN_TIMEOUT // 60}m cap).{queue_note} Track via "
            f"get_tasks / queue_list; review their branch (git_diff_branch) "
            f"before the PR ask. The operator merges.")


ALL_DELEGATION_TOOLS = [delegate_task]
TOOLS = ALL_DELEGATION_TOOLS  # channel loader entry point (_load_tools)
