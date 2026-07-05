# =============================================================================
# carry_import.py — carry-everything on upgrade (CF-65).
# =============================================================================
# When an operator upgrades from a hub app account to their own Cove, identity
# and economy edges already carry (handle-keyed on the hub, registry stays
# master). Their personal tuning practice did NOT: tuning_sessions /
# tuning_streaks / tuning_preferences live only in the hub DB, so a fresh
# Cove's Tune tab started blank. This module closes that gap, seamlessly —
# the operator does nothing; on connect their history just appears.
#
#   HUB side (LP_REGISTRY_MASTER): POST /api/registry/carry-export
#     - auth: X-Operator-Token ONLY. The owner exports their own data; the
#       fleet secret is deliberately NOT accepted here (it cannot identify
#       WHOSE data to export, and the fleet has no business bulk-reading an
#       operator's journal history).
#     - paged by tuning_sessions.id (`after` cursor, batches of <= 500).
#     - the first page (after=0) also carries the tuning_preferences row and
#       the account-preferences keep-list keys.
#
#   COVE side: import_carry(presence_id, token)
#     - pulls pages; INSERT ... ON CONFLICT (session_id) DO NOTHING
#       (session_id is UNIQUE — natural idempotency key; re-running skips all),
#     - explicit column INTERSECTION with the local schema (hub and Cove can be
#       on different migration versions; unknown columns are dropped, counted),
#     - RECOMPUTES the streak from the sessions now present locally
#       (self-correcting — never copies the hub streak row),
#     - merges account-preferences keep-list keys only where locally absent,
#     - progress readable at GET /api/onboarding/carry-status
#       ("Bringing your history over… ✓ N tunings").
#
# Wired from onboarding.connect_operator as a background task — the identity
# connect NEVER blocks or fails because carry failed — and from a first-boot
# sweep (dashboard app lifespan) for provisioned/hosted Coves that already
# hold an operator token but have an empty local history.
# =============================================================================
import asyncio
import hashlib
import json
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.env import env, env_bool
from src.utils.carry import (
    ACCOUNT_PREFS_KEEP,
    EXCLUDED_COLUMNS as _EXCLUDED_COLUMNS,
    intersect_columns,
    jsonable as _jsonable,
    streak_from_date_counts,
)

log = logging.getLogger(__name__)
router = APIRouter()

_PAGE_SIZE = 500

# In-memory progress per local presence id — enough for the connect-panel UI.
_STATUS: dict = {}


def _hash_token(token: str) -> str:
    """Match the accounts.auth_token storage scheme (sha256 of the raw token)."""
    return hashlib.sha256(token.encode()).hexdigest()


# ─── Hub side: the owner exports their own practice data ─────────────────────

@router.post("/api/registry/carry-export")
async def carry_export(request: Request):
    """Export the calling operator's tuning practice (sessions, paged, plus
    preferences + keep-list on the first page). Registry-master only."""
    if not env_bool("LP_REGISTRY_MASTER"):
        raise HTTPException(501, "Not the registry master")
    tok = (request.headers.get("X-Operator-Token", "") or "").strip()
    if not tok:
        raise HTTPException(403, "carry-export requires X-Operator-Token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        after = int(body.get("after") or 0)
    except Exception:
        after = 0
    try:
        limit = max(1, min(int(body.get("limit") or _PAGE_SIZE), _PAGE_SIZE))
    except Exception:
        limit = _PAGE_SIZE

    from src.memory.database import get_db
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT id, username FROM accounts WHERE auth_token = %s AND active = TRUE",
            (_hash_token(tok),))
        acct = await r.fetchone()
        if not acct:
            raise HTTPException(403, "Invalid operator token")
        aid = acct["id"]
        r = await conn.execute(
            "SELECT * FROM tuning_sessions WHERE presence_id = %s AND id > %s "
            "ORDER BY id ASC LIMIT %s", (aid, after, limit))
        rows = [dict(x) for x in await r.fetchall()]
        out = {
            "ok": True,
            "handle": (acct.get("username") or ""),
            "sessions": [_jsonable(x) for x in rows],
            "count": len(rows),
            "next_after": (rows[-1]["id"] if rows else None),
        }
        if after == 0:
            r = await conn.execute(
                "SELECT excluded_signal_types, preferred_frequency, top_frequency, "
                "last_principle FROM tuning_preferences WHERE presence_id = %s", (aid,))
            pref = await r.fetchone()
            out["tuning_preferences"] = _jsonable(dict(pref)) if pref else None
            r = await conn.execute(
                "SELECT preferences FROM accounts WHERE id = %s", (aid,))
            arow = await r.fetchone()
            blob = (arow or {}).get("preferences") or {}
            if isinstance(blob, str):
                try:
                    blob = json.loads(blob)
                except Exception:
                    blob = {}
            out["preferences_keep"] = {k: blob[k] for k in ACCOUNT_PREFS_KEEP if k in blob}
            # Observability only — key NAMES present in the blob (never values),
            # so the keep-list can be reviewed against what real accounts hold.
            out["preferences_keys_present"] = sorted(blob.keys()) if isinstance(blob, dict) else []
        return out


