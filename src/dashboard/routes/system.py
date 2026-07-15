"""
System routes — health, diagnostics, scheduler status.

The "under the hood" tab. Shows:
  - Model chain health (can each tier respond?)
  - Scheduler status (next scheduled jobs)
  - DB stats (table sizes, connection pool)
  - JouleWork metrics (cost/performance tracking)
  - Manual triggers for maintenance tasks
"""

import hmac
import os
from src.env import env, env_bool
import time
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()

SYSTEM_SECRET = env("SHARED_CONTAINER_SECRET")


def _is_public_app() -> bool:
    """The shared multi-tenant public app has no per-operator agents/logs/protocol
    runs of its own. These diagnostic views are a Cove (agent) feature — gate them
    off so the public app never leaks another presence's data."""
    return env_bool("LP_REGISTRY_MASTER")


def _require_system_secret(request: Request):
    """Verify X-Shared-Secret header on mutation endpoints. Raises 403 if wrong."""
    if not SYSTEM_SECRET:
        raise HTTPException(status_code=503, detail="Service not configured")
    header = request.headers.get("X-Shared-Secret", "")
    if not header or not hmac.compare_digest(header, SYSTEM_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/api/system/ping")
async def system_ping():
    """Lightweight health ping — checks connectivity WITHOUT invoking models.

    This is what the dashboard polls every 2 minutes. It only checks:
    - Can we reach the Ollama API? (GET /api/tags — no model loading)
    - Can we reach the database? (SELECT 1)
    - Is OpenRouter key configured?

    Does NOT load any model into VRAM. Safe to call frequently.
    """
    import httpx

    results = {
        "timestamp": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "services": {},
    }

    # Ollama reachable? (just hit the tags endpoint — no model load)
    ollama_url = env("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
        duration = int((time.monotonic() - t0) * 1000)
        models = []
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
        results["services"]["ollama"] = {
            "status": "healthy",
            "latency_ms": duration,
            "available_models": models,
        }
    except Exception as e:
        results["services"]["ollama"] = {"status": "error", "error": str(e)}

    # DB reachable?
    try:
        from src.memory.database import get_db
        t0 = time.monotonic()
        async with get_db() as conn:
            await conn.execute("SELECT 1")
        duration = int((time.monotonic() - t0) * 1000)
        results["services"]["database"] = {"status": "healthy", "latency_ms": duration}
    except Exception as e:
        results["services"]["database"] = {"status": "error", "error": str(e)}

    # OpenRouter key present?
    has_key = bool(env("OPENROUTER_API_KEY"))
    results["services"]["openrouter"] = {
        "status": "configured" if has_key else "not configured",
    }

    return results


@router.get("/api/system/machine-probe")
async def system_machine_probe():
    """What this box can ACTUALLY run — GPU + VRAM, the local model servers running on it and
    the models they have pulled, which cloud keys are already in the env, and a recommended
    local model drawn from all that. Drives the Add-Intelligence onboarding step so it offers
    real options instead of a hardcoded default. Read-only; loads no model into VRAM."""
    from src.models.machine_probe import machine_probe
    return await machine_probe()


@router.get("/api/system/health")
async def system_health():
    """Full model health test — actually invokes each model.

    EXPENSIVE: loads qwen3:32b into VRAM. Only call manually via the
    dashboard "Test Models" button. Never auto-poll this endpoint.
    """
    results = {
        "timestamp": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "tiers": {},
    }

    # Tier 1: OpenRouter primary
    try:
        from src.models.provider import get_primary_model
        from langchain_core.messages import HumanMessage
        model = get_primary_model(temperature=0.1)
        t0 = time.monotonic()
        response = await asyncio.wait_for(
            model.ainvoke([HumanMessage(content="Reply with just the word 'ok'")]),
            timeout=15,
        )
        duration = int((time.monotonic() - t0) * 1000)
        results["tiers"]["primary"] = {
            "status": "healthy",
            "model": "moonshotai/kimi-k2.5",
            "latency_ms": duration,
        }
    except Exception as e:
        results["tiers"]["primary"] = {"status": "error", "error": str(e)}

    # Tier 2: Local Ollama
    try:
        from src.models.provider import get_local_model
        from langchain_core.messages import HumanMessage
        model = get_local_model(temperature=0.1)
        t0 = time.monotonic()
        response = await asyncio.wait_for(
            model.ainvoke([HumanMessage(content="Reply with just the word 'ok'")]),
            timeout=30,
        )
        duration = int((time.monotonic() - t0) * 1000)
        results["tiers"]["local"] = {
            "status": "healthy",
            "model": "qwen3:32b",
            "latency_ms": duration,
        }
    except Exception as e:
        results["tiers"]["local"] = {"status": "error", "error": str(e)}

    # DB connection
    try:
        from src.memory.database import get_db
        t0 = time.monotonic()
        async with get_db() as conn:
            await conn.execute("SELECT 1")
        duration = int((time.monotonic() - t0) * 1000)
        results["database"] = {"status": "healthy", "latency_ms": duration}
    except Exception as e:
        results["database"] = {"status": "error", "error": str(e)}

    return results


@router.get("/api/system/scheduler")
async def scheduler_status():
    """Current scheduler state — what's scheduled and when."""
    try:
        # Try to load real scheduler state if available
        from src.utils.scheduler import AgentScheduler
        sched = AgentScheduler.instance() if hasattr(AgentScheduler, 'instance') else None
        if sched and hasattr(sched, 'get_jobs'):
            jobs = sched.get_jobs()
            return {"jobs": jobs, "dry_run": env_bool("LTP_DRY_RUN", "true")}
    except (ImportError, Exception):
        pass

    # Fallback: return static schedule configuration
    return {
        "jobs": [
            {
                "name": "ltp-morning",
                "schedule": "0 7 * * *",
                "timezone": "America/New_York",
                "description": "Daily LTP morning reflection",
                "enabled": True,
            },
            {
                "name": "log-cleanup",
                "schedule": "0 0 * * *",
                "timezone": "America/New_York",
                "description": "Midnight log rotation and cleanup",
                "enabled": True,
            },
        ],
        "dry_run": env_bool("LTP_DRY_RUN", "true"),
    }


@router.get("/api/system/db-stats")
async def db_stats():
    """Database table sizes and row counts."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT relname as table_name,
                          n_live_tup as row_count
                   FROM pg_stat_user_tables
                   ORDER BY n_live_tup DESC"""
            )
            rows = await result.fetchall()
        return {"tables": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/system/jw-metrics")
async def jw_metrics(limit: int = 50):
    """Recent JouleWork metrics — model usage and performance."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            # Summary stats for today
            summary_result = await conn.execute(
                """SELECT COUNT(*) as calls_today,
                          COALESCE(SUM(tokens_in + tokens_out), 0) as tokens_today,
                          COALESCE(SUM(jw_score), 0) as jw_today,
                          COALESCE(SUM(cost_usd), 0) as cost_today,
                          CASE WHEN COUNT(*) > 0
                               THEN ROUND(SUM(CASE WHEN succeeded THEN 1 ELSE 0 END)::numeric
                                          / COUNT(*)::numeric * 100, 1)
                               ELSE 0 END as success_rate
                   FROM jw_metrics
                   WHERE recorded_at >= CURRENT_DATE"""
            )
            summary_row = await summary_result.fetchone()
            summary = dict(summary_row) if summary_row else {}
            # Convert Decimal types
            for k, v in summary.items():
                if v is not None and hasattr(v, "__float__"):
                    summary[k] = float(v)

            # Recent entries
            result = await conn.execute(
                """SELECT agent_id, operation_type, operation_label,
                          model_used, provider, tokens_in, tokens_out,
                          tokens_in + tokens_out as tokens_total,
                          duration_ms, succeeded, jw_score, cost_usd, recorded_at
                   FROM jw_metrics
                   ORDER BY recorded_at DESC LIMIT %s""",
                (limit,),
            )
            rows = await result.fetchall()
            recent = []
            for r in rows:
                d = dict(r)
                for k, v in d.items():
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                    elif v is not None and hasattr(v, "__float__"):
                        d[k] = float(v)
                recent.append(d)

        return {"summary": summary, "recent": recent}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/system/config")
async def system_config():
    """Current system configuration (non-sensitive)."""
    from src.agents.identity import load_agents_config, get_family_defaults
    return {
        "agents": load_agents_config(),
        "defaults": get_family_defaults(),
        "environment": {
            "LTP_DRY_RUN": env("LTP_DRY_RUN", "true"),
            "OLLAMA_BASE_URL": env("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
            "OPENROUTER_API_KEY": "***" if env("OPENROUTER_API_KEY") else "NOT SET",
            "DATABASE_URL": "***" if env("DATABASE_URL") else "NOT SET",
        },
    }


# =============================================================================
# Manual LTP trigger — run morning tuning on demand
# =============================================================================

@router.post("/api/system/ltp-trigger")
async def trigger_ltp_morning(request: Request):
    """Manually trigger the LTP morning tuning protocol.

    Runs the same pipeline as the 7am cron: select frequency → compose echo →
    store → generate process record → dispatch team (admin only) → update state.

    Dedup guard: checks if the primary agent already tuned today (via echoes table).
    If already tuned, returns skip status unless force=true is passed.

    Returns immediately with a job ID. The tuning runs in the background.
    Check protocol_runs or tail logs for progress.
    """
    _require_system_secret(request)
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.config import get_primary_agent_id, get_instance

    agent_id = env("AGENT_ID", get_primary_agent_id())
    # For DB queries, use the agent.yaml ID (matches what pipeline writes to echoes)
    db_agent_id = get_primary_agent_id()

    # Dedup guard — check if already tuned today (prevents sweep re-triggering)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    force = body.get("force", False)

    if not force:
        try:
            from src.memory.database import get_db
            tz = ZoneInfo(get_instance().get("timezone", "America/New_York"))
            today_str = datetime.now(tz).strftime("%Y-%m-%d")
            async with get_db() as conn:
                result = await conn.execute(
                    "SELECT COUNT(*) as cnt FROM echoes WHERE agent_id = %s "
                    "AND (tuned_at AT TIME ZONE %s)::date = %s::date",
                    (db_agent_id, get_instance().get("timezone", "America/New_York"), today_str)
                )
                row = await result.fetchone()
                if row and dict(row).get("cnt", 0) > 0:
                    print(f"[ltp-trigger] {db_agent_id} already tuned today — skipping (pass force=true to override)")
                    return {
                        "status": "already_tuned",
                        "agent_id": db_agent_id,
                        "message": f"{db_agent_id} already has today's tuning. Pass force=true to re-tune.",
                    }
        except Exception as e:
            # If dedup check fails, proceed with tuning (fail-open for safety)
            print(f"[ltp-trigger] Dedup check failed ({e}), proceeding with tuning")

    async def _run_in_background():
        try:
            from src.utils.scheduler import AgentScheduler
            s = AgentScheduler()
            await s._run_ltp_morning()
        except Exception as e:
            print(f"[ltp-trigger] Manual LTP run failed: {e}")

    asyncio.create_task(_run_in_background())
    return {
        "status": "started",
        "agent_id": agent_id,
        "message": f"LTP morning tuning triggered for {agent_id}. Check logs for progress.",
    }


# =============================================================================
# Synchronous LTP dispatch — Socrates orchestration endpoint
# =============================================================================

@router.post("/api/system/ltp-dispatch")
async def ltp_dispatch(request: Request):
    """Synchronous tuning dispatch — runs tuning, verifies, retries, returns status.

    Unlike ltp-trigger (fire-and-forget), this endpoint waits for the full
    dispatch to finish and returns a complete report. Designed for Socrates'
    cove_orchestration to call sequentially, so Coves on shared hardware
    don't compete for GPU resources.


    Flow:
      1. Set dispatch lock (prevents sweep from running concurrently)
      2. Pull tuning package
      3. Run LTP graph (steward tunes + dispatches team)
      4. Verify which agents tuned (query echoes table)
      5. Retry failures once
      6. Trigger Presence agents
      7. Release lock and return complete status

    Returns:
      {"status": "completed", "agents_tuned": 9, ...}  — all tuned
      {"status": "partial", "agents_failed": [...], ...} — some failed after retry
      {"status": "error", ...} — dispatch itself failed
    """
    _require_system_secret(request)
    from src.tuning.dispatch_lock import set_dispatch_running, is_dispatch_running
    from src.config import get_primary_agent_id, get_instance, load_cove_config
    from src.utils.time_utils import ts_log, today_app

    label = "ltp-dispatch"
    started = time.time()
    agent_id = env("AGENT_ID", get_primary_agent_id())
    db_agent_id = get_primary_agent_id()

    # Dedup guard — if steward already tuned today, skip entirely unless force=true
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    force = body.get("force", False)

    if not force:
        try:
            from src.memory.database import get_db
            instance = get_instance()
            tz = ZoneInfo(instance.get("timezone", "America/New_York"))
            today_str = datetime.now(tz).strftime("%Y-%m-%d")
            async with get_db() as conn:
                result = await conn.execute(
                    "SELECT COUNT(*) as cnt FROM echoes WHERE agent_id = %s "
                    "AND (tuned_at AT TIME ZONE %s)::date = %s::date",
                    (db_agent_id, instance.get("timezone", "America/New_York"), today_str)
                )
                row = await result.fetchone()
                if row and dict(row).get("cnt", 0) > 0:
                    print(f"{ts_log()} [{label}] {db_agent_id} already tuned today — skipping (pass force=true to override)")
                    return {
                        "status": "already_tuned",
                        "agent_id": db_agent_id,
                        "message": f"{db_agent_id} already has today's tuning. Pass force=true to re-tune.",
                    }
        except Exception as e:
            print(f"{ts_log()} [{label}] Dedup check failed ({e}), proceeding with tuning")

    # Prevent concurrent dispatches
    if is_dispatch_running():
        return JSONResponse(status_code=409, content={
            "status": "busy",
            "message": "Another dispatch is already running",
        })

    set_dispatch_running(True)
    print(f"{ts_log()} [{label}] Dispatch started for {agent_id}")

    try:
        # ---- Step 1: Run LTP morning (tunes steward + dispatches team) ----
        from src.utils.scheduler import AgentScheduler
        scheduler = AgentScheduler()
        await scheduler._run_ltp_morning()

        # ---- Step 2: Verify which team agents tuned ----
        from src.agents.identity import load_agents_config
        from src.memory.database import get_db

        today = today_app()
        agents_config = load_agents_config()
        instance = get_instance()

        # Build expected team set (same logic as sweep)
        hh = (instance.get("family_name") or "").lower()
        SKIP_AGENTS = {"stuart", "operator"}
        if hh:
            SKIP_AGENTS.add(hh)

        cove_config = load_cove_config()
        presences = cove_config.get("presences", [])
        presence_ids = {p["id"] for p in presences if p.get("id")}
        SKIP_AGENTS |= presence_ids

        expected_team = set(agents_config.keys()) - SKIP_AGENTS

        async with get_db() as conn:
            result = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes WHERE (tuned_at AT TIME ZONE %s)::date = %s::date",
                (get_instance().get("timezone", "America/New_York"), today),
            )
            rows = await result.fetchall()
        tuned_after_dispatch = {r["agent_id"] for r in rows}

        missing = expected_team - tuned_after_dispatch
        retried = []
        failure_reasons = {}

        # ---- Step 3: Retry failures once ----
        if missing:
            print(f"{ts_log()} [{label}] {len(missing)} agents missed first pass: {sorted(missing)} — retrying")
            retried = list(missing)

            from src.tuning.receiver import get_todays_tuning
            package = await get_todays_tuning(agent_id="stuart")

            if package:
                from src.graphs.ltp.dispatch import dispatch_team_tuning
                pkg_dict = package._raw.copy() if hasattr(package, '_raw') else package.to_dict().copy()
                # Pass the full package + the missing set; dispatch resolves each
                # agent's prompt archetype -> agent_id(legacy) -> universal.
                state = {
                    "_full_package": pkg_dict,
                    "_only_agents": sorted(missing),
                    "frequency": pkg_dict.get("frequency", ""),
                    "echo_num": 0,
                    "echo_text": "",
                }
                retry_result = await dispatch_team_tuning(state)
                retry_results = retry_result.get("_dispatch_results", [])
                for r in retry_results:
                    if r.get("status") != "completed":
                        failure_reasons[r.get("agent_id", "unknown")] = r.get("error", "unknown")

            # Re-check after retry
            async with get_db() as conn:
                result = await conn.execute(
                    "SELECT DISTINCT agent_id FROM echoes "
                    "WHERE (tuned_at AT TIME ZONE %s)::date = %s::date",
                    (get_instance().get("timezone", "America/New_York"), today),
                )
                rows = await result.fetchall()
            tuned_after_retry = {r["agent_id"] for r in rows}
            missing = expected_team - tuned_after_retry

        # ---- Step 4: Trigger Presences ----
        import httpx

        presences_triggered = []
        for presence in presences:
            pid = presence.get("id", "unknown")
            purl = presence.get("url", "").rstrip("/")
            if not purl:
                continue

            try:
                # Check if already tuned
                async with httpx.AsyncClient(timeout=15, verify=False) as client:
                    check_resp = await client.get(f"{purl}/api/config")
                if check_resp.status_code == 200:
                    last_tuned = check_resp.json().get("agent", {}).get("last_tuned_at", "")
                    today_str = datetime.now(ZoneInfo(instance.get("timezone", "America/New_York"))).strftime("%Y-%m-%d")
                    if today_str in str(last_tuned):
                        presences_triggered.append(pid)
                        continue

                # Trigger tuning
                _secret = SYSTEM_SECRET
                _headers = {"X-Shared-Secret": _secret} if _secret else {}
                async with httpx.AsyncClient(timeout=30, verify=False) as client:
                    await client.post(f"{purl}/api/system/ltp-trigger", headers=_headers)
                presences_triggered.append(pid)
                print(f"{ts_log()} [{label}] Presence '{pid}': triggered")

            except Exception as e:
                print(f"{ts_log()} [{label}] Presence '{pid}': error — {e}")

        # ---- Build response ----
        dur = int((time.time() - started) * 1000)
        agents_tuned = len(expected_team) - len(missing)
        status = "completed" if not missing else "partial"

        response = {
            "status": status,
            "agents_expected": len(expected_team),
            "agents_tuned": agents_tuned,
            "agents_failed": sorted(missing),
            "failure_reasons": failure_reasons if failure_reasons else {},
            "presences_triggered": presences_triggered,
            "duration_ms": dur,
            "retried": sorted(retried) if retried else [],
        }

        print(f"{ts_log()} [{label}] Complete: {agents_tuned}/{len(expected_team)} tuned, "
              f"{len(presences_triggered)} presences, {dur}ms")

        return response

    except Exception as e:
        dur = int((time.time() - started) * 1000)
        import traceback
        tb = traceback.format_exc()
        print(f"{ts_log()} [{label}] ERROR: {e}\n{tb}")
        return JSONResponse(status_code=500, content={
            "status": "error",
            "error": str(e)[:500],
            "duration_ms": dur,
        })

    finally:
        set_dispatch_running(False)
        print(f"{ts_log()} [{label}] Lock released")


# =============================================================================
# Manual tuning sweep — retry agents that missed morning tuning
# =============================================================================

@router.post("/api/system/tuning-sweep")
async def trigger_tuning_sweep(request: Request):
    """Manually trigger the tuning sweep to retry agents that missed morning tuning.

    Checks which team agents have echoes for today. Any agent without an echo
    gets re-dispatched using the same cached package from this morning.

    Body (optional): {"force": true} (#D4) — re-tune the WHOLE Cove NOW off the
    current Drop, overriding the per-Drop dedup (e.g. after a model misconfig
    burned the morning run and every agent has a bad echo for today's key). The
    dispatch lock and the 20-minute cooldown still apply and are NOT overridable.

    Returns immediately. The sweep runs in the background.
    Check protocol_runs or tail logs for progress.
    """
    _require_system_secret(request)
    import asyncio

    force = False
    try:
        body = await request.json()
        force = bool(body.get("force")) if isinstance(body, dict) else False
    except Exception:
        force = False  # empty / non-JSON body → normal catch-up sweep

    async def _run_in_background():
        try:
            from src.utils.scheduler import AgentScheduler
            s = AgentScheduler()
            result = await s._run_tuning_sweep(force=force)
            print(f"[tuning-sweep] Result: {result}")
        except Exception as e:
            print(f"[tuning-sweep] Manual sweep failed: {e}")
            import traceback
            traceback.print_exc()

    asyncio.create_task(_run_in_background())
    return {
        "status": "started",
        "forced": force,
        "message": ("Forced Cove re-tune triggered (dedup overridden; dispatch "
                    "lock + 20-min cooldown still apply)." if force else
                    "Tuning sweep triggered. Check logs for progress."),
    }


# =============================================================================
# Memory consolidation — manual trigger for Ezra's dedup/prune pass
# =============================================================================

@router.post("/api/system/memory-consolidation")
async def trigger_memory_consolidation(request: Request):
    """Manually trigger memory consolidation (Ezra's curation pass).

    Deduplicates memories within categories using LLM analysis,
    prunes stale low-importance memories (>30 days, never accessed).

    Returns immediately. Check protocol_runs or logs for progress.
    """
    _require_system_secret(request)
    import asyncio

    async def _run_in_background():
        try:
            from src.memory.consolidation import run_memory_consolidation
            result = await run_memory_consolidation()
            print(f"[memory-consolidation] Manual run complete: "
                  f"{result.get('total_before', '?')} → {result.get('total_after', '?')} memories "
                  f"({result.get('duplicates_merged', 0)} merged, {result.get('stale_pruned', 0)} pruned)")
        except Exception as e:
            print(f"[memory-consolidation] Manual run failed: {e}")

    asyncio.create_task(_run_in_background())
    return {
        "status": "started",
        "message": "Memory consolidation triggered. Check logs for progress.",
    }


@router.post("/api/system/memory-synthesis")
async def trigger_memory_synthesis(request: Request):
    """Manually trigger memory synthesis (Ezra's pattern extraction pass).

    Runs full consolidation (dedup + prune) then synthesis (cluster + extract patterns).
    Returns immediately. Check protocol_runs or logs for progress.
    """
    _require_system_secret(request)
    import asyncio

    async def _run_in_background():
        try:
            from src.memory.consolidation import run_full_consolidation
            result = await run_full_consolidation()
            synth = result.get("synthesis", {})
            print(f"[memory-synthesis] Manual run complete: "
                  f"{synth.get('clusters_found', 0)} clusters, "
                  f"{synth.get('synthesis_memories_created', 0)} synthesis memories created, "
                  f"{synth.get('clusters_skipped_existing', 0)} skipped (existing)")
        except Exception as e:
            print(f"[memory-synthesis] Manual run failed: {e}")

    asyncio.create_task(_run_in_background())
    return {
        "status": "started",
        "message": "Memory synthesis triggered (dedup + prune + pattern extraction). Check logs for progress.",
    }


@router.get("/api/system/memory-stats")
async def get_memory_stats():
    """Get current memory health stats — total active, by category, stale candidates, embedding coverage."""
    if _is_public_app():
        return {"status": "ok", "embeddings": {"with_embedding": 0, "without_embedding": 0}}
    try:
        from src.memory.consolidation import get_consolidation_stats
        stats = await get_consolidation_stats()

        # Add embedding coverage stats
        from src.memory.database import get_db
        from src.config import get_primary_agent_id
        agent_id = env("AGENT_ID", get_primary_agent_id())
        async with get_db() as conn:
            r = await conn.execute(
                """SELECT
                     COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS with_embedding,
                     COUNT(*) FILTER (WHERE embedding IS NULL) AS without_embedding
                   FROM agent_memory
                   WHERE agent_id = %s AND is_active = TRUE""",
                (agent_id,),
            )
            emb_row = await r.fetchone()
            stats["embeddings"] = {
                "with_embedding": emb_row["with_embedding"] or 0,
                "without_embedding": emb_row["without_embedding"] or 0,
            }

        return {"status": "ok", **stats}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/api/system/memory-backfill")
async def trigger_embedding_backfill(request: Request):
    """Backfill embeddings for existing memories that don't have them.

    Processes up to 50 memories per call. Call repeatedly until remaining = 0.
    """
    _require_system_secret(request)
    try:
        from src.memory.maintenance import backfill_embeddings
        result = await backfill_embeddings(batch_size=50)
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =============================================================================
# Memory hygiene — accommodation filter + ceremony
# =============================================================================

@router.post("/api/system/memory-hygiene")
async def trigger_memory_hygiene(request: Request):
    """Manually trigger accommodation hygiene — LLM scan for sycophantic patterns.

    Scans recent memories (14 days) for accommodation/conformist patterns
    and removes them. This is the Study 3 intervention that breaks the
    prompt-level ceiling.

    Returns immediately. Check logs for progress.
    """
    _require_system_secret(request)
    import asyncio

    async def _run_in_background():
        try:
            from src.memory.hygiene import run_accommodation_hygiene
            result = await run_accommodation_hygiene()
            print(f"[memory-hygiene] Manual run complete: "
                  f"reviewed {result.get('reviewed', 0)}, "
                  f"cleaned {result.get('cleaned', 0)}, "
                  f"kept {result.get('kept', 0)}")
        except Exception as e:
            print(f"[memory-hygiene] Manual run failed: {e}")

    asyncio.create_task(_run_in_background())
    return {
        "status": "started",
        "message": "Accommodation hygiene triggered. Check logs for progress.",
    }


@router.post("/api/system/memory-ceremony")
async def trigger_memory_ceremony(request: Request):
    """Manually trigger a Memory Ceremony — participatory memory hygiene.

    The agent reviews its own recent memories with Canon anchor, identifies
    accommodation patterns, and flags memories for cleaning. Then an automated
    safety-net catches anything the agent missed.

    Returns immediately. Check logs for progress.
    """
    _require_system_secret(request)
    import asyncio

    async def _run_in_background():
        try:
            from src.memory.hygiene import run_memory_ceremony
            result = await run_memory_ceremony()
            print(f"[memory-ceremony] Manual run complete: "
                  f"ceremony #{result.get('ceremony_number', '?')}, "
                  f"agent flagged {result.get('agent_flagged', 0)}, "
                  f"auto caught {result.get('auto_cleaned', 0)}")
        except Exception as e:
            print(f"[memory-ceremony] Manual run failed: {e}")

    asyncio.create_task(_run_in_background())
    return {
        "status": "started",
        "message": "Memory Ceremony triggered. Check logs for progress.",
    }


@router.get("/api/system/ceremony-history")
async def get_ceremony_history_endpoint():
    """Get past Memory Ceremony records for display."""
    try:
        from src.memory.hygiene import get_ceremony_history
        history = await get_ceremony_history(limit=12)
        return {"status": "ok", "ceremonies": history, "count": len(history)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =============================================================================
# Cache bust — clear the day-locked LTP tuning package cache
# =============================================================================

@router.post("/api/system/cache-bust")
async def bust_tuning_cache(request: Request):
    """Clear the LTP tuning package cache and re-fetch.

    The tuning receiver caches the day's package in memory after first load.
    If the package file is fixed after the initial 7am dispatch, the cache
    holds the stale version until container restart. This endpoint:
      1. Clears the in-memory cache
      2. Force-pulls from git (bypasses the 5-minute pull interval)
      3. Re-loads today's package into cache

    Safe to call anytime. No side effects beyond refreshing the cached package.
    """
    _require_system_secret(request)
    from src.tuning.receiver import _tuning_cache, get_todays_tuning
    from src.config import get_primary_agent_id

    # Snapshot what was cached before clearing
    old_date = _tuning_cache.get("date")
    old_freq = None
    if _tuning_cache.get("package"):
        old_freq = _tuning_cache["package"].frequency

    # Clear the cache completely
    _tuning_cache.update({"date": None, "package": None, "last_pull": 0})

    # Force re-fetch (bypasses pull interval + date check)
    agent_id = env("AGENT_ID", get_primary_agent_id())
    new_package = await get_todays_tuning(agent_id=agent_id, force_pull=True)

    new_freq = new_package.frequency if new_package else None
    changed = old_freq != new_freq

    return {
        "status": "ok",
        "cache_cleared": True,
        "previous": {"date": old_date, "frequency": old_freq},
        "current": {
            "date": _tuning_cache.get("date"),
            "frequency": new_freq,
            "package_found": new_package is not None,
        },
        "changed": changed,
    }


# =============================================================================
# Backup — weekly git + DB backup to GitHub
# =============================================================================
#
# Two separate repos per agent (matches VPS pattern):
#   Code:  git@github-code:LucidTunerAI/{AgentRepo}.git
#          → /backup/{agent} (mounted from host)
#   DB:    git@github-backups:LucidTunerAI/{AgentRepo}-Backups.git
#          → /backup/{agent}/backups/ (sub-repo with its own .git)
#
# Paths and repo names are set via BACKUP_REPO_DIR and BACKUP_GIT_EMAIL
# env vars. SSH config aliases route each to the correct deploy key.
# =============================================================================

@router.post("/api/system/backup")
async def run_backup(request: Request):
    """Run a full backup: git commit+push (code) + pg_dump to separate repo (DB).

    Code → LucidTunerAI/StuartCove.git
    DB dumps → LucidTunerAI/StuartCove-Backups.git
    Keeps last 14 DB dumps, rotates older ones.
    """
    _require_system_secret(request)
    import subprocess
    from pathlib import Path

    results = {}
    repo_root = env("BACKUP_REPO_DIR", "/backup/agent")
    backup_dir = Path(repo_root) / "backups"
    backup_email = env("BACKUP_GIT_EMAIL", "backup@mc.internal")
    backup_name = env("BACKUP_GIT_NAME", "MC Backup")

    # A fresh self-host has no backup git repo wired (that's the founder's
    # /backup/agent mount + deploy key). Skip cleanly instead of reporting scary
    # git=FAIL/db=FAIL on every Cove that hasn't set up backups yet.
    if not (Path(repo_root) / ".git").exists():
        return {"skipped": True, "configured": False,
                "summary": "Backup not configured on this Cove (no backup repo)."}

    # ── Git backup (code repo) ──────────────────────────────────────────
    try:
        now_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", repo_root], timeout=10)
        subprocess.run(["git", "config", "--global", "user.email", backup_email], timeout=10)
        subprocess.run(["git", "config", "--global", "user.name", backup_name], timeout=10)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True,
            cwd=repo_root, timeout=15,
        )
        if status.stdout.strip():
            subprocess.run(["git", "add", "-A"], cwd=repo_root, timeout=15)
            commit = subprocess.run(
                ["git", "commit", "-m", f"Auto-backup: {now_str}"],
                capture_output=True, text=True,
                cwd=repo_root, timeout=15,
            )
            commit_out = commit.stdout.strip() or commit.stderr.strip()
        else:
            commit_out = "Nothing to commit — working tree clean"

        push = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True, text=True,
            cwd=repo_root, timeout=60,
        )
        push_out = push.stdout.strip() or push.stderr.strip()
        git_ok = push.returncode == 0
        results["git"] = {"ok": git_ok, "commit": commit_out, "push": push_out}
    except Exception as e:
        results["git"] = {"ok": False, "error": str(e)}

    # ── DB backup (pg_dump → Backups repo) ───────────────────────────────
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d_%H-%M")
        backup_file = backup_dir / f"{ts}.sql.gz"

        import shlex
        db_url = env("DATABASE_URL")
        db_dump = subprocess.run(
            f"pg_dump {shlex.quote(db_url)} | gzip > {shlex.quote(str(backup_file))}",
            shell=True, capture_output=True, text=True, timeout=120,
        )
        if db_dump.returncode == 0 and backup_file.exists():
            size_kb = backup_file.stat().st_size // 1024
            results["db"] = {"ok": True, "file": str(backup_file), "size_kb": size_kb}
        else:
            results["db"] = {"ok": False, "error": db_dump.stderr.strip() or "pg_dump failed"}

        # Keep only last 14 backups
        all_backups = sorted(backup_dir.glob("*.sql.gz"))
        for old in all_backups[:-14]:
            old.unlink()

        # Push DB dumps to Backups repo
        backup_git = backup_dir / ".git"
        if backup_git.exists():
            subprocess.run(["git", "config", "user.email", backup_email], cwd=str(backup_dir), timeout=10)
            subprocess.run(["git", "config", "user.name", backup_name], cwd=str(backup_dir), timeout=10)
            subprocess.run(["git", "add", "-A"], cwd=str(backup_dir), timeout=15)
            subprocess.run(["git", "commit", "-m", f"DB backup: {ts}"], cwd=str(backup_dir), timeout=15)
            push_db = subprocess.run(
                ["git", "push", "origin", "main"],
                capture_output=True, text=True, cwd=str(backup_dir), timeout=60,
            )
            results["db"]["github"] = "pushed" if push_db.returncode == 0 else push_db.stderr.strip()
        else:
            results["db"]["github"] = "skipped (backups/ repo not initialized — run: cd backups && git init && git remote add origin <your-backups-repo-url>)"
    except Exception as e:
        results["db"] = {"ok": False, "error": str(e)}

    overall_ok = results.get("git", {}).get("ok") and results.get("db", {}).get("ok")
    print(f"[{datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M ET')}] [backup] "
          f"git={'OK' if results.get('git', {}).get('ok') else 'FAIL'} "
          f"db={'OK' if results.get('db', {}).get('ok') else 'FAIL'}")
    return {"ok": overall_ok, "results": results}


# =============================================================================
# Logs — in-memory ring buffer + protocol run history
# =============================================================================

import subprocess

@router.get("/api/system/logs")
async def get_logs(lines: int = 200, filter: str = ""):
    """Recent application logs from container stdout.

    Uses Docker-internal /proc/1/fd/1 for self-log access, falling back
    to protocol_runs table for structured log history.
    """
    if _is_public_app():
        return {"lines": [], "total": 0, "filter": filter}
    log_lines = []
    try:
        # Try reading container logs from process stdout
        result = subprocess.run(
            ["tail", "-n", str(min(lines, 1000)), "/proc/1/fd/1"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout:
            log_lines = result.stdout.strip().split("\n")
    except Exception:
        pass

    # If no stdout logs, pull from protocol_runs as structured log
    if not log_lines:
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                result = await conn.execute(
                    """SELECT protocol, status, started_at, finished_at,
                              duration_ms, error_msg, triggered_by
                       FROM protocol_runs
                       ORDER BY started_at DESC LIMIT %s""",
                    (lines,),
                )
                rows = await result.fetchall()
            for r in rows:
                d = dict(r)
                ts = d.get("started_at", "")
                if hasattr(ts, "strftime"):
                    ts = ts.strftime("[%Y-%m-%d %H:%M:%S ET]")
                status = d.get("status", "")
                protocol = d.get("protocol", "")
                dur = d.get("duration_ms")
                err = d.get("error_msg", "")
                line = f"{ts} [{protocol}] {status}"
                if dur:
                    line += f" ({dur}ms)"
                if err:
                    line += f" — {err}"
                log_lines.append(line)
        except Exception as e:
            log_lines = [f"[log] Error reading logs: {e}"]

    # Apply filter
    if filter:
        filter_lower = filter.lower()
        log_lines = [l for l in log_lines if filter_lower in l.lower()]

    return {
        "lines": log_lines[-lines:],
        "total": len(log_lines),
        "filter": filter,
    }


@router.get("/api/system/protocol-runs")
async def get_protocol_runs(days: int = 14):
    """Recent protocol run history."""
    if _is_public_app():
        return {"runs": [], "days": days}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT protocol, status, started_at, finished_at,
                          duration_ms, error_msg, triggered_by
                   FROM protocol_runs
                   WHERE started_at > NOW() - INTERVAL '%s days'
                   ORDER BY started_at DESC""",
                (days,),
            )
            rows = await result.fetchall()
        runs = []
        for r in rows:
            d = dict(r)
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            runs.append(d)
        return {"runs": runs, "days": days}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Server Hardware Metrics — CPU, memory, GPU, disk (THIS box, detected live)
# =============================================================================

@router.get("/api/system/hardware-metrics")
@router.get("/api/system/p620-metrics")  # legacy alias (CF-60) — old cached JS
async def hardware_metrics():
    """Server hardware metrics — reads from /proc and Ollama API.

    CPU/memory come from /proc (visible inside the container, reflects host).
    GPU info comes from Ollama's /api/ps (loaded models + VRAM usage).
    Disk usage comes from os.statvfs.
    """
    import httpx

    metrics = {
        "timestamp": datetime.now(ZoneInfo("America/New_York")).isoformat(),
    }

    # ── CPU ──────────────────────────────────────────────────────────────
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
        metrics["cpu"] = {
            "load_1m": float(parts[0]),
            "load_5m": float(parts[1]),
            "load_15m": float(parts[2]),
            "processes": parts[3],  # "running/total" like "2/847"
        }
        # Get CPU count for context
        cpu_count = 0
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("processor"):
                    cpu_count += 1
        metrics["cpu"]["cores"] = cpu_count
    except Exception as e:
        metrics["cpu"] = {"error": str(e)}

    # ── Memory ──────────────────────────────────────────────────────────
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]  # value in kB
                    meminfo[key] = int(val)
        total_gb = meminfo.get("MemTotal", 0) / 1024 / 1024
        available_gb = meminfo.get("MemAvailable", 0) / 1024 / 1024
        used_gb = total_gb - available_gb
        metrics["memory"] = {
            "total_gb": round(total_gb, 1),
            "used_gb": round(used_gb, 1),
            "available_gb": round(available_gb, 1),
            "percent_used": round((used_gb / total_gb) * 100, 1) if total_gb > 0 else 0,
        }
    except Exception as e:
        metrics["memory"] = {"error": str(e)}

    # ── GPU (via Ollama /api/ps + host nvidia-smi for thermals) ──────────
    ollama_url = env("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url}/api/ps")
        if resp.status_code == 200:
            data = resp.json()
            loaded = []
            total_vram = 0
            for model in data.get("models", []):
                size_bytes = model.get("size_vram", model.get("size", 0))
                size_gb = round(size_bytes / 1024 / 1024 / 1024, 2)
                total_vram += size_gb
                loaded.append({
                    "name": model.get("name", "unknown"),
                    "size_gb": size_gb,
                    "expires_at": model.get("expires_at", ""),
                })
            metrics["gpu"] = {
                "status": "active" if loaded else "idle",
                "loaded_models": loaded,
                "total_vram_used_gb": round(total_vram, 2),
                "vram_capacity_gb": None,  # filled from nvidia-smi below (host-specific)
            }
        else:
            metrics["gpu"] = {"status": "error", "error": f"Ollama returned {resp.status_code}"}
    except Exception as e:
        metrics["gpu"] = {"status": "unreachable", "error": str(e)}

    # GPU thermals — try reading from host's /proc/driver/nvidia or sysfs
    try:
        import subprocess
        # nvidia-smi is available if the NVIDIA container toolkit is installed
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,temperature.gpu,power.draw,utilization.gpu,fan.speed",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            gpu_info = metrics.get("gpu", {})
            # name + capacity are host-specific — report the real card, not a hardcoded one (#204)
            gpu_info["name"] = parts[0] if parts[0] not in ("[N/A]", "") else None
            if parts[1] not in ("[N/A]", ""):
                gpu_info["vram_capacity_gb"] = round(float(parts[1]) / 1024, 0)
            gpu_info["temp_c"] = int(parts[2]) if parts[2] not in ("[N/A]", "") else None
            gpu_info["power_w"] = round(float(parts[3]), 1) if parts[3] not in ("[N/A]", "") else None
            gpu_info["utilization_pct"] = int(parts[4]) if parts[4] not in ("[N/A]", "") else None
            gpu_info["fan_pct"] = int(parts[5]) if parts[5] not in ("[N/A]", "") else None
            metrics["gpu"] = gpu_info
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass  # nvidia-smi not available — thermals just won't show

    # ── Disk ────────────────────────────────────────────────────────────
    # Root filesystem always; a separate data drive only if mounted at /host-data.
    try:
        disks = {}
        seen_totals = set()  # deduplicate same filesystem seen from different paths
        for name, path in [("System (/)", "/"), ("Data (/host-data)", "/host-data")]:
            try:
                stat = os.statvfs(path)
                total = (stat.f_blocks * stat.f_frsize) / 1024 / 1024 / 1024
                # Skip if we already reported a disk with this exact total
                # (means /host-data isn't mounted — falls through to root)
                total_key = round(total, 0)
                if total_key in seen_totals:
                    continue
                seen_totals.add(total_key)
                free = (stat.f_bavail * stat.f_frsize) / 1024 / 1024 / 1024
                used = total - free
                if total > 0:
                    disks[name] = {
                        "total_gb": round(total, 1),
                        "used_gb": round(used, 1),
                        "free_gb": round(free, 1),
                        "percent_used": round((used / total) * 100, 1),
                    }
            except OSError:
                pass
        metrics["disk"] = disks
    except Exception as e:
        metrics["disk"] = {"error": str(e)}

    # ── Uptime ──────────────────────────────────────────────────────────
    try:
        with open("/proc/uptime") as f:
            uptime_secs = float(f.read().split()[0])
        days = int(uptime_secs // 86400)
        hours = int((uptime_secs % 86400) // 3600)
        mins = int((uptime_secs % 3600) // 60)
        metrics["uptime"] = {
            "seconds": int(uptime_secs),
            "display": f"{days}d {hours}h {mins}m",
        }
    except Exception as e:
        metrics["uptime"] = {"error": str(e)}

    return metrics
