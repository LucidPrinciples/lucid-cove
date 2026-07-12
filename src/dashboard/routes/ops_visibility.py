"""
Ops-Visibility Surface — one page of VERIFIED dev-loop state (Phase 1).

Successor to the retired Haven MC board. The whole point (Chords, 2026-07-11):
"what you say, what he says, and what I see are never in line." This page ends
that by rendering ONLY state fetched from the system that OWNS it — never a
claim relayed by an agent:

  INTAKE  — the operator's jules-backlog board, read cross-scope through
            Companion C's backlog_tools._board_get (intake-owner NC creds + the
            transient-423 retry). NOT a fresh Nextcloud read.
  QUEUE   — steward_queue rows + recent approval_requests (credential-redacted),
            straight from the DB.
  GITHUB  — the GitHub REST API at render time (open PRs, recently merged,
            branches, main tip). REST ONLY — never shell git; the container
            clone is not the truth.
  VAULT   — read-only render of Memory.md (top) + the newest handoff-*.md.

The product is reconcile(): it diffs those sources and surfaces the DISAGREEMENTS
(queue says done but GitHub has no PR; an open PR no queue row tracks; a board
ticket marked done that nothing else can confirm). Disagreement is the highest-
value output — it is the UI face of #D50 telemetry reconciliation, and the guard
against the #D18/#D52 fabrication class.

Non-negotiables (spec): verified state only · read-only v1 (no mutating buttons) ·
every row source-labeled with a fetched-at time · a failing source renders
"unavailable", NEVER empty-equals-fine (no silent truncation) · admin/operator
gated, same gate as the steward queue · repo-first so every Cove inherits it.

Route: GET /api/ops/state  → the assembled JSON.
       GET /ops            → serves the static page.
"""

import re
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.env import env

router = APIRouter()

# Which repos the GITHUB column tracks. Owner is resolved from the local clone's
# origin (see _github_owner_token); these are the sibling names under that owner.
_DEFAULT_REPOS = ("lucid-cove", "ltp-core", "ltp-drop")
_GH_CACHE_TTL = 60          # seconds — one poll cycle
_GH_TIMEOUT = 5.0           # per-call ceiling; degrade gracefully past it
_VAULT_MEMORY_LINES = 80    # top-of-file render depth


# =============================================================================
# Access gate — admin/operator only (mirrors steward_queue._require_operator)
# =============================================================================

async def _require_operator(request: Request) -> bool:
    """True if allowed. In single mode the local operator IS the admin, so it's
    open; in multi mode only the admin presence passes. Never leaks to members."""
    if env("COVE_MODE", "single") != "multi":
        return True
    try:
        from src.dashboard.routes.presence import get_current_presence
        p = await get_current_presence(request)
    except Exception:
        return False
    return bool(p and p.get("cove_role") == "admin")


# =============================================================================
# Ticket-id matching (pure) — the join key across all three systems
# =============================================================================

# A board/queue/PR ticket id: '#D52', '#1626', or bare 'D52' in a branch name.
_ID_RE = re.compile(r"#([A-Za-z]{0,3}\d+)\b")
# Branch ids follow the team convention: a 'd'-prefixed dev id (stuart/d40-...)
# or a 3+ digit ticket number (stuart/1626-...). Version-ish segments like 'v12'
# are NOT ticket ids and must not match.
_BRANCH_ID_RE = re.compile(r"(?:^|[-_/])([dD]\d+|\d{3,})(?:$|[-_/])")


def _norm_id(raw: str) -> str:
    """Normalize an id token to '#D52' / '#1626' form (uppercase, no spaces)."""
    t = raw.strip().lstrip("#").upper()
    return "#" + t if t else ""


# A board ticket sitting in one of these lanes is EXPECTED-archived: done work
# that was legitimately closed out (often before the PR loop, or non-dev intake
# like docs/decisions) belongs here and is NOT a disagreement. board_ticket_
# untracked only fires for a ticket checked done while still parked in an ACTIVE
# lane — that's the real inconsistency (marked done but never reconciled/moved).
_ARCHIVED_LANES = ("completed", "done", "shipped", "archived", "archive", "closed")


