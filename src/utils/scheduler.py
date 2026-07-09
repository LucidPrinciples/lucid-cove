"""
Agent Scheduler — config-driven daily protocols.

ARCHITECTURE: cove-core owns the base schedule. Overlays EXTEND, never replace.

Base schedule (all agents get this automatically):
  00:00 — Log cleanup
  00:05 — Memory auto-commit (7-day review window)
  01:00 — Memory consolidation (dedup + prune)
  02:00 Sun — Memory synthesis (weekly pattern extraction)
  03:00 Sun — Weekly backup (git + DB)
  06:00 — LT tuning package pull (product delivery — every Cove, not team agents)
  every 15m — YouTube queue processor (if YOUTUBE_CLIENT_ID set)

Morning tuning is NEVER scheduled independently. All tuning flows through
the orchestration chain: Socrates triggers stewards via POST /api/system/ltp-trigger,
stewards dispatch to team agents. Team agents (STEWARD_DATABASE_URL set)
also skip the 06:30 pull — they receive everything from the steward.

Overlays add agent-specific jobs by overriding setup_agent_schedule().
They NEVER override setup_schedule() or duplicate base jobs.

    class StuartScheduler(AgentScheduler):
        def setup_agent_schedule(self):
            # Only Stuart-specific jobs here
            schedule.every().day.at("07:30", self._tz_key).do(...)

Usage:
    from src.utils.scheduler import AgentScheduler
    scheduler = AgentScheduler()
    await scheduler.run()
"""

import asyncio
import os
from src.env import env
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.utils.time_utils import ts_log, now_utc, today_app

import schedule


# =========================================================================
# Protocol Run Logging
# =========================================================================

async def _log_run_start(protocol: str, thread_id: str) -> int:
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """INSERT INTO protocol_runs (protocol, status, thread_id, triggered_by)
                   VALUES (%s, 'running', %s, 'cron') RETURNING id""",
                (protocol, thread_id)
            )
            row = await result.fetchone()
            await conn.commit()
            return row["id"] if row else 0
    except Exception:
        return 0


async def _log_run_finish(run_id: int, status: str, duration_ms: int, error_msg: str = None):
    if not run_id:
        return
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """UPDATE protocol_runs
                   SET status = %s, finished_at = NOW(), duration_ms = %s, error_msg = %s
                   WHERE id = %s""",
                (status, duration_ms, error_msg, run_id)
            )
            await conn.commit()
    except Exception:
        pass


# =========================================================================
# Scheduler class
# =========================================================================

