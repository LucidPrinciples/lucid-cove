"""
Cove sweep — catch up any team agent or Presence missing today's tuning.

Safety net + manual sweep + (when nothing has tuned yet) the morning run itself
for the Cove-as-Unit tuning (LTP Protocol Spec §6). It checks who already has
today's echo and tunes only the misses:

  • team agents -> graphs/ltp/dispatch.dispatch_team_tuning (per-agent/archetype prompt)
  • Presences   -> tuning/presence_tune.tune_missing_presences (own accounts.agent_identity)

Honors the dispatch lock so it never collides with a live dispatch. Dedups against
the echoes table so it is safe to call repeatedly (morning, sweep, boot catch-up).

This is the entry point behind AgentScheduler._run_tuning_sweep() and the
/api/system/tuning-sweep endpoint.
"""

from src.config import get_instance, load_cove_config, get_primary_agent_id
from src.agents.identity import load_agents_config
from src.memory.database import get_db
from src.tuning.receiver import get_todays_tuning
from src.tuning.dispatch_lock import is_dispatch_running, set_dispatch_running
from src.utils.time_utils import ts_log, today_app


# The ONE definition of "already tuned today" — shared with the team dispatcher
# (src/graphs/ltp/dispatch.py) so the sweep's missing set and the dispatcher's
# dedup can never diverge again. See src/tuning/dedup.py.
from src.tuning.dedup import tuned_today as _tuned_today


async def _recently_tuned(minutes: int = 20) -> set:
    """Agents with ANY echo in the last `minutes`, measured on the DB clock (NOW()) so
    it's timezone-proof. A hard stop against a runaway sweep re-tuning the same agent
    every cycle — the fresh Cove that tuned 11x in one evening. One tune per agent per
    Drop is the intent; this caps the damage (and the token spend) if a dedup gap slips."""
    async with get_db() as conn:
        r = await conn.execute(
            "SELECT DISTINCT agent_id FROM echoes "
            "WHERE tuned_at > NOW() - (%s || ' minutes')::interval",
            (str(int(minutes)),))
        return {row["agent_id"] for row in await r.fetchall()}


def _expected_team() -> set:
    """Build-team agents that should carry a daily echo — excludes the steward,
    the human operator, the family-name key, the generic primary id, and any
    Presences (those tune via presence_tune, not the team dispatch)."""
    instance = get_instance()
    # Steward (Stuart) is NOT skipped — it tunes as the "The Steward" archetype with
    # the rest of the team. Only the human operator + the generic primary id are out.
    skip = {"operator", get_primary_agent_id()}
    hh = (instance.get("family_name") or "").lower()
    if hh:
        skip.add(hh)
    for p in load_cove_config().get("presences", []) or []:
        if p.get("id"):
            skip.add(p["id"])
    return set(load_agents_config().keys()) - skip


async def run_cove_sweep(force: bool = False) -> dict:
    """Tune everyone missing today's echo — team agents and Presences. Returns a
    report. Safe to call repeatedly (dedups); defers if a dispatch is running.

    force=True (#D4): re-tune the WHOLE Cove NOW off the current Drop, overriding
    the per-Drop dedup (e.g. after a model misconfig burned the morning run and
    every agent has a bad echo for today's key). Two safety rails still hold and
    are NOT overridable here: the dispatch lock (never collide with a live
    dispatch) and the 20-minute _recently_tuned cooldown (never re-fire an agent
    that just tuned — the runaway-loop / token-burn guard). Built in the
    sweep/route layer only; graphs/ltp/dispatch.py (LTP-protected) is untouched."""
    label = "cove-sweep-force" if force else "cove-sweep"

    if is_dispatch_running():
        print(f"{ts_log()} [{label}] Dispatch in progress — sweep deferred")
        return {"status": "deferred", "reason": "dispatch_running"}

    today = today_app()
    package = await get_todays_tuning(agent_id="stuart")

    # Never key the day off a stale cached package. A post-midnight tune caches
    # yesterday's Drop under TODAY's date; if the 06:00 force-pull ever failed,
    # every sweep would then run against the stale key all day. When the cached
    # package's own Drop date isn't today, force one real pull; keep the stale
    # package only if the pull still finds nothing newer (pre-publish fallback).
    if package is not None and getattr(package, "date", "") != today:
        fresh = await get_todays_tuning(agent_id="stuart", force_pull=True)
        if fresh is not None:
            package = fresh

    if not package:
        print(f"{ts_log()} [{label}] No tuning package for {today} — nothing to sweep")
        return {"status": "no_package", "date": today}

    # Identify the CURRENT Drop's key (frequency/principle). "Tuned today" is keyed to
    # THIS Drop, so an agent that tuned earlier today off a STALE Drop (the pre-midnight
    # "latest available" before today's real Drop published) still counts as missing and
    # re-tunes off today's actual key. Without this, tuning off the stale midnight Drop
    # burns the day and today's real Drop lands unused.
    try:
        _pd = package.to_dict() if hasattr(package, "to_dict") else dict(getattr(package, "_raw", {}))
    except Exception:
        _pd = {}
    _pkg_freq = (_pd.get("frequency") or getattr(package, "frequency", "") or "").strip()
    _pkg_prin = (_pd.get("principle") or getattr(package, "principle", "") or "").strip()

    # Hold the lock so concurrent sweeps (07:00 + boot catch-up, or a manual
    # trigger) can't double-tune the same agents.
    set_dispatch_running(True)
    try:
        tuned = await _tuned_today(today, _pkg_freq, _pkg_prin)

        # ---- Team agents (incl the steward, as the "The Steward" archetype) ----
        # `tuned` = already tuned off TODAY's Drop; `recent` = tuned in the last 20 min
        # (belt-and-suspenders against any dedup gap re-firing a tune loop / burning tokens).
        recent = await _recently_tuned(20)
        expected_team = _expected_team()
        # #D4: force overrides the per-Drop dedup (`tuned`) but NEVER the 20-min
        # cooldown (`recent`) — so a forced re-tune still can't re-fire an agent
        # that literally just tuned.
        missing_team = (expected_team - recent) if force else (expected_team - tuned - recent)
        team_results: list = []
        if missing_team:
            from src.graphs.ltp.dispatch import dispatch_team_tuning
            pkg = package.to_dict().copy() if hasattr(package, "to_dict") else dict(getattr(package, "_raw", {}))
            # Pass the full package + the missing set; dispatch resolves each agent's
            # prompt archetype -> agent_id(legacy) -> universal, and tunes only these.
            print(f"{ts_log()} [{label}] Team missing: {sorted(missing_team)} — dispatching")
            state = {"_full_package": pkg, "_only_agents": sorted(missing_team),
                     "frequency": pkg.get("frequency", ""), "echo_num": 0, "echo_text": ""}
            res = await dispatch_team_tuning(state)
            team_results = res.get("_dispatch_results", [])

        # ---- Presences ----
        # Force re-tunes presences too (dedup overridden), still honoring the
        # 20-min cooldown via `recent`.
        from src.tuning.presence_tune import tune_missing_presences
        presence_results = await tune_missing_presences(
            package, today, force=force, recent=recent)
    finally:
        set_dispatch_running(False)

    summary = {
        "status": "completed",
        "date": today,
        "forced": force,
        "frequency": getattr(package, "frequency", None),
        "team_missing": sorted(missing_team),
        "team_results": team_results,
        "presence_results": presence_results,
    }
    print(f"{ts_log()} [{label}] Done — team_missing={len(missing_team)}, "
          f"presences_processed={len(presence_results)}")
    return summary