def _is_archived_lane(lane: str) -> bool:
    l = (lane or "").strip().lower()
    return any(l.startswith(a) for a in _ARCHIVED_LANES)


def ids_in_text(text: str) -> set:
    """All ticket ids mentioned in free text (titles, bodies, queue sources)."""
    if not text:
        return set()
    return {_norm_id(m) for m in _ID_RE.findall(text)}


def ids_in_branch(branch: str) -> set:
    """Ticket ids embedded in a branch name, e.g. 'stuart/d40-read-file' -> {#D40}.
    Bare numeric-only segments are ignored (too loose to be a reliable id)."""
    return {_norm_id(seg) for seg in _BRANCH_ID_RE.findall(branch or "")}


# =============================================================================
# reconcile() — THE PRODUCT. Pure function, unit-tested hardest.
# =============================================================================

def reconcile(board: dict, queue: dict, github: dict) -> list:
    """Diff the three owned-state sources and return typed mismatches.

    Args are the same normalized dicts the fetchers below produce (and the
    tests pass in by hand):
      board  = {"tickets": [{"id","title","lane","done"}], ...}
      queue  = {"items":   [{"id","source","title","status","pr_url"}], ...}
      github = {"repos":   [{"repo","open_prs":[{"number","title","head","body"}],
                             "merged":[{...}], ...}], ...}

    Mismatch types (each: {type, id?, title, detail, sources:[...]}):
      queue_done_no_pr      — a queue row marked done with no PR anywhere.
      pr_open_not_in_queue  — an open PR no queue row references.
      board_ticket_untracked— a board ticket marked DONE that neither the queue
                              nor GitHub can confirm (the fabrication-class trap).

    Non-done board tickets absent from the queue are NORMAL intake, not a
    mismatch — the board is a pre-sort inbox.
    """
    mismatches = []

    queue_items = (queue or {}).get("items", []) or []
    repos = (github or {}).get("repos", []) or []
    board_tickets = (board or {}).get("tickets", []) or []

    # --- Index queue by referenced ids + collect its PR urls ---
    queue_ids = set()
    queue_pr_urls = set()
    for it in queue_items:
        queue_ids |= ids_in_text(it.get("source", ""))
        queue_ids |= ids_in_text(it.get("title", ""))
        if it.get("pr_url"):
            queue_pr_urls.add((it.get("pr_url") or "").strip())

    # --- Index GitHub PRs (open + merged) by referenced ids ---
    open_pr_ids = set()
    merged_pr_ids = set()
    open_prs = []          # (repo, pr) for the not-in-queue check
    for rp in repos:
        rname = rp.get("repo", "")
        for pr in rp.get("open_prs", []) or []:
            refs = ids_in_text(pr.get("title", "")) | ids_in_text(pr.get("body", "")) \
                   | ids_in_branch(pr.get("head", ""))
            pr = {**pr, "_ids": refs}
            open_pr_ids |= refs
            open_prs.append((rname, pr))
        for pr in rp.get("merged", []) or []:
            merged_pr_ids |= ids_in_text(pr.get("title", "")) \
                | ids_in_text(pr.get("body", "")) | ids_in_branch(pr.get("head", ""))

    all_pr_ids = open_pr_ids | merged_pr_ids

    # 1) queue_done_no_pr — done in the queue, but no PR proves it.
    for it in queue_items:
        status = (it.get("status") or "").strip().lower()
        if status not in ("done", "merged"):
            continue
        item_ids = ids_in_text(it.get("source", "")) | ids_in_text(it.get("title", ""))
        has_pr = bool(it.get("pr_url")) or bool(item_ids & all_pr_ids)
        if not has_pr:
            mismatches.append({
                "type": "queue_done_no_pr",
                "id": next(iter(item_ids), None),
                "title": it.get("title", ""),
                "detail": (f"Queue item [{it.get('id')}] '{it.get('title','')}' is "
                           f"marked {status} but no PR (open or merged) references it."),
                "sources": ["db:steward_queue", "github"],
            })

    # 2) pr_open_not_in_queue — an open PR the queue doesn't track.
    for rname, pr in open_prs:
        refs = pr.get("_ids", set())
        tracked = bool(refs & queue_ids) or (pr.get("html_url", "") in queue_pr_urls)
        if not tracked:
            mismatches.append({
                "type": "pr_open_not_in_queue",
                "id": next(iter(refs), None),
                "title": pr.get("title", ""),
                "detail": (f"Open PR {rname}#{pr.get('number')} "
                           f"'{pr.get('title','')}' (head {pr.get('head','?')}) "
                           f"has no steward_queue row tracking it."),
                "sources": ["github", "db:steward_queue"],
            })

    # 3) board_ticket_untracked — a ticket checked done while STILL in an active
    #    lane, with nothing in the queue or GitHub to confirm it. Tickets already
    #    moved to an archived lane (Completed/Done/...) are expected-closed and
    #    are NOT flagged — the whole Completed archive is not a pile of
    #    disagreements. The signal here is "marked done but never reconciled".
    for t in board_tickets:
        if not t.get("done"):
            continue
        if _is_archived_lane(t.get("lane", "")):
            continue
        tid = _norm_id(t.get("id", "")) if t.get("id") else ""
        ids = {tid} if tid else ids_in_text(t.get("title", ""))
        if not ids:
            continue
        confirmed = bool(ids & (queue_ids | all_pr_ids))
        if not confirmed:
            mismatches.append({
                "type": "board_ticket_untracked",
                "id": tid or next(iter(ids), None),
                "title": t.get("title", ""),
                "detail": (f"Board ticket {tid or t.get('title','')} is checked done "
                           f"but still in active lane '{t.get('lane','?')}', and "
                           f"neither the queue nor GitHub confirms it — move it to "
                           f"Completed or reconcile it."),
                "sources": ["nc:jules-backlog.md", "db:steward_queue", "github"],
            })

    return mismatches


