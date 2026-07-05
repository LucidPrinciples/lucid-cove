"""jules → backlog processor.

The key workflow loop: the operator talks (jules records to AgentSkills/Inbox),
the agent responds in text — new jules transcripts get processed into action
items, appended to the Presence's jules-backlog.md, and the source files
(md + audio) archived to AgentSkills/Inbox/Archive.

Runs automatically after every jules save (background, best-effort — a
processing failure NEVER breaks the recording path and leaves the transcript
untouched in the Inbox). Also exposed as POST /api/jules/process for the
"Process jules" button on the Backlog board or any agent to drive.

Env: JULES_AUTO_PROCESS=0 disables the auto-run (default on).
"""

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.env import env

router = APIRouter()
log = logging.getLogger("jules_process")

INBOX_PATH = "AgentSkills/Inbox"
ARCHIVE_PATH = "AgentSkills/Inbox/Archive"
BACKLOG_PATH = "AgentSkills/Ops/jules-backlog.md"
LOG_PATH = "AgentSkills/Ops/jules-log.md"
AUDIO_EXTS = (".webm", ".wav", ".mp3", ".m4a", ".ogg")

_LOG_HEADER = """# jules processing log

> One line per processed recording — the paper trail: Inbox → backlog items → Archive.
> FAILED lines mean the recording is still in the Inbox and will be retried
> (next save, the board's Process button, or the 30-minute sweep).
"""

_BACKLOG_SKELETON = """# jules backlog

> Auto-maintained: jules voice notes are processed into items here.
> Lanes: Now / Soon / Later / Projects / Completed.

## Now

## Soon

## Later

## Projects

## Completed
"""

_EXTRACT_PROMPT = """You process the operator's voice-note transcript into backlog items.

Extract every distinct action item or notable idea. Return ONLY a JSON array,
no prose, no code fences. Each element:
  {"title": "short imperative title", "desc": "one-sentence description", "lane": "now"|"soon"|"later"}

Rules:
- "now" only if the operator says it's urgent/next; default to "soon" for tasks, "later" for ideas.
- Keep the operator's reference numbers (like M12, CF-31, B9) in the title when spoken.
- If the transcript contains no actionable content, return [].
"""


# ── WebDAV helpers (same auth/URL shape as jules.py) ─────────────────────────

def _dav_base(nc_url: str, nc_user: str) -> str:
    return f"{nc_url}/remote.php/dav/files/{nc_user}"


async def _list_inbox(client: httpx.AsyncClient, nc_url: str, nc_user: str) -> list:
    """Depth-1 PROPFIND on the Inbox; returns filenames (not paths)."""
    url = f"{_dav_base(nc_url, nc_user)}/{INBOX_PATH}"
    resp = await client.request("PROPFIND", url, headers={"Depth": "1"})
    if resp.status_code != 207:
        raise RuntimeError(f"Inbox PROPFIND HTTP {resp.status_code}")
    names = []
    ns = "{DAV:}"
    root = ET.fromstring(resp.text)
    for r in root.findall(f"{ns}response"):
        href = unquote((r.findtext(f"{ns}href") or ""))
        name = href.rstrip("/").split("/")[-1]
        if href.rstrip("/").endswith(INBOX_PATH):
            continue  # the folder itself
        if r.find(f".//{ns}collection") is not None:
            continue  # subfolders (Archive)
        names.append(name)
    return names


async def _read_file(client: httpx.AsyncClient, nc_url: str, nc_user: str, rel: str) -> str:
    resp = await client.get(f"{_dav_base(nc_url, nc_user)}/{quote(rel)}")
    if resp.status_code != 200:
        raise RuntimeError(f"GET {rel} HTTP {resp.status_code}")
    return resp.text