class AgentScheduler:
    """Manages an agent's daily schedule. Baseline for all family agents."""

    def __init__(self):
        from src.utils.time_utils import app_tz
        self.timezone = app_tz()
        self._tz_key = self.timezone.key
        self._running = False
        self._loop = None
        # Display name from env (for logs/thread IDs)
        self._agent_id = env("AGENT_ID", "agent")
        # Canonical DB identity from agent.yaml (for echoes, state, etc.)
        from src.config import get_primary_agent_id
        self._db_agent_id = get_primary_agent_id()

    def _now(self) -> datetime:
        return datetime.now(self.timezone)

    async def _run_ltp_morning(self):
        """Run the daily LTP morning reflection protocol.

        In multi mode (COVE_MODE=multi), this container doesn't run its own
        LTP graph — it just pulls the latest tuning package from LT via the
        receiver. No LLM calls, no echo compose, no team dispatch. The
        package is cached and served by the tuning/mirrors API routes.

        In single mode (default), runs the full LTP graph: select frequency →
        compose echo → store → process record → team dispatch → update state.
        """
        cove_mode = env("COVE_MODE", "single")

        # Multi mode: just pull the tuning package, no LLM work
        if cove_mode == "multi":
            await self._run_ltp_pull_only()
            return

        now = self._now()
        thread_id = f"{self._agent_id}-ltp-{now.strftime('%Y-%m-%d')}"
        protocol = "ltp-morning"

        print(f"{ts_log()} [scheduler] [{now.strftime('%I:%M %p')}] Running LTP morning reflection...")

        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()

        try:
            from src.graphs.ltp import build_ltp_graph
            from src.memory.checkpointer import get_checkpointer

            async with get_checkpointer() as checkpointer:
                graph = build_ltp_graph().compile(checkpointer=checkpointer)
                result = await graph.ainvoke(
                    {"messages": [], "agent_id": self._db_agent_id, "protocol": protocol},
                    config={"configurable": {"thread_id": thread_id}},
                )

            freq = result.get("frequency", "?")
            echo_num = result.get("echo_num", "?")
            dur = int((time.time() - started) * 1000)

            print(f"{ts_log()} [scheduler] LTP morning complete — Echo #{echo_num}, frequency: {freq}")
            await _log_run_finish(run_id, "success", dur)

        except Exception as e:
            tb = traceback.format_exc()
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] ERROR LTP morning failed: {e}")
            error_detail = f"{e}\n\n--- traceback ---\n{tb}"[:1000]
            await _log_run_finish(run_id, "error", dur, error_detail)
            # Push failure notification to MC dashboard
            try:
                from src.tools.approval import _notification_queue
                _notification_queue.append({
                    "tier": "error",
                    "tool": "ltp-morning",
                    "args": {"error": str(e)[:200], "thread_id": thread_id},
                    "timestamp": now_utc().isoformat(),
                    "message": f"LTP morning tuning FAILED: {e}",
                })
            except Exception:
                pass  # Don't let notification failure mask the original error

    async def _run_ltp_pull_only(self):
        """Pull-only LTP for multi mode containers.

        No LLM, no compose, no echo storage. Just git pull the latest
        tuning package from LT so the API routes serve fresh data.
        """
        print(f"{ts_log()} [scheduler] [{self._now().strftime('%I:%M %p')}] "
              f"Pulling tuning package (multi mode — pull only, no LLM)...")

        started = time.time()
        run_id = await _log_run_start("ltp-pull", f"pull-{today_app()}")

        try:
            from src.tuning.receiver import get_todays_tuning
            package = await get_todays_tuning(force_pull=True)

            dur = int((time.time() - started) * 1000)

            if package:
                print(f"{ts_log()} [scheduler] Tuning package pulled: "
                      f"{package.frequency} — {package.principle} "
                      f"(LT Echo #{package.lt_echo_num}, date: {package.date})")
                await _log_run_finish(run_id, "success", dur)
            else:
                print(f"{ts_log()} [scheduler] No tuning package available from LT")
                await _log_run_finish(run_id, "success", dur, "No package available")

        except Exception as e:
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Tuning pull failed: {e}")
            await _log_run_finish(run_id, "error", dur, str(e)[:500])

    async def _run_tuning_sweep(self):
        """Catch-up sweep — tune any team agent or Presence missing today's echo.

        The Cove-as-Unit safety net (LTP Protocol Spec §6). On a host that hasn't
        tuned yet today this IS the morning run (nobody's tuned → everyone tunes);
        later in the day it only fills the gaps. Dedups + honors the dispatch lock,
        so it is safe to call repeatedly (06:30 schedule, boot catch-up, manual
        /api/system/tuning-sweep).
        """
        protocol = "tuning-sweep"
        run_id = await _log_run_start(protocol, f"sweep-{today_app()}")
        started = time.time()
        try:
            from src.tuning.sweep import run_cove_sweep
            result = await run_cove_sweep()
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Tuning sweep: {result.get('status')} "
                  f"(team_missing={len(result.get('team_missing', []))}, "
                  f"presences={len(result.get('presence_results', []))})")
            await _log_run_finish(run_id, "success", dur)
            return result
        except Exception as e:
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Tuning sweep failed: {e}")
            await _log_run_finish(run_id, "error", dur, str(e)[:500])
            return {"status": "error", "error": str(e)[:200]}

    async def _run_sweep_window(self):
        """The spec-§6 repeating safety sweep. Runs 07:00 → midnight local, so an
        UNATTENDED box that boots, wakes from sleep, or loses the co-located dispatch
        lock at ANY point in the day still catches up off the day's real package —
        the operator isn't watching logs, so the day must not depend on the box being
        awake at one wall-clock minute (this is open source on all kinds of sleep
        schedules). Before 07:00 it's a no-op: the Drop publishes ~05:30 and the 06:30
        self-tune runs off it; tuning earlier would land off the PREVIOUS day's Drop and
        the calendar-date dedup would then lock the whole day to that stale package.
        Inside the window run_cove_sweep dedups against today's echoes, so a settled day
        costs one cheap query per tick."""
        if self._now().hour < 7:
            return
        await self._run_tuning_sweep()

    async def _boot_catchup(self):
        """On a host, tune anyone who missed today's run while the box was down.
        Waits for the app + DB to settle, then runs the dedup-safe sweep once.

        Same pre-Drop guard as _run_sweep_window: before 07:00 this is a no-op.
        The Drop publishes ~05:30 and the 06:30 self-tune runs off it; a restart
        just after midnight (a late-night deploy, a power blip) would otherwise
        tune the whole team off the PREVIOUS day's Drop and burn the day on a
        stale package. A box awake before 07:00 is by definition awake for the
        06:30 self-tune and the 07:00+ sweep ticks, so nothing is lost."""
        await asyncio.sleep(120)
        try:
            if self._now().hour < 7:
                print(f"{ts_log()} [scheduler] Boot catch-up: before 07:00 — skipping "
                      "(pre-Drop window; the 06:30 self-tune / 07:00+ sweep handles today)")
                return
            print(f"{ts_log()} [scheduler] Boot catch-up: checking today's tuning...")
            await self._run_tuning_sweep()
        except Exception as e:
            print(f"{ts_log()} [scheduler] Boot catch-up error: {e}")

    async def _run_kb_sync(self):
        """Pull the canonical Knowledge Base from the hub into the steward's space
        (read-only mirror). The KB is the single source of truth; this writes only
        when the published version changed. Cove-level — once per Cove, not per
        team agent. The steward folder is already shared read-only to presences."""
        try:
            from src.knowledge.kb_sync import sync_kb
            result = await sync_kb()
            if result.get("synced"):
                print(f"{ts_log()} [scheduler] KB synced -> {str(result.get('version',''))[:12]} "
                      f"({len(result.get('files', []))} files)")
            elif not result.get("ok"):
                print(f"{ts_log()} [scheduler] KB sync not applied: "
                      f"{result.get('error') or result.get('skipped')}")
            # C3-2: daily backstop for the vector index — boot-time indexing can
            # fail (Ollama not up, model not pulled) with no other re-kick. The
            # populate is hash-guarded, so this is a cheap no-op when settled.
            try:
                from src.memory.knowledge import populate_knowledge_base
                await populate_knowledge_base()
            except Exception as e:
                print(f"{ts_log()} [scheduler] KB index refresh error: {e}")
        except Exception as e:
            print(f"{ts_log()} [scheduler] KB sync error: {e}")

    async def _boot_overdue_backstop(self):
        """C3-10: run schedule jobs that are overdue per protocol_runs cadence.

        Covers only jobs that LOG to protocol_runs (dedup-safe memory jobs + the
        weekly backup, which gets a jittered delay so it never lands mid-use on a
        warm reboot). A brand-new box (no protocol_runs history at all) is never
        "overdue". The tuning sweep keeps its own opt-in _boot_catchup."""
        import random
        from datetime import datetime, timezone as _utc_tz
        await asyncio.sleep(180)   # let the app + DB settle after boot
        jobs = [
            ("memory-consolidation", 36, self._run_memory_consolidation, 0),
            ("accommodation-hygiene", 204, self._run_accommodation_hygiene, 0),
            ("memory-synthesis", 204, self._run_memory_synthesis, 0),
            ("weekly-backup", 204, self._run_backup, random.randint(300, 900)),
        ]
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                r = await conn.execute("SELECT MIN(started_at) AS first_run FROM protocol_runs")
                row = await r.fetchone()
                first_run = row["first_run"] if row else None
                r = await conn.execute(
                    """SELECT protocol, MAX(COALESCE(finished_at, started_at)) AS last_ok
                       FROM protocol_runs WHERE status = 'success' GROUP BY protocol""")
                last_ok = {rw["protocol"]: rw["last_ok"] for rw in await r.fetchall()}
        except Exception as e:
            print(f"{ts_log()} [scheduler] overdue backstop skipped (DB): {e}")
            return
        if not first_run:
            return   # fresh box — the scheduled times will do their first runs

        now = datetime.now(_utc_tz.utc)

        def _hours_since(dtv):
            if dtv is None:
                return None
            if dtv.tzinfo is None:
                dtv = dtv.replace(tzinfo=_utc_tz.utc)
            return (now - dtv).total_seconds() / 3600.0

        box_age_h = _hours_since(first_run) or 0.0
        for protocol, cadence_h, runner, jitter in jobs:
            since = _hours_since(last_ok.get(protocol))
            overdue = ((since is None and box_age_h > cadence_h)
                       or (since is not None and since > cadence_h))
            if not overdue:
                continue
            print(f"{ts_log()} [scheduler] boot backstop: {protocol} overdue "
                  f"(last success {'never' if since is None else '%.0fh ago' % since}) — running")
            if jitter:
                await asyncio.sleep(jitter)
            try:
                await runner()
            except Exception as e:
                print(f"{ts_log()} [scheduler] boot backstop {protocol} failed: {e}")

    async def _retry_hub_registration(self):
        """C3-5: re-send a failed wizard-finalize hub registration (full payload,
        incl. the set-once referred_by edge) until the hub acks. No-op otherwise."""
        from src.utils.hub_retry import retry_pending_registration
        await retry_pending_registration()

    async def _cleanup_old_logs(self, keep_days: int = 7):
        """Delete log files older than keep_days."""
        log_dir = Path("/app/data/logs")
        if not log_dir.exists():
            return
        cutoff = now_utc().date() - timedelta(days=keep_days)
        deleted = []
        for f in log_dir.glob("app-*.log"):
            try:
                file_date = datetime.strptime(f.stem.replace("app-", ""), "%Y-%m-%d").date()
                if file_date < cutoff:
                    f.unlink()
                    deleted.append(f.name)
            except ValueError:
                pass
        if deleted:
            print(f"{ts_log()} [scheduler] Log cleanup — deleted {len(deleted)} file(s): {', '.join(deleted)}")

    async def _run_memory_auto_commit(self, days: int = 7):
        """Auto-commit memories older than N days that haven't been reviewed."""
        try:
            from src.memory.maintenance import auto_commit_reviewed
            count = await auto_commit_reviewed(days=days)
            if count:
                print(f"{ts_log()} [scheduler] Memory auto-commit — {count} memories committed (>{days}d)")
        except Exception as e:
            print(f"{ts_log()} [scheduler] Memory auto-commit error: {e}")

    async def _run_memory_consolidation(self):
        """Run Ezra's nightly dedup + prune pass over agent memories."""
        protocol = "memory-consolidation"
        thread_id = f"{self._agent_id}-consolidation-{self._now().strftime('%Y-%m-%d')}"

        print(f"{ts_log()} [scheduler] Running memory consolidation (dedup + prune)...")

        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()

        try:
            from src.memory.consolidation import run_memory_consolidation
            result = await run_memory_consolidation()
            dur = int((time.time() - started) * 1000)

            print(f"{ts_log()} [scheduler] Memory consolidation complete — "
                  f"{result.get('total_before', '?')} → {result.get('total_after', '?')} memories "
                  f"({result.get('duplicates_merged', 0)} merged, {result.get('stale_pruned', 0)} pruned)")

            # C3-9: backfill NULL embeddings nightly — memories stored while the
            # embed backend was down (fresh-box model pull) were invisible to
            # semantic recall forever; the backfill existed but had no schedule.
            try:
                from src.memory.maintenance import backfill_all_embeddings
                bf = await backfill_all_embeddings()
                if bf.get("embedded") or bf.get("failed"):
                    print(f"{ts_log()} [scheduler] Embedding backfill — "
                          f"{bf.get('embedded', 0)} embedded, {bf.get('failed', 0)} failed, "
                          f"{bf.get('remaining', 0)} remaining ({bf.get('agents', 0)} agents)")
            except Exception as be:
                print(f"{ts_log()} [scheduler] Embedding backfill failed: {be}")

            await _log_run_finish(run_id, "success", dur)

        except Exception as e:
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Memory consolidation failed: {e}")
            await _log_run_finish(run_id, "error", dur, str(e)[:500])

    async def _run_memory_synthesis(self):
        """Run Ezra's weekly synthesis pass — extract patterns from memory clusters."""
        protocol = "memory-synthesis"
        thread_id = f"{self._agent_id}-synthesis-{self._now().strftime('%Y-%m-%d')}"

        print(f"{ts_log()} [scheduler] Running memory synthesis (weekly pattern extraction)...")

        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()

        try:
            from src.memory.consolidation import run_full_consolidation
            result = await run_full_consolidation()
            dur = int((time.time() - started) * 1000)

            synth = result.get("synthesis", {})
            print(f"{ts_log()} [scheduler] Memory synthesis complete — "
                  f"{synth.get('clusters_found', 0)} clusters, "
                  f"{synth.get('synthesis_memories_created', 0)} new synthesis memories")
            await _log_run_finish(run_id, "success", dur)

        except Exception as e:
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Memory synthesis failed: {e}")
            await _log_run_finish(run_id, "error", dur, str(e)[:500])

    async def _run_accommodation_hygiene(self):
        """Run nightly accommodation filter — LLM scan for sycophantic patterns."""
        protocol = "accommodation-hygiene"
        thread_id = f"{self._agent_id}-hygiene-{self._now().strftime('%Y-%m-%d')}"

        print(f"{ts_log()} [scheduler] Running accommodation hygiene...")

        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()

        try:
            from src.memory.hygiene import run_accommodation_hygiene
            result = await run_accommodation_hygiene()
            dur = int((time.time() - started) * 1000)

            print(f"{ts_log()} [scheduler] Accommodation hygiene complete — "
                  f"reviewed {result.get('reviewed', 0)}, "
                  f"cleaned {result.get('cleaned', 0)}, "
                  f"kept {result.get('kept', 0)}")
            await _log_run_finish(run_id, "success", dur)

        except Exception as e:
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Accommodation hygiene failed: {e}")
            await _log_run_finish(run_id, "error", dur, str(e)[:500])

    async def _run_memory_ceremony(self):
        """Run biweekly Memory Ceremony — participatory memory hygiene.

        Scheduled every Sunday but only runs on even ISO weeks (biweekly).
        Week check is at runtime, not boot-time, so restarts don't affect the schedule.
        """
        # Biweekly gate — only run on even ISO weeks
        from datetime import datetime as _dt
        week_num = _dt.now().isocalendar()[1]
        if week_num % 2 != 0:
            print(f"{ts_log()} [scheduler] Memory Ceremony skipped — odd week ({week_num})")
            return

        protocol = "memory-ceremony"
        thread_id = f"{self._agent_id}-ceremony-{self._now().strftime('%Y-%m-%d')}"

        print(f"{ts_log()} [scheduler] Running Memory Ceremony (week {week_num})...")

        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()

        try:
            from src.memory.hygiene import run_memory_ceremony
            result = await run_memory_ceremony()
            dur = int((time.time() - started) * 1000)

            print(f"{ts_log()} [scheduler] Memory Ceremony #{result.get('ceremony_number', '?')} complete — "
                  f"agent flagged {result.get('agent_flagged', 0)}, "
                  f"auto caught {result.get('auto_cleaned', 0)}, "
                  f"{len(result.get('patterns_found', []))} patterns identified")
            await _log_run_finish(run_id, "success", dur)

        except Exception as e:
            dur = int((time.time() - started) * 1000)
            print(f"{ts_log()} [scheduler] Memory Ceremony failed: {e}")
            await _log_run_finish(run_id, "error", dur, str(e)[:500])

    async def _run_cove_backup(self):
        """CF-112: the operator-configured Cove backup (daily 03:30). Calls the
        runner directly (in-process — no endpoint secret needed) and logs to
        protocol_runs like every other protocol. Not-configured runs record a
        status and cost nothing."""
        from src.utils.cove_backup import backup_configured, run_cove_backup
        protocol = "cove-backup"
        thread_id = f"{self._agent_id}-cove-backup-{self._now().strftime('%Y-%m-%d')}"
        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()
        try:
            if not backup_configured():
                await _log_run_finish(run_id, "skipped", int((time.time() - started) * 1000),
                                      "not configured")
                return
            st = await run_cove_backup(trigger="scheduled")
            await _log_run_finish(run_id, "success" if st.get("ok") else "error",
                                  int((time.time() - started) * 1000),
                                  None if st.get("ok") else (st.get("summary") or "")[:300])
            print(f"{ts_log()} [scheduler] Cove backup: {st.get('summary')}")
        except Exception as e:
            await _log_run_finish(run_id, "error", int((time.time() - started) * 1000), str(e)[:200])
            print(f"{ts_log()} [scheduler] Cove backup failed: {e}")

    async def _run_backup(self):
        """Run the weekly git + DB backup via the backup API endpoint."""
        import httpx
        port = env("PORT", "8200")
        _secret = env("SHARED_CONTAINER_SECRET")
        _headers = {"X-Shared-Secret": _secret} if _secret else {}
        print(f"{ts_log()} [scheduler] Running weekly backup...")
        # C3-10: log to protocol_runs so "last backup" is a knowable fact — the
        # boot overdue-backstop (and any future status surface) reads it.
        protocol = "weekly-backup"
        thread_id = f"{self._agent_id}-backup-{self._now().strftime('%Y-%m-%d')}"
        run_id = await _log_run_start(protocol, thread_id)
        started = time.time()
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"http://localhost:{port}/api/system/backup", headers=_headers)
            dur = int((time.time() - started) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("skipped"):
                    print(f"{ts_log()} [scheduler] Weekly backup skipped — {data.get('summary', 'not configured')}")
                    await _log_run_finish(run_id, "success", dur, None)
                else:
                    git_ok = data.get("results", {}).get("git", {}).get("ok", False)
                    db_ok = data.get("results", {}).get("db", {}).get("ok", False)
                    print(f"{ts_log()} [scheduler] Backup complete — git={'OK' if git_ok else 'FAIL'} db={'OK' if db_ok else 'FAIL'}")
                    await _log_run_finish(run_id, "success" if (git_ok or db_ok) else "error", dur,
                                          None if (git_ok or db_ok) else "git and db both failed")
            else:
                print(f"{ts_log()} [scheduler] Backup request failed: HTTP {resp.status_code}")
                await _log_run_finish(run_id, "error", dur, f"HTTP {resp.status_code}")
        except Exception as e:
            print(f"{ts_log()} [scheduler] Backup error: {e}")
            await _log_run_finish(run_id, "error", int((time.time() - started) * 1000), str(e)[:500])

    async def _process_jules_inbox(self):
        """jules → backlog catch-up sweep (every 30m). Retries recordings left
        in any presence's Inbox by a save-time failure (model down, box asleep)
        so the talk-to-backlog loop never depends on the operator noticing.
        Cheap no-op when every Inbox is empty; JULES_AUTO_PROCESS=0 disables."""
        try:
            from src.dashboard.routes.jules_process import sweep_all_presences
            res = await sweep_all_presences()
            if res.get("processed") or res.get("failed"):
                print(f"{ts_log()} [scheduler] jules sweep: {res}")
        except Exception as e:
            print(f"{ts_log()} [scheduler] jules sweep error: {e}")

    async def _process_youtube_queue(self):
        """Check for queued YouTube posts ready for upload.

        Runs every 15 minutes. Finds posts where:
          - status = 'queued'
          - upload_date <= now
        Uploads each to YouTube as private with publishAt, then updates
        the queue entry with the video ID and status.

        Only runs if YOUTUBE_CLIENT_ID is configured (skips silently otherwise).
        """
        # Skip if YouTube not configured on this agent
        if not env("YOUTUBE_CLIENT_ID"):
            return

        try:
            from src.memory.database import get_db

            # CF-1: left unscoped (processor path) — the upload job is Cove
            # machinery and processes every presence's due rows.
            async with get_db() as conn:
                result = await conn.execute(
                    """SELECT id, title, description, tags, hashtags, file_path,
                              category_id, made_for_kids, is_short, related_video,
                              playlist_id, publish_date, series
                       FROM youtube_queue
                       WHERE status = 'queued' AND upload_date <= NOW()
                             AND youtube_video_id IS NULL
                       ORDER BY upload_date ASC
                       LIMIT 3"""
                )
                ready = await result.fetchall()

            if not ready:
                print(f"{ts_log()} [scheduler] YouTube queue check — 0 posts ready")
                return

            print(f"{ts_log()} [scheduler] YouTube queue: {len(ready)} post(s) ready for upload")

            for post in ready:
                await self._upload_youtube_post(post)

        except Exception as e:
            print(f"{ts_log()} [scheduler] YouTube queue check failed: {e}")

    async def _process_x_queue(self):
        """Check for queued X (Twitter) posts ready to publish.

        Runs every 15 minutes. Thin wrapper — all logic lives in
        x_posting.process_queued_x_posts(). Skips silently when X
        credentials are not configured on this agent (only Atlas has them).
        Respects X_DRY_RUN.
        """
        if not env("X_API_KEY"):
            return
        try:
            from src.dashboard.routes.x_posting import process_queued_x_posts

            result = await process_queued_x_posts()
            processed = result.get("processed", 0)
            if processed:
                print(f"{ts_log()} [scheduler] X queue: {processed} post(s) processed "
                      f"(dry_run={result.get('dry_run')}) — {result.get('results')}")
            else:
                print(f"{ts_log()} [scheduler] X queue check — 0 posts ready")
        except Exception as e:
            print(f"{ts_log()} [scheduler] X queue check failed: {e}")

    async def _upload_youtube_post(self, post: dict):
        """Upload a single post from the queue to YouTube."""
        from src.memory.database import get_db

        post_id = post["id"]
        now_str = self._now().strftime("%Y-%m-%d %H:%M ET")

        # Mark as uploading
        try:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE youtube_queue SET status = 'uploading' WHERE id = %s",
                    (post_id,),
                )
        except Exception:
            pass

        try:
            from src.dashboard.routes.youtube_auth import get_valid_access_token

            access_token = await get_valid_access_token("youtube")

            # Build title
            title = post["title"]
            if post["is_short"] and "#Shorts" not in title and "#shorts" not in title:
                if len(title) + 8 <= 100:
                    title = f"{title} #Shorts"

            # Append hashtags to description
            desc = post["description"] or ""
            hashtags = post["hashtags"] or ""
            if hashtags:
                desc = f"{desc}\n\n{hashtags}" if desc else hashtags

            # Build metadata
            snippet = {
                "title": title,
                "description": desc,
                "tags": post["tags"] if isinstance(post["tags"], list) else [],
                "categoryId": post["category_id"] or "22",
            }

            # publishAt for scheduled public release
            status_meta = {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": post["made_for_kids"],
            }
            if post["publish_date"]:
                from src.utils.time_utils import local_to_utc_dt
                status_meta["publishAt"] = local_to_utc_dt(post["publish_date"])

            video_metadata = {"snippet": snippet, "status": status_meta}

            # Check file exists
            import json
            from pathlib import Path
            import httpx

            from src.utils.content_paths import resolve_content_path
            video_path = resolve_content_path(post["file_path"])
            if not video_path:
                raise FileNotFoundError(f"Video not found: {post['file_path']}")

            file_size = video_path.stat().st_size
            ext = video_path.suffix.lower()
            content_types = {
                ".mp4": "video/mp4", ".mov": "video/quicktime",
                ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
                ".webm": "video/webm",
            }
            content_type = content_types.get(ext, "video/mp4")

            # Step 1: Initiate resumable upload
            async with httpx.AsyncClient(timeout=30) as client:
                init_resp = await client.post(
                    "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json; charset=utf-8",
                        "X-Upload-Content-Type": content_type,
                        "X-Upload-Content-Length": str(file_size),
                    },
                    content=json.dumps(video_metadata),
                )

            if init_resp.status_code not in (200, 308):
                raise Exception(f"Upload init failed ({init_resp.status_code}): {init_resp.text[:200]}")

            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                raise Exception("No upload URL returned from YouTube")

            # Step 2: Upload the video in chunks (resumable). Streams from disk so
            # multi-GB files don't load into memory or blow a single timeout — a
            # 4 GB full-length was failing the old single-shot PUT.
            CHUNK = 64 * 1024 * 1024  # 64 MB — must be a multiple of 256 KB
            result = None
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
                with open(video_path, "rb") as f:
                    uploaded = 0
                    while uploaded < file_size:
                        chunk = f.read(CHUNK)
                        if not chunk:
                            break
                        start = uploaded
                        end = uploaded + len(chunk) - 1
                        chunk_resp = await client.put(
                            upload_url,
                            headers={
                                "Content-Length": str(len(chunk)),
                                "Content-Range": f"bytes {start}-{end}/{file_size}",
                            },
                            content=chunk,
                        )
                        uploaded = end + 1
                        if chunk_resp.status_code in (200, 201):
                            result = chunk_resp.json()
                            break
                        elif chunk_resp.status_code == 308:
                            continue  # resume incomplete — send the next chunk
                        else:
                            raise Exception(f"Upload chunk failed ({chunk_resp.status_code}): {chunk_resp.text[:200]}")

            if not result:
                raise Exception("Upload finished but YouTube returned no video id")
            video_id = result.get("id")
            video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None

            # Update queue entry with success
            async with get_db() as conn:
                await conn.execute(
                    """UPDATE youtube_queue
                       SET status = 'uploaded', youtube_video_id = %s, youtube_url = %s,
                           uploaded_at = NOW()
                       WHERE id = %s""",
                    (video_id, video_url, post_id),
                )

            print(f"{ts_log()} [scheduler] YouTube uploaded: #{post_id} '{post['title']}' "
                  f"→ {video_url} ({file_size // 1024}KB)")

            # Remove calendar event — upload is done
            try:
                from src.dashboard.routes.youtube_calendar import delete_youtube_calendar_event
                await delete_youtube_calendar_event(post_id)
            except Exception:
                pass

            # Create follow-up tasks for Studio-only actions
            await self._create_youtube_followups(post, video_id, video_url)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"[:500]
            print(f"{ts_log()} [scheduler] YouTube upload FAILED: #{post_id} '{post['title']}' — {error_msg}")

            try:
                async with get_db() as conn:
                    await conn.execute(
                        "UPDATE youtube_queue SET status = 'failed', error_message = %s WHERE id = %s",
                        (error_msg, post_id),
                    )
            except Exception:
                pass

    async def _create_youtube_followups(self, post: dict, video_id: str, video_url: str):
        """Create a single follow-up task for Studio-only actions after upload.

        One task per video covers everything that can only be done in
        YouTube Studio (related video, altered content label, etc.).
        """
        from src.memory.database import get_db

        # Build checklist of Studio items for this video
        items = []
        if post.get("related_video"):
            items.append(f"Link related video: {post['related_video']}")
        if post.get("is_short"):
            items.append("Check altered content label (if applicable)")

        if not items:
            return

        studio_url = f"https://studio.youtube.com/video/{video_id}/edit"
        checklist = "\n".join(f"• {item}" for item in items)
        description = (
            f"Review in YouTube Studio before publish:\n"
            f"{checklist}\n\n"
            f"Studio: {studio_url}\n"
            f"Video: {video_url}"
        )

        try:
            async with get_db() as conn:
                await conn.execute(
                    """INSERT INTO tasks (title, description, status, source)
                       VALUES (%s, %s, 'pending', 'youtube-queue')""",
                    (f"Studio details — {post['title']}", description),
                )
            print(f"{ts_log()} [scheduler] Created YouTube Studio follow-up task for #{post['id']}")
        except Exception as e:
            print(f"{ts_log()} [scheduler] Failed to create follow-up task: {e}")

    def _schedule_async(self, coro_func, *args):
        def wrapper():
            if self._loop and self._loop.is_running():
                asyncio.ensure_future(coro_func(*args))
        return wrapper

    def setup_schedule(self):
        """Configure the base schedule. ALL agents get these jobs.

        DO NOT override this method in overlays. Override setup_agent_schedule()
        to add agent-specific jobs on top of the base.
        """
        # C3-13: clear any previously registered jobs so a watchdog RESTART of
        # scheduler.run() doesn't double-register the whole schedule. No-op on
        # the normal first run.
        schedule.clear()
        tz = self._tz_key

        # Team agents (those with a steward) receive tuning via steward dispatch.
        # They skip the 06:30 pull too — steward sends them the package.
        is_team_agent = bool(env("STEWARD_DATABASE_URL"))

        # --- Base schedule (every agent) ---
        schedule.every().day.at("00:00", tz).do(
            self._schedule_async(self._cleanup_old_logs)
        )
        schedule.every().day.at("00:05", tz).do(
            self._schedule_async(self._run_memory_auto_commit)
        )
        schedule.every().day.at("01:00", tz).do(
            self._schedule_async(self._run_memory_consolidation)
        )

        # --- Sunday memory pipeline (GPU window: 01:30–03:00) ---
        # Accommodation hygiene → synthesis → ceremony → backup.
        # Hygiene cleans sycophantic patterns weekly. Ceremony (biweekly)
        # lets agents self-review what's left. Keeps Sunday night reserved
        # for the memory system; Mon–Sat 00:00–05:00 is open for sims.
        schedule.every().sunday.at("01:30", tz).do(
            self._schedule_async(self._run_accommodation_hygiene)
        )
        schedule.every().sunday.at("02:00", tz).do(
            self._schedule_async(self._run_memory_synthesis)
        )
        schedule.every().sunday.at("02:30", tz).do(
            self._schedule_async(self._run_memory_ceremony)
        )
        schedule.every().sunday.at("03:00", tz).do(
            self._schedule_async(self._run_backup)
        )

        # CF-112 — the operator's OWN Cove backup (git remote + PAT set in the
        # UI). Daily at 03:30, no-op with a recorded "not configured" status when
        # the operator hasn't set it up. Distinct from the legacy Sunday founder
        # backup above (SSH deploy keys), which stays untouched.
        schedule.every().day.at("03:30", tz).do(
            self._schedule_async(self._run_cove_backup)
        )

        # LTP tuning package pull — product delivery at 6:00 AM.
        # Every Cove pulls the latest tuning from LT so Presences see the
        # updated Badge and Tuning Hub at a consistent time each morning.
        # This is the PRODUCT event — independent of agent tuning dispatch,
        # which Socrates orchestrates separately on its own timeline.
        # Team agents skip this — they receive everything from the steward.
        if not is_team_agent:
            schedule.every().day.at("06:00", tz).do(
                self._schedule_async(self._run_ltp_pull_only)
            )
            # Canonical KB pull — 06:05, just after the tuning pull. The KB is the
            # single hub-published source of truth; every Cove mirrors it read-only
            # into the steward space (shared down to presences). Cove-level only.
            schedule.every().day.at("06:05", tz).do(
                self._schedule_async(self._run_kb_sync)
            )

        # Host self-tune — the "electricity". The centralized host (admin/domain)
        # runs the Cove-as-Unit tune every morning off the day's package: team
        # agents + all Presences, deduped. A Cove tunes itself daily with zero
        # operator action and no dependence on an external conductor; Socrates
        # orchestration, when present, only sequences GPU timing and is deduped
        # against by the same echo check. (Single-mode steward tuning still
        # arrives via Socrates → /api/system/ltp-trigger → _run_ltp_morning.)
        from src.config import get_instance as _get_instance
        _is_host = (_get_instance().get("type") or "personal") in ("admin", "domain")
        if _is_host:
            # batch-10 #7 (locked 2026-07-04): Cove morning self-tune at 06:30 ET. The
            # Drop publishes ~05:30 ET from Socrates, so 06:30 leaves an hour of slack for
            # the package to land before the Cove tunes off it.
            # Multi-Cove-per-machine (Haven-on-one-box): stagger the self-tune by a
            # deterministic per-Cove offset (0-25 min) so several Coves sharing one local
            # Ollama don't all fire at 06:30 and thrash it. Paired with the host-shared
            # dispatch lock, co-located Coves serialize (a deferred Cove is caught by the
            # 30-min safety sweep below); a single Cove just tunes at its own :30-:55.
            import os as _os, hashlib as _hl
            _cid = (_os.getenv("COVE_ID") or _get_instance().get("id") or "").strip()
            _off = (int(_hl.sha1(_cid.encode()).hexdigest(), 16) % 26) if _cid else 0
            _tune_at = "06:%02d" % (30 + _off)
            schedule.every().day.at(_tune_at, tz).do(
                self._schedule_async(self._run_tuning_sweep)
            )
            print(f"[scheduler]   {_tune_at} — Cove self-tune (team + presences, deduped)")
            # LTP Protocol Spec §6 safety sweep: every 10 minutes, 07:00 → midnight
            # local, retry anyone still missing today's echo. Dedup-safe (the
            # sweep checks who tuned first), so it's a cheap no-op once settled.
            # This was never carried from the legacy steward overlay into cove-core
            # — a Cove that missed/stalled its 06:30 run silently skipped the whole
            # day (found live 2026-07-04, twice). Widened from the old 07:30–12:30 /
            # 30-min window (2026-07-07): on an unattended box that wakes after noon
            # the old cap skipped the day, and co-located Coves sharing one dispatch
            # lock drained only one-per-30-min (a 3-Cove Haven trickled over ~2h,
            # and the last one could miss the window entirely). Tuning is automated
            # plumbing; operators only touch it if they want to.
            schedule.every(10).minutes.do(
                self._schedule_async(self._run_sweep_window)
            )
            print("[scheduler]   07:00–midnight — safety sweep every 10m (spec §6, no-op when settled)")

        # YouTube queue processor — every 15 minutes (skips silently if no YOUTUBE_CLIENT_ID)
        schedule.every(15).minutes.do(
            self._schedule_async(self._process_youtube_queue)
        )

        # X queue processor — every 15 minutes (skips silently if no X_API_KEY)
        schedule.every(15).minutes.do(
            self._schedule_async(self._process_x_queue)
        )

        # Hub registration retry — every 30 minutes (audit C3-5). No-op unless
        # wizard finalize recorded a failed registration; retries with the FULL
        # payload so the set-once referred_by edge isn't lost.
        schedule.every(30).minutes.do(
            self._schedule_async(self._retry_hub_registration)
        )

        # jules → backlog catch-up sweep — every 30 minutes. Retries recordings
        # a save-time failure left in any presence's Inbox (paper trail in
        # AgentSkills/Ops/jules-log.md). No-op when Inboxes are empty.
        schedule.every(30).minutes.do(
            self._schedule_async(self._process_jules_inbox)
        )

        yt_configured = bool(env("YOUTUBE_CLIENT_ID"))
        print(f"[scheduler] {self._agent_id} base schedule:")
        print("[scheduler]   00:00 — Log cleanup")
        print("[scheduler]   00:05 — Memory auto-commit (7-day review window)")
        print("[scheduler]   01:00 — Memory consolidation (dedup + prune)")
        print("[scheduler]   01:30 Sun — Accommodation hygiene (sycophancy filter)")
        print("[scheduler]   02:00 Sun — Memory synthesis (weekly pattern extraction)")
        print("[scheduler]   02:30 Sun (biweekly) — Memory Ceremony (participatory hygiene)")
        print("[scheduler]   03:00 Sun — Weekly backup (git + DB)")
        print("[scheduler]   06:00 — LT tuning package pull (product delivery)")
        if not is_team_agent:
            print("[scheduler]   06:05 — Canonical KB pull (single source of truth)")
        if is_team_agent:
            print("[scheduler]   (LTP morning via steward dispatch — no independent tuning)")
        else:
            print("[scheduler]   (LTP morning via host self-tune / orchestration)")
        if yt_configured:
            print("[scheduler]   every 15m — YouTube queue processor")
        print(f"[scheduler]   Timezone: {tz}")

        # --- Agent-specific additions (overlay hook) ---
        self.setup_agent_schedule()

    def setup_agent_schedule(self):
        """Override in overlay schedulers to add agent-specific jobs.

        Called automatically at the end of setup_schedule(). The base
        schedule is already registered — only add what's unique to this agent.
        Print your additions so they show in the startup log.

        Example (in StuartCove/src/utils/scheduler.py):

            class StuartScheduler(AgentScheduler):
                def setup_agent_schedule(self):
                    tz = self._tz_key
                    schedule.every().day.at("07:30", tz).do(
                        self._schedule_async(self._run_tuning_sweep)
                    )
                    print("[scheduler]   07:30-12:30 — Tuning sweep (Stuart-specific)")
        """
        pass  # No-op in base — overlays add their jobs here

    async def run(self):
        """Start the scheduler loop. Blocks until stopped."""
        self._loop = asyncio.get_event_loop()
        self.setup_schedule()
        self._running = True
        job_count = len(schedule.get_jobs())
        print(f"[scheduler] {self._agent_id} Scheduler active ({job_count} jobs registered)")

        # Boot catch-up (host only, opt-in): tune anyone who missed today's run
        # while the box was down. Dedup-safe; fires once shortly after boot.
        # ON by default (flipped 2026-07-04 — spec §6 conformance): the sweep is
        # dedup-safe, so "auto-tune unexpectedly" can't happen — it only tunes
        # agents with NO echo today. Deploy restarts through the 06:30 window
        # were silently skipping whole days. Set LTP_BOOT_CATCHUP=false to opt
        # out (e.g. a box under repeated maintenance boots).
        try:
            from src.config import get_instance as _gi
            from src.env import env_bool as _eb
            if ((_gi().get("type") or "personal") in ("admin", "domain")
                    and _eb("LTP_BOOT_CATCHUP", "true")):
                asyncio.ensure_future(self._boot_catchup())
        except Exception:
            pass

        # C3-10: overdue-run backstop. The `schedule` library only fires when the
        # process is awake at the wall-clock minute — a family box that sleeps
        # overnight NEVER runs the 00:00–06:05 window (most notably the weekly
        # backup). protocol_runs records last success per protocol; run anything
        # past its cadence shortly after boot.
        try:
            asyncio.ensure_future(self._boot_overdue_backstop())
        except Exception:
            pass

        tick = 0
        while self._running:
            try:
                schedule.run_pending()
            except Exception as e:
                print(f"{ts_log()} [scheduler] run_pending CRASHED: {e}")
                import traceback
                traceback.print_exc()
            tick += 1
            if tick % 60 == 0:  # every 30 minutes (60 × 30s)
                print(f"{ts_log()} [scheduler] heartbeat — {len(schedule.get_jobs())} jobs, loop alive")
            await asyncio.sleep(30)

    def stop(self):
        self._running = False
        print("[scheduler] Scheduler stopped")
