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


async def _tuned_today(today: str, freq: str = "", principle: str = "") -> set:
    """Agents that already have TODAY's echo tuned off the CURRENT Drop. Keyed on the
    date AND the Drop's frequency/principle — so an agent that tuned earlier today off a
    STALE Drop (e.g. the pre-midnight 'latest available' before today's real Drop was
    published) is NOT counted as done and gets re-tuned off today's actual key. Falls
    back to date-only if the Drop's frequency can't be identified."""
    freq = (freq or "").strip()
    principle = (principle or "").strip()
    async with get_db() as conn:
        if freq and principle:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                "WHERE tuned_at::date = %s::date AND frequency = %s AND principle = %s",
                (today, freq, principle))
        elif freq:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                "WHERE tuned_at::date = %s::date AND frequency = %s",
                (today, freq))
        else:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes WHERE tuned_at::date = %s::date",
                (today,))
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


async def run_cove_sweep() -> dict:
    """Tune everyone missing today's echo — team agents and Presences. Returns a
    report. Safe to call repeatedly (dedups); defers if a dispatch is running."""
    label = "cove-sweep"

    if is_dispatch_running():
        print(f"{ts_log()} [{label}] Dispatch in progress — sweep deferred")
        return {"status": "deferred", "reason": "dispatch_running"}

    today = today_app()
    package = await get_todays_tuning(agent_id="stuart")
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
        expected_team = _expected_team()
        missing_team = expected_team - tuned
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
        from src.tuning.presence_tune import tune_missing_presences
        presence_results = await tune_missing_presences(package, today)
    finally:
        set_dispatch_running(False)

    summary = {
        "status": "completed",
        "date": today,
        "frequency": getattr(package, "frequency", None),
        "team_missing": sorted(missing_team),
        "team_results": team_results,
        "presence_results": presence_results,
    }
    print(f"{ts_log()} [{label}] Done — team_missing={len(missing_team)}, "
          f"presences_processed={len(presence_results)}")
    return summary
