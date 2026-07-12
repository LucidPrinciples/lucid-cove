"""
Backlog board tools — the steward's window onto the operator's dev-intake board.

Geography (locked 2026-07-11, Chords): the operator's jules backlog
(`AgentSkills/Ops/jules-backlog.md` in the OPERATOR'S Nextcloud space) is INTAKE —
where dev work lands. The steward_queue is EXECUTION — where the team runs it.
These tools connect them: the steward READS the intake board, PULLS tickets into
the queue, and UPDATES board lines (lane moves, notes, done marks) so the operator
and the team are always looking at the same truth.

Why this exists: on 07-11 Stuart was asked about #D52, grepped his OWN NC scope
(agents' NC tools are scoped to their own user), honestly found nothing — while
the ticket sat on the operator's board. The board must be reachable across that
scope boundary, through the board OWNER'S credentials, resolved server-side.

Tier assignments:
  AUTO    — backlog_board (read-only)
  NOTIFY  — backlog_pull, backlog_update (board writes are visible, not gated)

The intake owner defaults to the Cove's admin operator (cove_role='admin');
override with the `dev_intake_account_id` setting. Writes go through WebDAV as
the intake owner (same path the jules processor writes), so NC versioning and
file ownership stay correct. v1 is read-modify-write without a lock — the board
is a low-traffic markdown file; last-writer-wins is acceptable and NC keeps
versions.
"""

import re
from urllib.parse import quote

from langchain_core.tools import tool

from src.env import env
from src.tools.approval import auto, notify

BOARD_RELPATH = "AgentSkills/Ops/jules-backlog.md"
MAX_BOARD_BYTES = 512 * 1024  # refuse to rewrite something that isn't a board

# Lane headers the board understands (## Now, ## Soon, ...). Unknown headers are
# preserved verbatim; #D43: INTERACTIVE/BLOCKED are their own lanes, never NOW.
_LANE_ALIASES = {
    "now": "Now", "soon": "Soon", "later": "Later",
    "projects": "Projects", "completed": "Completed", "done": "Completed",
    "interactive": "Interactive", "blocked": "Blocked",
}


# =============================================================================
# Pure text helpers (unit-tested directly)
# =============================================================================

def _ticket_pattern(ticket: str) -> re.Pattern:
    """Match a ticket id like '#D52' or '#1626' as a whole token."""
    t = ticket.strip()
    if not t.startswith("#"):
        t = "#" + t
    return re.compile(r"(^|[^\w#])" + re.escape(t) + r"(?![\w])")


def find_ticket(text: str, ticket: str):
    """Return (line_index, lane_header or None) for the first item line
    mentioning the ticket, or (None, None)."""
    pat = _ticket_pattern(ticket)
    lane = None
    for i, line in enumerate(text.split("\n")):
        s = line.strip()
        if s.startswith("## "):
            lane = s[3:].strip()
        if s.startswith("- ") and pat.search(line):
            return i, lane
    return None, None


def _lane_header_index(lines: list, lane: str):
    """Index of the '## <lane>' header line, tolerant of suffixes
    ('## Now — this week'). None if absent."""
    want = _LANE_ALIASES.get(lane.strip().lower(), lane.strip()).lower()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("## ") and s[3:].strip().lower().startswith(want):
            return i
    return None


def move_ticket_lane(text: str, ticket: str, lane: str):
    """Move the ticket's line under another lane header. Returns (new_text, msg)."""
    idx, from_lane = find_ticket(text, ticket)
    if idx is None:
        return text, f"Ticket {ticket} not found on the board."
    lines = text.split("\n")
    hdr = _lane_header_index(lines, lane)
    if hdr is None:
        return text, (f"Lane '{lane}' not found. Lanes present: "
                      + ", ".join(l.strip()[3:] for l in lines
                                  if l.strip().startswith("## ")))
    item = lines.pop(idx)
    if idx < hdr:
        hdr -= 1
    lines.insert(hdr + 1, item)
    return "\n".join(lines), f"Moved {ticket}: {from_lane or '?'} → {lines[hdr].strip()[3:]}."


def annotate_ticket(text: str, ticket: str, note: str):
    """Append ' · <note>' to the ticket's line. Returns (new_text, msg)."""
    idx, _ = find_ticket(text, ticket)
    if idx is None:
        return text, f"Ticket {ticket} not found on the board."
    lines = text.split("\n")
    note = note.strip().replace("\n", " ")[:300]
    lines[idx] = lines[idx].rstrip() + f" · {note}"
    return "\n".join(lines), f"Annotated {ticket}: {note}"