# =============================================================================
# INTAKE — parse the board text (honors #D43: every '## Lane' is its own group)
# =============================================================================

def parse_board(text: str) -> list:
    """Parse jules-backlog markdown into a flat ticket list, preserving EVERY
    lane header as its own group (#D43: INTERACTIVE/BLOCKED are never folded into
    NOW). Returns [{id, title, lane, done, raw}]."""
    tickets = []
    lane = None
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("## "):
            lane = s[3:].strip()
            continue
        if lane and (s.startswith("- [ ] ") or s.startswith("- [x] ")):
            done = s.startswith("- [x] ")
            rest = s[6:]
            ids = ids_in_text(rest)
            tid = next(iter(ids), "")
            title = re.sub(r"\s*`\[\w+\]`", "", rest).replace("**", "").strip()
            title = re.sub(r"\*\((.+?)\)\*\s*$", "", title).strip().rstrip(".")
            tickets.append({
                "id": tid, "title": title[:200], "lane": lane, "done": done,
            })
    return tickets


async def _fetch_intake() -> dict:
    """INTAKE column via Companion C's _board_get (cross-scope + 423 resilience).
    A failing board is 'unavailable', never a silently-empty column."""
    try:
        from src.tools.backlog_tools import _board_get
        text, label = await _board_get()
        return {"tickets": parse_board(text), "source": label,
                "fetched_at": time.time(), "error": None}
    except Exception as e:
        return {"tickets": [], "source": "nc:jules-backlog.md",
                "fetched_at": time.time(), "error": str(e)[:200]}


# =============================================================================
# QUEUE — steward_queue + recent approval_requests (redacted)
# =============================================================================

