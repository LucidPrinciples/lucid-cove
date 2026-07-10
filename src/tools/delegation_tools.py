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

log = logging.getLogger("delegation")

DELEGATION_TURN_TIMEOUT = 900  # seconds — a background turn gets 15 minutes


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


async def _run_agent_turn(channel: str, message: str, agent_label: str) -> None:
    """Background: deliver the brief as a human turn in the agent's channel and
    run the agent's graph once. Best-effort — on any failure the brief is still
    queued as a task and the channel history explains the state."""
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
        async with get_checkpointer() as checkpointer:
            graph = await get_channel_graph(channel, checkpointer)
            cfg = {"configurable": {"thread_id": thread_id}}
            await asyncio.wait_for(graph.ainvoke(graph_input, config=cfg),
                                   timeout=DELEGATION_TURN_TIMEOUT)
        log.info(f"[delegation] {agent_label} completed a delegated turn on {channel}")
    except asyncio.TimeoutError:
        log.warning(f"[delegation] {agent_label}'s turn timed out on {channel} "
                    f"— brief is in the channel; it resumes on the next turn")
    except Exception as e:
        log.warning(f"[delegation] background turn failed for {agent_label} "
                    f"on {channel}: {e} — brief delivery may be incomplete; "
                    f"the task row still tracks the work")


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
        _run_agent_turn(channel, message, target))

    return (f"Delegated to {target}: task #{task_id} created, brief delivered "
            f"to {channel}, and {target} is starting now (background turn, "
            f"{DELEGATION_TURN_TIMEOUT // 60}m cap).{queue_note} Track via "
            f"get_tasks / queue_list; review their branch (git_diff_branch) "
            f"before the PR ask. The operator merges.")


ALL_DELEGATION_TOOLS = [delegate_task]
TOOLS = ALL_DELEGATION_TOOLS  # channel loader entry point (_load_tools)