def mark_ticket_done(text: str, ticket: str):
    """Flip '- [ ]' to '- [x]' on the ticket's line. Returns (new_text, msg)."""
    idx, _ = find_ticket(text, ticket)
    if idx is None:
        return text, f"Ticket {ticket} not found on the board."
    lines = text.split("\n")
    if "- [x]" in lines[idx]:
        return text, f"{ticket} is already marked done."
    if "- [ ]" not in lines[idx]:
        return text, f"{ticket}'s line has no checkbox to mark."
    lines[idx] = lines[idx].replace("- [ ]", "- [x]", 1)
    return "\n".join(lines), f"Marked {ticket} done on the board."


def ticket_title(text: str, ticket: str) -> str:
    """Short queue title from the ticket's board line."""
    idx, _ = find_ticket(text, ticket)
    if idx is None:
        return ""
    line = text.split("\n")[idx].strip()
    line = re.sub(r"^- \[[ x]\]\s*", "", line)
    line = line.replace("**", "")
    return line[:70]


# =============================================================================
# Board I/O (through the intake owner's NC credentials)
# =============================================================================

async def _intake_creds():
    """(nc_url, nc_user, nc_pass, label) for the Cove's dev-intake owner.
    Setting `dev_intake_account_id` overrides; default = the admin operator."""
    nc_url = env("NEXTCLOUD_URL")
    if not nc_url:
        raise RuntimeError("NEXTCLOUD_URL not configured")
    from src.memory.database import get_db
    from src.utils.settings import get_setting
    override = (await get_setting("dev_intake_account_id", default="") or "").strip()
    async with get_db() as conn:
        if override:
            r = await conn.execute(
                "SELECT username, nc_username, nc_password FROM accounts WHERE id = %s",
                (override,))
        else:
            r = await conn.execute(
                "SELECT username, nc_username, nc_password FROM accounts "
                "WHERE cove_role = 'admin' AND nc_username IS NOT NULL "
                "ORDER BY created_at LIMIT 1")
        row = await r.fetchone()
    if not row or not row["nc_username"] or not row["nc_password"]:
        raise RuntimeError("No intake owner with Nextcloud credentials found "
                           "(set dev_intake_account_id or provision the admin's NC user)")
    return nc_url, row["nc_username"], row["nc_password"], row["username"]


def _dav_url(nc_url: str, nc_user: str) -> str:
    return f"{nc_url}/remote.php/dav/files/{nc_user}/{quote(BOARD_RELPATH)}"


# WebDAV codes worth retrying: 423 = a transient Nextcloud transactional lock
# (normally clears in ms; an orphaned one from a timed-out PUT is what wedged the
# board on 07-12), 429/5xx = momentary server hiccups. Everything else (auth, 404,
# size) fails fast. Backoff totals ~6s across the retries.
_TRANSIENT_DAV = {423, 429, 500, 502, 503, 504}
_RETRY_DELAYS = (0.4, 0.8, 1.6, 3.2)