async def _fetch_queue() -> dict:
    """QUEUE column: open steward_queue rows + recently resolved approval_requests
    with credential-path redaction (Companion B) on the result excerpts."""
    from src.tools.system_tools import redact_credentials  # Companion B
    out = {"items": [], "approvals": [], "source": "db:steward_queue",
           "fetched_at": time.time(), "error": None}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT id, source, title, status, assignee, pr_url, updated_at "
                "FROM steward_queue ORDER BY (status IN ('done','dropped')), "
                "updated_at DESC LIMIT 60")
            for row in await r.fetchall():
                out["items"].append({
                    "id": row["id"], "source": row["source"], "title": row["title"],
                    "status": row["status"], "assignee": row["assignee"],
                    "pr_url": row["pr_url"],
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else "",
                })
            try:
                r = await conn.execute(
                    "SELECT request_id, tool_name, status, result, resolved_at "
                    "FROM approval_requests WHERE status IN ('approved','denied') "
                    "ORDER BY resolved_at DESC LIMIT 10")
                for row in await r.fetchall():
                    out["approvals"].append({
                        "request_id": row["request_id"],
                        "tool_name": row["tool_name"],
                        "status": row["status"],
                        "result": redact_credentials((row["result"] or "")[:200]),
                        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else "",
                    })
            except Exception:
                pass  # approval_requests may not be migrated — queue still renders
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


# =============================================================================
# GITHUB — REST only, cached, degrade gracefully. NEVER shell git.
# =============================================================================

_gh_cache = {"at": 0.0, "data": None}


def _github_owner_token():
    """(owner, token) shared with create_github_pr's source (Companion A path):
    resolve the local clone's origin for the owner, token from GH_TOKEN chain.
    Owner falls back to env/LucidPrinciples so the column still renders."""
    owner, token = None, ""
    try:
        from src.tools.dev_tools import _resolve_repo, _github_repo_and_token, _github_token
        repo_dir = _resolve_repo("lucid-cove")
        if repo_dir and not str(repo_dir).startswith("Error"):
            rt = _github_repo_and_token(repo_dir)
            if rt:
                slug, tok = rt
                owner = slug.split("/")[0]
                token = tok
        if not token:
            token = _github_token()
    except Exception:
        pass
    owner = owner or env("OPS_GITHUB_OWNER", "LucidPrinciples")
    return owner, token


async def _gh_get(client, url):
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def _fetch_github() -> dict:
    """GITHUB column: per repo — open PRs, last 10 merged, non-main branches,
    main tip sha+time. 60s cache; 5s per-call timeout; a failing repo carries its
    own error and does not blank the column."""
    now = time.time()
    if _gh_cache["data"] is not None and (now - _gh_cache["at"]) < _GH_CACHE_TTL:
        return _gh_cache["data"]

    owner, token = _github_owner_token()
    repo_names = [x.strip() for x in env("OPS_GITHUB_REPOS", ",".join(_DEFAULT_REPOS)).split(",") if x.strip()]
    out = {"repos": [], "owner": owner, "fetched_at": now,
           "error": None if token else "No GitHub token configured"}
    if not token:
        _gh_cache.update(at=now, data=out)
        return out

    import httpx
    from src.utils.github import _headers
    headers = _headers(token)
    async with httpx.AsyncClient(timeout=_GH_TIMEOUT, headers=headers) as client:
        for name in repo_names:
            slug = f"{owner}/{name}"
            rp = {"repo": name, "slug": slug, "open_prs": [], "merged": [],
                  "branches": [], "main_sha": None, "main_at": None, "error": None}
            try:
                pulls = await _gh_get(client, f"https://api.github.com/repos/{slug}/pulls?state=open&per_page=30")
                for pr in pulls:
                    rp["open_prs"].append({
                        "number": pr.get("number"), "title": pr.get("title", ""),
                        "head": (pr.get("head") or {}).get("ref", ""),
                        "author": (pr.get("user") or {}).get("login", ""),
                        "mergeable_state": pr.get("mergeable_state", ""),
                        "html_url": pr.get("html_url", ""),
                        "body": (pr.get("body") or "")[:500],
                    })
                closed = await _gh_get(client, f"https://api.github.com/repos/{slug}/pulls?state=closed&per_page=20&sort=updated&direction=desc")
                merged = [pr for pr in closed if pr.get("merged_at")][:10]
                for pr in merged:
                    rp["merged"].append({
                        "number": pr.get("number"), "title": pr.get("title", ""),
                        "head": (pr.get("head") or {}).get("ref", ""),
                        "merged_at": pr.get("merged_at", ""),
                        "html_url": pr.get("html_url", ""),
                        "body": (pr.get("body") or "")[:300],
                    })
                branches = await _gh_get(client, f"https://api.github.com/repos/{slug}/branches?per_page=50")
                rp["branches"] = [b.get("name") for b in branches if b.get("name") not in ("main", "master")]
                main = await _gh_get(client, f"https://api.github.com/repos/{slug}/commits/main")
                rp["main_sha"] = (main.get("sha") or "")[:7]
                rp["main_at"] = ((main.get("commit") or {}).get("committer") or {}).get("date", "")
            except Exception as e:
                rp["error"] = str(e)[:160]
            out["repos"].append(rp)

    _gh_cache.update(at=now, data=out)
    return out