async def _write_file(client: httpx.AsyncClient, nc_url: str, nc_user: str, rel: str, content: str) -> None:
    resp = await client.put(
        f"{_dav_base(nc_url, nc_user)}/{quote(rel)}",
        content=content.encode("utf-8"),
        headers={"Content-Type": "text/markdown"},
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"PUT {rel} HTTP {resp.status_code}")


async def _ensure_archive(client: httpx.AsyncClient, nc_url: str, nc_user: str) -> None:
    resp = await client.request("MKCOL", f"{_dav_base(nc_url, nc_user)}/{ARCHIVE_PATH}")
    if resp.status_code not in (201, 405):  # 405 = already exists
        raise RuntimeError(f"MKCOL Archive HTTP {resp.status_code}")


async def _move_to_archive(client: httpx.AsyncClient, nc_url: str, nc_user: str, name: str) -> bool:
    src = f"{_dav_base(nc_url, nc_user)}/{INBOX_PATH}/{quote(name)}"
    dst = f"{_dav_base(nc_url, nc_user)}/{ARCHIVE_PATH}/{quote(name)}"
    resp = await client.request("MOVE", src, headers={"Destination": dst, "Overwrite": "F"})
    if resp.status_code == 412:  # name collision in Archive — suffix and retry
        stamped = re.sub(r"(\.[^.]+)$", f"-{datetime.now(timezone.utc).strftime('%H%M%S')}\\1", name)
        dst = f"{_dav_base(nc_url, nc_user)}/{ARCHIVE_PATH}/{quote(stamped)}"
        resp = await client.request("MOVE", src, headers={"Destination": dst, "Overwrite": "F"})
    return resp.status_code in (201, 204)


# ── Item extraction (the agent's brain) ──────────────────────────────────────

def _resolve_agent_id() -> str:
    """The Cove's own agent processes jules. JULES_PROCESS_AGENT pins the task
    to a specific team member (e.g. soren); otherwise julian where he exists,
    else the steward/first agent, else the default chain."""
    pinned = (env("JULES_PROCESS_AGENT", "") or "").strip()
    if pinned:
        return pinned
    try:
        from src.config import load_cove_config
        cfg = load_cove_config() or {}
        agents = [a.get("id") or a.get("handle", "").split(".")[0]
                  for a in (cfg.get("agents") or []) if isinstance(a, dict)]
        agents = [a for a in agents if a]
        if "julian" in agents:
            return "julian"
        if agents:
            return agents[0]
    except Exception:
        pass
    return "stuart"