async def _board_get():
    """Returns (text, provenance_label). Retries a transient WebDAV lock (423)
    before giving up. Raises with a plain message on real failure."""
    import asyncio
    import httpx
    nc_url, nc_user, nc_pass, label = await _intake_creds()
    url = _dav_url(nc_url, nc_user)
    last = "no response"
    async with httpx.AsyncClient(timeout=20, auth=(nc_user, nc_pass)) as client:
        for delay in (0.0, *_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await client.get(url)
            except httpx.TimeoutException as e:
                last = f"timeout: {e}"
                continue
            if resp.status_code == 200:
                return resp.text, f"{label or nc_user}'s board ({nc_user}:{BOARD_RELPATH})"
            if resp.status_code == 404:
                raise RuntimeError(f"No board file yet at {nc_user}:{BOARD_RELPATH}")
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in _TRANSIENT_DAV:
                break
    raise RuntimeError(f"Board read failed ({last})")


async def _board_put(text: str):
    """Write the board via WebDAV, retrying a transient Nextcloud lock (423) with
    backoff. Last-writer-wins is intentional (see module doc); this only makes the
    write survive a momentary lock instead of hard-failing the whole board on the
    first 423 — the failure mode that wedged it."""
    import asyncio
    import httpx
    if len(text.encode("utf-8")) > MAX_BOARD_BYTES:
        raise RuntimeError("Refusing write: board text exceeds size guard")
    nc_url, nc_user, nc_pass, _ = await _intake_creds()
    url = _dav_url(nc_url, nc_user)
    body = text.encode("utf-8")
    last = "no response"
    async with httpx.AsyncClient(timeout=30, auth=(nc_user, nc_pass)) as client:
        for delay in (0.0, *_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await client.put(url, content=body)
            except httpx.TimeoutException as e:
                last = f"timeout: {e}"
                continue
            if resp.status_code in (200, 201, 204):
                return
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in _TRANSIENT_DAV:
                break
    raise RuntimeError(f"Board write failed ({last} after retries)")


async def _insert_queue_row(source: str, title: str, assignee: str) -> int:
    """Create (or claim) the steward_queue row for a pulled ticket. Mirrors
    delegation_tools.link_or_create_queue_row semantics: claim an open row on
    the same source first, else INSERT."""
    from src.memory.database import get_db
    status = "assigned" if assignee else "queued"
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT id, status FROM steward_queue WHERE source = %s "
            "AND status IN ('queued','assigned') LIMIT 1", (source,))
        row = await r.fetchone()
        if row:
            if assignee:
                await conn.execute(
                    "UPDATE steward_queue SET status='assigned', assignee=%s, "
                    "updated_at=NOW() WHERE id=%s", (assignee, row["id"]))
            return row["id"]
        r = await conn.execute(
            "INSERT INTO steward_queue (source, title, status, assignee) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (source, title, status, assignee or None))
        row = await r.fetchone()
        return row["id"]


# =============================================================================
# Tools
# =============================================================================

@auto
@tool
async def backlog_board(lane: str = "") -> str:
    """Read the operator's dev-intake backlog board (cross-scope: the board
    lives in the OPERATOR'S space, not yours — this tool reaches it for you).

    Args:
        lane: optional lane filter (now/soon/later/projects/completed/...)
    """
    try:
        text, label = await _board_get()
    except Exception as e:
        return f"Board unavailable: {e}"
    if lane:
        lines = text.split("\n")
        hdr = _lane_header_index(lines, lane)
        if hdr is None:
            return (f"Lane '{lane}' not found on {label}. Lanes: "
                    + ", ".join(l.strip()[3:] for l in lines
                                if l.strip().startswith("## ")))
        out = [lines[hdr]]
        for line in lines[hdr + 1:]:
            if line.strip().startswith("## "):
                break
            out.append(line)
        text = "\n".join(out).strip()
    if len(text) > 12000:
        text = text[:12000] + "\n... [truncated — use the lane filter]"
    return f"SOURCE: {label}\n\n{text}"


@notify
@tool
async def backlog_pull(ticket: str, assignee: str = "") -> str:
    """Pull a ticket from the operator's intake board into the steward queue
    (creates the queue row, annotates the board line with the queue id).

    Args:
        ticket: the board ticket id, e.g. '#D52' or '#1626'
        assignee: who takes it now (your agent id) — optional, else it queues
    """
    try:
        text, label = await _board_get()
    except Exception as e:
        return f"Board unavailable: {e}"
    idx, lane = find_ticket(text, ticket)
    if idx is None:
        return (f"Ticket {ticket} not found on {label}. "
                "Read it with backlog_board first — ids match as whole tokens.")
    if not ticket.startswith("#"):
        ticket = "#" + ticket
    title = ticket_title(text, ticket) or ticket
    try:
        qid = await _insert_queue_row(f"board:{ticket}", title, assignee.strip())
    except Exception as e:
        return f"Queue insert failed (board untouched): {e}"
    new_text, msg = annotate_ticket(text, ticket, f"→ queue#{qid}")
    try:
        await _board_put(new_text)
        board_note = msg
    except Exception as e:
        board_note = f"queue row created but board annotate failed: {e}"
    return (f"Pulled {ticket} ('{title}') from {lane or '?'} lane into the queue "
            f"as [{qid}] ({'assigned to ' + assignee if assignee else 'queued'}). {board_note}")


@notify
@tool
async def backlog_update(ticket: str, lane: str = "", note: str = "",
                         done: bool = False) -> str:
    """Update a ticket ON the operator's intake board: move lanes, append a
    note, or mark it done. (Queue rows update via queue_update — this is the
    board side.)

    Args:
        ticket: the board ticket id, e.g. '#D52'
        lane: move to this lane (now/soon/later/projects/completed) — optional
        note: append a short note to the ticket's line — optional
        done: mark the checkbox done — optional
    """
    if not (lane or note or done):
        return "Nothing to do — pass lane, note, and/or done."
    try:
        text, label = await _board_get()
    except Exception as e:
        return f"Board unavailable: {e}"
    msgs = []
    if lane:
        text, m = move_ticket_lane(text, ticket, lane)
        msgs.append(m)
    if done:
        text, m = mark_ticket_done(text, ticket)
        msgs.append(m)
    if note:
        text, m = annotate_ticket(text, ticket, note)
        msgs.append(m)
    if all(("not found" in m) for m in msgs):
        return f"Ticket {ticket} not found on {label}."
    try:
        await _board_put(text)
    except Exception as e:
        return f"Board write failed, nothing saved: {e}"
    return " ".join(msgs) + f" (on {label})"


ALL_BACKLOG_TOOLS = [backlog_board, backlog_pull, backlog_update]
TOOLS = ALL_BACKLOG_TOOLS  # channel loader entry point (_load_tools)