# =============================================================================
# VAULT — read-only Memory.md top + newest handoff (founder-only if mounted)
# =============================================================================

def _vault_dir() -> Path | None:
    """The vault path if this Cove has one mounted (founder). None otherwise —
    the column feature-flags off cleanly on Coves without a vault."""
    for cand in (env("LP_VAULT_DIR", ""), env("VAULT_DIR", "")):
        if cand:
            p = Path(cand)
            lp = p / "LP-Vault"
            if lp.exists():
                return lp
            if (p / "Memory.md").exists():
                return p
    # Common founder mount.
    default = Path("/vault/LP-Vault")
    return default if default.exists() else None


async def _fetch_vault() -> dict:
    out = {"memory": None, "handoff": None, "source": "vault", "available": False,
           "fetched_at": time.time(), "error": None}
    try:
        vd = _vault_dir()
        if vd is None:
            out["error"] = "No vault mounted (feature off for this Cove)."
            return out
        out["available"] = True
        mem = vd / "Memory.md"
        if mem.exists():
            lines = mem.read_text(encoding="utf-8", errors="replace").split("\n")
            out["memory"] = {"text": "\n".join(lines[:_VAULT_MEMORY_LINES]),
                             "path": "vault:Memory.md",
                             "total_lines": len(lines)}
        handoffs = sorted(vd.glob("handoff-*.md"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        if handoffs:
            h = handoffs[0]
            htext = h.read_text(encoding="utf-8", errors="replace")
            out["handoff"] = {"text": htext[:6000], "path": f"vault:{h.name}",
                              "truncated": len(htext) > 6000}
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


# =============================================================================
# Assembly
# =============================================================================

async def _fetch_watcher_open_count() -> int | None:
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT COUNT(*) AS n FROM watcher_alerts WHERE status='open'")
            row = await r.fetchone()
            return row["n"] if row else 0
    except Exception:
        return None


@router.get("/api/ops/state")
async def ops_state(request: Request):
    """The single JSON assembly — every source with its own fetched_at + error.
    A failing source renders 'unavailable', never empty-equals-fine."""
    if not await _require_operator(request):
        return JSONResponse({"error": "Admin/operator only."}, status_code=403)

    intake = await _fetch_intake()
    queue = await _fetch_queue()
    github = await _fetch_github()
    vault = await _fetch_vault()
    watcher_open = await _fetch_watcher_open_count()

    mismatches = reconcile(
        {"tickets": intake.get("tickets", [])},
        {"items": queue.get("items", [])},
        {"repos": github.get("repos", [])},
    )

    return {
        "ok": True,
        "generated_at": time.time(),
        "intake": intake,
        "queue": queue,
        "github": github,
        "vault": vault,
        "header": {
            "watcher_open": watcher_open,
            "repos": [{"repo": r["repo"], "main_sha": r.get("main_sha"),
                       "error": r.get("error")} for r in github.get("repos", [])],
        },
        "mismatches": mismatches,
    }


@router.get("/ops")
async def serve_ops_page():
    """Serve the Ops-Visibility page."""
    static = Path(__file__).parent.parent / "static" / "action-board" / "ops-visibility.html"
    if not static.exists():
        return HTMLResponse("Ops-Visibility page not found", status_code=404)
    return HTMLResponse(static.read_text(encoding="utf-8"))