async def _extract_items(transcript: str) -> list | None:
    """Model call → list of {title, desc, lane}. None = failure (leave file in Inbox)."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.models.provider import invoke_with_fallback
        raw = await invoke_with_fallback(
            [SystemMessage(content=_EXTRACT_PROMPT), HumanMessage(content=transcript[:12000])],
            temperature=0.2, timeout=90, label="jules/process",
            agent_id=_resolve_agent_id(), operation_type="task",
        )
    except Exception as e:
        log.warning("[jules-process] model call failed: %s", e)
        return None
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\[.*\]", text, flags=re.S)
    if not m:
        return None
    try:
        items = json.loads(m.group(0))
    except Exception:
        return None
    clean = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "")).strip()
        if not title:
            continue
        lane = str(it.get("lane", "soon")).lower()
        clean.append({
            "title": title[:120],
            "desc": str(it.get("desc", "")).strip()[:400],
            "lane": lane if lane in ("now", "soon", "later") else "soon",
        })
    return clean


# ── Backlog append ────────────────────────────────────────────────────────────

def _append_items(backlog_text: str, items: list, source: str) -> tuple:
    """Insert items at the top of their lane, numbered past the current max #N.
    Returns (new_text, assigned_numbers)."""
    nums = [int(n) for n in re.findall(r"#(\d+)\s", backlog_text)]
    next_num = (max(nums) + 1) if nums else 1
    assigned = []
    by_lane = {"now": [], "soon": [], "later": []}
    for it in items:
        line = f"- [ ] **#{next_num} {it['title']}.** {it['desc']} `[jules]` *({source})*"
        by_lane[it["lane"]].append(line)
        assigned.append(next_num)
        next_num += 1
    lines = backlog_text.split("\n")
    lane_headers = {"now": "## now", "soon": "## soon", "later": "## later"}
    for lane, new_lines in by_lane.items():
        if not new_lines:
            continue
        idx = None
        for i, ln in enumerate(lines):
            if ln.strip().lower().startswith(lane_headers[lane]):
                idx = i + 1
                break
        if idx is None:
            lines += ["", f"## {lane.capitalize()}", ""] + new_lines
        else:
            lines[idx:idx] = [""] + new_lines if (idx < len(lines) and lines[idx].strip()) else new_lines
    return "\n".join(lines), assigned


# ── Processing log (the paper trail) ─────────────────────────────────────────

async def _log_entry(client: httpx.AsyncClient, nc_url: str, nc_user: str,
                     name: str, outcome: str, trigger: str) -> None:
    """Append one line to AgentSkills/Ops/jules-log.md. Best-effort — the log
    never blocks processing, but a log write failure is itself logged to app logs."""
    try:
        try:
            existing = await _read_file(client, nc_url, nc_user, LOG_PATH)
        except Exception:
            existing = _LOG_HEADER
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        line = f"- {stamp} · **{name}** → {outcome} · via {trigger}"
        await _write_file(client, nc_url, nc_user, LOG_PATH, existing.rstrip("\n") + "\n" + line + "\n")
    except Exception as e:
        log.warning("[jules-process] log write failed for %s: %s", name, e)


# ── The sweep ─────────────────────────────────────────────────────────────────

async def process_inbox(nc_url: str, nc_user: str, nc_pass: str, trigger: str = "auto") -> dict:
    """Process every jules-*.md in the Inbox. Per-file: extract → append →
    log → archive. A file only archives after its items are safely IN the
    backlog; every outcome (success or failure) writes a jules-log.md line."""
    processed, added, failed = [], 0, []
    agent = _resolve_agent_id()
    async with httpx.AsyncClient(timeout=60, auth=(nc_user, nc_pass)) as client:
        names = await _list_inbox(client, nc_url, nc_user)
        # Addressed recordings are NOT ours to process: jules-for-{recipient}-*
        # waits for that agent/person, jules-hold-* waits for the operator. The
        # Inbox is a shared drop-zone — only plain jules-* means "for the backlog".
        jules_mds = sorted(
            n for n in names
            if n.startswith("jules-") and n.endswith(".md")
            and not n.startswith(("jules-for-", "jules-hold-"))
        )
        if not jules_mds:
            return {"ok": True, "processed": 0, "items_added": 0, "failed": []}
        await _ensure_archive(client, nc_url, nc_user)
        for name in jules_mds:
            try:
                body = await _read_file(client, nc_url, nc_user, f"{INBOX_PATH}/{name}")
                transcript = body.split("---", 1)[-1].strip() if "---" in body else body.strip()
                if not transcript:
                    items = []
                else:
                    items = await _extract_items(transcript)
                    if items is None:
                        failed.append({"file": name, "error": "extraction failed — left in Inbox"})
                        await _log_entry(client, nc_url, nc_user, name,
                                         "FAILED: extraction failed — LEFT IN INBOX", trigger)
                        continue
                nums = []
                if items:
                    try:
                        backlog = await _read_file(client, nc_url, nc_user, BACKLOG_PATH)
                    except Exception:
                        backlog = _BACKLOG_SKELETON
                    stem = name[:-3]
                    new_text, nums = _append_items(backlog, items, stem)
                    await _write_file(client, nc_url, nc_user, BACKLOG_PATH, new_text)
                    added += len(items)
                # archive the md + any sibling audio (only reached when items are saved or none found)
                await _move_to_archive(client, nc_url, nc_user, name)
                stem = name[:-3]
                audio_moved = False
                for ext in AUDIO_EXTS:
                    if f"{stem}{ext}" in names:
                        audio_moved = await _move_to_archive(client, nc_url, nc_user, f"{stem}{ext}") or audio_moved
                outcome = (f"items #{nums[0]}–#{nums[-1]} ({len(nums)})" if nums
                           else "no action items") + f" · agent {agent} · archived" + \
                          (" (audio: yes)" if audio_moved else "")
                await _log_entry(client, nc_url, nc_user, name, outcome, trigger)
                processed.append(name)
            except Exception as e:
                log.warning("[jules-process] %s failed: %s", name, e)
                failed.append({"file": name, "error": str(e)[:200]})
                try:
                    await _log_entry(client, nc_url, nc_user, name,
                                     f"FAILED: {str(e)[:120]} — LEFT IN INBOX", trigger)
                except Exception:
                    pass
    return {"ok": True, "processed": len(processed), "items_added": added,
            "files": processed, "failed": failed}


async def sweep_all_presences() -> dict:
    """Catch-up sweep for the scheduler: process EVERY active presence's Inbox
    with their own NC creds (same enumeration as the Cove backup). A recording
    stuck by a save-time failure is retried here — nothing waits on an operator.
    Cheap no-op when every Inbox is empty."""
    if env("JULES_AUTO_PROCESS", "1") in ("0", "false", "off"):
        return {"ok": True, "skipped": "JULES_AUTO_PROCESS off"}
    nc_url = (env("NEXTCLOUD_URL") or "").rstrip("/")
    if not nc_url:
        return {"ok": True, "skipped": "no NEXTCLOUD_URL"}
    totals = {"ok": True, "presences": 0, "processed": 0, "items_added": 0, "failed": 0}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT nc_username, nc_password FROM accounts "
                "WHERE active = TRUE AND nc_username IS NOT NULL AND nc_username <> '' "
                "AND nc_password IS NOT NULL AND nc_password <> ''")
            rows = await r.fetchall()
    except Exception as e:
        return {"ok": False, "error": f"presence enumeration failed: {str(e)[:120]}"}
    for row in rows or []:
        try:
            res = await process_inbox(nc_url, row["nc_username"], row["nc_password"], trigger="sweep")
            totals["presences"] += 1
            totals["processed"] += res.get("processed", 0)
            totals["items_added"] += res.get("items_added", 0)
            totals["failed"] += len(res.get("failed", []))
        except Exception as e:
            log.warning("[jules-process] sweep %s failed: %s", row["nc_username"], e)
            totals["failed"] += 1
    return totals


def schedule_auto_process(nc_url: str, nc_user: str, nc_pass: str) -> None:
    """Fire-and-forget hook for the jules save path. Never raises."""
    try:
        if env("JULES_AUTO_PROCESS", "1") in ("0", "false", "off"):
            return
        if not (nc_url and nc_user and nc_pass):
            return

        async def _run():
            try:
                res = await process_inbox(nc_url, nc_user, nc_pass)
                log.info("[jules-process] auto: %s", res)
            except Exception as e:
                log.warning("[jules-process] auto failed (Inbox untouched): %s", e)

        asyncio.get_event_loop().create_task(_run())
    except Exception:
        pass


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/api/jules/process")
async def process_now(request: Request):
    """Process the jules Inbox into the backlog (the Backlog board button)."""
    from src.dashboard.routes.nextcloud import get_nc_creds
    nc_url, nc_user, nc_pass = await get_nc_creds(request)
    if not nc_user or not nc_pass:
        return JSONResponse({"ok": False, "error": "Nextcloud not configured"}, status_code=500)
    try:
        return JSONResponse(await process_inbox(nc_url, nc_user, nc_pass, trigger="button"))
    except Exception as e:
        log.error("[jules-process] sweep failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
