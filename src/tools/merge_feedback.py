"""
#D49 — merge-feedback reaches the agent.

Chords merging a PR is a GitHub event the Cove never hears: the agent that built
the work doesn't learn it landed, and — worse — may conflate "merged" with
"deployed". This closes that loop. On a schedule (and on demand via the endpoint)
it:

  1. reads steward_queue rows still in flight WITH a pr_url (assigned/in_review),
  2. asks GitHub whether each row's PR is merged (REST, read-only),
  3. annotates the matching row — note "merged (PR #N) — merged != deployed" —
     WITHOUT advancing it to done (merged != deployed is the whole point; the
     operator marks done at deploy time),
  4. drops a one-line system note into the steward's day channel so the agent
     actually hears it (reuses the #D14/#D23 aupdate_state inject pattern).

Side benefit: it writes verified merge state onto the row, which is exactly what
the /ops reconcile() needs to stop guessing about an in-flight ticket.

GitHub access is READ-ONLY (never shell git). The only writes are the queue-row
annotation (through the shared steward_queue_tools._update, one transition
matrix) and a best-effort channel note. Idempotent: a row already carrying the
"merged (PR #N)" sentinel is skipped, so re-runs are safe.
"""

import re

_PR_URL_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")


def _sentinel(number: int) -> str:
    return f"merged (PR #{number})"


# =============================================================================
# Pure helpers (unit-tested hardest — this is the matching logic)
# =============================================================================

def parse_pr_url(pr_url: str):
    """(owner/name, number) parsed from a PR html_url, or None."""
    if not pr_url:
        return None
    m = _PR_URL_RE.search(pr_url)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def plan_merge_feedback(rows: list, merged_numbers: set) -> list:
    """PURE. Decide which in-flight rows to annotate.

    Args:
        rows: [{id, title, pr_url, notes}] — steward_queue rows in flight.
        merged_numbers: set[int] of PR numbers GitHub confirms are merged.
    Returns:
        [{id, pr_number, title, note}] for rows whose PR is merged and NOT already
        annotated. A row with no parseable pr_url, an unmerged PR, or an existing
        "merged (PR #N)" sentinel in its notes is skipped (idempotent).
    """
    actions = []
    for r in rows:
        parsed = parse_pr_url(r.get("pr_url", "") or "")
        if not parsed:
            continue
        _, number = parsed
        if number not in merged_numbers:
            continue
        if _sentinel(number) in (r.get("notes", "") or ""):
            continue
        actions.append({
            "id": r["id"],
            "pr_number": number,
            "title": r.get("title", "") or "",
            "note": f"{_sentinel(number)} — merged != deployed (operator deploys to ship)",
        })
    return actions


# =============================================================================
# I/O (thin wrappers)
# =============================================================================

async def _fetch_in_flight_rows() -> list:
    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT id, title, status, pr_url, notes FROM steward_queue "
            "WHERE status IN ('assigned','in_review') "
            "AND pr_url IS NOT NULL AND pr_url <> '' LIMIT 100")
        return [dict(x) for x in await r.fetchall()]


async def _merged_numbers_for(pairs: set) -> set:
    """pairs = set[(slug, number)]. Returns set[int] of numbers merged on GitHub.
    Read-only REST; degrades to empty on any failure (never blocks the caller)."""
    if not pairs:
        return set()
    import httpx
    from src.tools.dev_tools import _github_token
    from src.utils.github import _headers
    token = _github_token()
    if not token:
        return set()
    merged = set()
    async with httpx.AsyncClient(timeout=5.0, headers=_headers(token)) as client:
        for slug, number in pairs:
            try:
                resp = await client.get(
                    f"https://api.github.com/repos/{slug}/pulls/{number}")
                if resp.status_code == 200 and resp.json().get("merged"):
                    merged.add(number)
            except Exception:
                continue
    return merged


async def _notify_steward(text: str) -> None:
    """Best-effort: inject a system note into the steward's day-channel active
    thread so the agent hears the merge. Mirrors #D14/#D23. Never raises."""
    try:
        from src.config import get_steward_channel_config
        sc = get_steward_channel_config()
        if not sc:
            return
        channel = f"{(sc.get('name') or 'stuart')}-day"
        from langchain_core.messages import SystemMessage
        from src.memory.checkpointer import get_checkpointer
        from src.graphs.channels import get_channel_graph
        from src.memory.database import channel_db_scope, get_db
        async with channel_db_scope(channel):
            async with get_checkpointer() as cp:
                graph = await get_channel_graph(channel, cp)
                async with get_db() as conn:
                    rows = await (await conn.execute(
                        "SELECT thread_id FROM chat_threads WHERE channel = %s "
                        "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
                        (channel,))).fetchall()
                if not rows:
                    return
                config = {"configurable": {"thread_id": rows[0]["thread_id"]}}
                await graph.aupdate_state(
                    config, {"messages": [SystemMessage(content=text)]},
                    as_node="agent")
    except Exception as e:
        print(f"[merge-feedback] notify skipped: {type(e).__name__}: {e}")


async def sync_merged_prs() -> dict:
    """One pass: find in-flight rows whose PR merged, annotate them, tell the
    agent. Returns a summary dict (also the endpoint's JSON response)."""
    rows = await _fetch_in_flight_rows()
    pairs = set()
    for r in rows:
        p = parse_pr_url(r.get("pr_url", "") or "")
        if p:
            pairs.add(p)
    merged_numbers = await _merged_numbers_for(pairs)
    actions = plan_merge_feedback(rows, merged_numbers)

    from src.tools.steward_queue_tools import _update
    applied = []
    for a in actions:
        try:
            await _update(a["id"], note=a["note"])   # annotate only — NOT done
            await _notify_steward(
                f"[SYSTEM: PR #{a['pr_number']} ({a['title']}) was MERGED by the "
                f"operator. merged != deployed — it ships when the boxes pull + "
                f"restart (RB16-DEPLOY). Queue item [{a['id']}] annotated; do NOT "
                f"mark it done until it's deployed.]")
            applied.append(a)
        except Exception as e:
            print(f"[merge-feedback] apply failed for row {a['id']}: {e}")

    return {
        "checked": len(rows),
        "merged": len(merged_numbers),
        "annotated": len(applied),
        "actions": applied,
    }