# ─── Cove side: pull + import ─────────────────────────────────────────────────

async def _fetch_page(base: str, token: str, after: int) -> dict:
    import httpx
    headers = {"Content-Type": "application/json",
               "X-Operator-Token": token,
               # Cloudflare fronting the hub blocks default library UAs.
               "User-Agent": "LucidCove-Cove/1.0"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(base + "/api/registry/carry-export",
                                     headers=headers, json={"after": after})
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return {"ok": False,
                    "reason": (data.get("detail") if isinstance(data, dict) else None)
                    or f"carry-export HTTP {resp.status_code}"}
        return data if isinstance(data, dict) else {"ok": False, "reason": "bad response"}
    except Exception as e:
        return {"ok": False, "reason": f"hub unreachable: {str(e)[:120]}"}


async def _recompute_streak(conn, presence_id) -> dict:
    """Recompute tuning_streaks for a presence from its local sessions and
    upsert the row. Self-correcting whether sessions came from import or live."""
    r = await conn.execute(
        "SELECT date, COUNT(*) AS n FROM tuning_sessions "
        "WHERE presence_id = %s AND date IS NOT NULL GROUP BY date", (presence_id,))
    rows = await r.fetchall()
    counts = {row["date"]: row["n"] for row in rows}
    vals = streak_from_date_counts(counts, date.today().isoformat())
    await conn.execute(
        """INSERT INTO tuning_streaks (presence_id, current_streak, longest_streak,
               last_tuning_date, total_sessions, this_month_sessions, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())
           ON CONFLICT (presence_id) DO UPDATE SET
               current_streak = EXCLUDED.current_streak,
               longest_streak = GREATEST(tuning_streaks.longest_streak, EXCLUDED.longest_streak),
               last_tuning_date = EXCLUDED.last_tuning_date,
               total_sessions = EXCLUDED.total_sessions,
               this_month_sessions = EXCLUDED.this_month_sessions,
               updated_at = NOW()""",
        (presence_id, vals["current_streak"], vals["longest_streak"],
         vals["last_tuning_date"], vals["total_sessions"], vals["this_month_sessions"]))
    return vals


async def import_carry(presence_id: str, token: str) -> dict:
    """Pull the operator's practice data from the hub into THIS Cove for the
    given local presence. Idempotent; safe to re-run. Returns a summary dict."""
    base = (env("LP_REGISTRY_URL") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "reason": "LP_REGISTRY_URL not set"}
    if not token:
        return {"ok": False, "reason": "no operator token"}

    from src.memory.database import get_db
    inserted = skipped = 0
    partial = False
    dropped_cols: list = []
    first_page: dict = {}
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tuning_sessions'")
        local_cols = {row["column_name"] for row in await r.fetchall()}

        after = 0
        while True:
            page = await _fetch_page(base, token, after)
            if not page.get("ok"):
                if after == 0:
                    return {"ok": False, "reason": page.get("reason") or "export failed"}
                # Partial import: idempotency lets the next run resume — but that next
                # run only happens if we RECORD the partial state (C3-4: it used to be
                # marked done and never resumed, silently truncating history forever).
                log.warning("carry-export page failed mid-import: %s", page.get("reason"))
                partial = True
                break
            if after == 0:
                first_page = page
            sessions = page.get("sessions") or []
            if sessions and not dropped_cols:
                dropped_cols = sorted(k for k in sessions[0]
                                      if k not in local_cols and k not in _EXCLUDED_COLUMNS)
            for srow in sessions:
                if not srow.get("session_id"):
                    continue
                cols = intersect_columns(srow.keys(), local_cols)
                if not cols:
                    continue
                sql = ("INSERT INTO tuning_sessions (presence_id, "
                       + ", ".join(cols) + ") VALUES (" + ", ".join(["%s"] * (len(cols) + 1))
                       + ") ON CONFLICT (session_id) DO NOTHING")
                r = await conn.execute(sql, (presence_id, *[srow[c] for c in cols]))
                if getattr(r, "rowcount", 0):
                    inserted += 1
                else:
                    skipped += 1
                st = _STATUS.get(str(presence_id))
                if st and st.get("state") == "running":
                    st["sessions"] = inserted
            nxt = page.get("next_after")
            if not sessions or not nxt:
                break
            after = int(nxt)

        # Preferences row: only where locally absent (local practice wins).
        pref = first_page.get("tuning_preferences")
        prefs_applied = False
        if isinstance(pref, dict):
            r = await conn.execute(
                """INSERT INTO tuning_preferences (presence_id, excluded_signal_types,
                       preferred_frequency, top_frequency, last_principle, updated_at)
                   VALUES (%s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (presence_id) DO NOTHING""",
                (presence_id, pref.get("excluded_signal_types") or "",
                 pref.get("preferred_frequency"), pref.get("top_frequency"),
                 pref.get("last_principle")))
            prefs_applied = bool(getattr(r, "rowcount", 0))

        # Account-preferences keep-list: merge keys only where locally absent.
        keep = first_page.get("preferences_keep") or {}
        if isinstance(keep, dict) and keep:
            r = await conn.execute(
                "SELECT preferences FROM accounts WHERE id = %s", (presence_id,))
            arow = await r.fetchone()
            blob = (arow or {}).get("preferences") or {}
            if isinstance(blob, str):
                try:
                    blob = json.loads(blob)
                except Exception:
                    blob = {}
            add = {k: v for k, v in keep.items() if k not in blob}
            if add:
                blob.update(add)
                await conn.execute(
                    "UPDATE accounts SET preferences = %s, updated_at = NOW() WHERE id = %s",
                    (json.dumps(blob), presence_id))

        streak = await _recompute_streak(conn, presence_id)

    # C3-4: persist the partial/complete state so a truncated import is re-run
    # (first_boot_carry checks this flag past its "rows exist" guard). Best-effort —
    # a missing system_settings table just means the old behavior.
    try:
        from src.utils.settings import update_setting
        await update_setting(f"carry_partial_{presence_id}", "1" if partial else "")
    except Exception:
        pass

    return {"ok": True, "partial": partial, "sessions": inserted, "skipped": skipped,
            "dropped_columns": dropped_cols, "prefs": prefs_applied,
            "streak": streak.get("current_streak", 0),
            "total_sessions": streak.get("total_sessions", 0),
            "handle": first_page.get("handle") or ""}


async def run_carry(presence_id: str, token: str, _preclaimed: bool = False) -> dict:
    """One carry run with _STATUS bookkeeping. Never raises. Awaitable so the
    standup ladder can see the outcome; start_carry wraps it fire-and-forget
    (with _preclaimed=True — it already set the running status SYNCHRONOUSLY so
    the wizard's first status poll never races the task startup, run-2 2.4)."""
    pid = str(presence_id)
    if not _preclaimed:
        st = _STATUS.get(pid)
        if st and st.get("state") == "running":
            return {"ok": False, "reason": "already running"}
        _STATUS[pid] = {"state": "running", "sessions": 0}
    try:
        res = await import_carry(pid, token)
        if res.get("ok"):
            _STATUS[pid] = {"state": "done", **res}
            log.info("carry-import done for %s: %s sessions (+%s already present)%s",
                     pid, res.get("sessions"), res.get("skipped"),
                     " — PARTIAL, will re-run" if res.get("partial") else "")
        else:
            _STATUS[pid] = {"state": "error", "reason": res.get("reason") or "failed"}
        return res
    except Exception as e:  # never let carry break anything around it
        log.warning("carry-import failed for %s: %s", pid, e)
        _STATUS[pid] = {"state": "error", "reason": str(e)[:200]}
        return {"ok": False, "reason": str(e)[:200]}


def start_carry(presence_id: str, token: str) -> bool:
    """Fire-and-forget background carry for a presence. Never raises. Returns
    False when a run is already in flight (or no event loop is running)."""
    pid = str(presence_id)
    st = _STATUS.get(pid)
    if st and st.get("state") == "running":
        return False
    # Claim the running status HERE, synchronously — the wizard polls
    # /api/onboarding/carry-status right after this returns, and an unstarted
    # task would read as "idle" and end the watcher (run-2 2.4).
    _STATUS[pid] = {"state": "running", "sessions": 0}
    try:
        asyncio.get_running_loop().create_task(run_carry(pid, token, _preclaimed=True))
        return True
    except RuntimeError:
        _STATUS.pop(pid, None)
        return False


async def first_boot_carry() -> dict:
    """Hosted/provisioned path: this Cove already holds an operator token (env
    or cove.yaml). If the local operator's tuning history is EMPTY — or the last
    import recorded itself PARTIAL (C3-4) — pull it. Self-guards completely: no
    token, no hub, registry master, or complete existing history all mean no-op.
    Returns {"settled": bool, ...} so the standup retry ladder (C3-3) knows
    whether to try again; settled means "no retry will ever help or be needed"."""
    try:
        if env_bool("LP_REGISTRY_MASTER"):
            return {"settled": True, "skip": "registry master"}
        if not (env("LP_REGISTRY_URL") or "").strip():
            return {"settled": True, "skip": "no hub"}
        from src.dashboard.routes.registry_client import _operator_token
        token = _operator_token()
        if not token:
            return {"settled": True, "skip": "no operator token"}
        from src.memory.database import get_db
        async with get_db() as conn:
            # The founding operator: the human admin account (no agent identity).
            r = await conn.execute(
                "SELECT id FROM accounts WHERE cove_role = 'admin' "
                "AND agent_identity IS NULL AND username IS NOT NULL "
                "ORDER BY created_at ASC LIMIT 1")
            op = await r.fetchone()
            if not op:
                return {"settled": True, "skip": "no admin account yet"}
            pid = str(op["id"])
            r = await conn.execute(
                "SELECT 1 FROM tuning_sessions WHERE presence_id = %s LIMIT 1", (pid,))
            has_rows = bool(await r.fetchone())
        if has_rows:
            # Rows alone used to end it here — a truncated import looked "done".
            from src.utils.settings import get_setting
            if (await get_setting(f"carry_partial_{pid}", default="")) != "1":
                return {"settled": True, "skip": "history already present"}
            log.info("first-boot carry: last import was partial — re-running for %s", pid)
        res = await run_carry(pid, token)
        if res.get("ok") and not res.get("partial"):
            return {"settled": True, "sessions": res.get("sessions", 0)}
        return {"settled": False, "reason": res.get("reason") or "partial import"}
    except Exception as e:
        log.debug("first-boot carry attempt failed: %s", e)
        return {"settled": False, "reason": str(e)[:200]}


# ─── Status for the connect-panel UI ─────────────────────────────────────────

@router.get("/api/onboarding/carry-status")
async def carry_status(request: Request):
    """Progress of the background history carry for the current presence."""
    from src.dashboard.routes.presence import get_current_presence
    p = await get_current_presence(request)
    if not p or not p.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    st = _STATUS.get(str(p["id"]))
    return {"ok": True, **(st or {"state": "idle"})}
