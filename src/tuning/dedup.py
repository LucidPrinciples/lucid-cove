"""
Tuning dedup — the ONE definition of "already applied this Drop".

Both the sweep (src/tuning/sweep.py) and the team dispatcher
(src/graphs/ltp/dispatch.py) must agree on what "tuned" means, or they fight:
the sweep flags an agent as missing while the dispatcher vetoes the re-tune
(the 2026-07-08 deadlock — team_missing=10 all day, zero dispatched).

Policy (2026-07-15, operator): backend tuning is keyed to the LATEST Drop, not
to "today" / "tomorrow" / any calendar boundary. Calendar-day dedup has regressed
multiple times (UTC vs Cove-local after ~20:00 ET → triple overnight Courage
echoes on Mann). Day boundaries are the wrong axis.

Definition: an agent has applied a Drop when it has ANY echo whose package
identity matches that Drop:
  frequency + principle + tuning_key  (preferred — key is the Drop phrase)
  frequency + principle                (fallback when key missing)
  (optional) lt_echo_num via echo text is NOT required — key is enough

No `tuned_at` date comparison. Ever.
  • Same Drop already applied → never re-run.
  • New Drop (new key / freq / principle) → not applied yet → run.
  • Reliability of "what is latest" is entirely the LT Drop / receiver.

`tuned_today(...)` remains the public name for call-site stability; the `today`
argument is IGNORED when package identity is present (kept only so old callers
do not break). Prefer `tuned_for_package(...)`.
"""

from src.memory.database import get_db


async def tuned_for_package(
    freq: str = "",
    principle: str = "",
    tuning_key: str = "",
) -> set:
    """Agent ids that already tuned to THIS Drop, in the current cycle.

    Match on package identity (freq/principle/key) AND a recency window. The
    22-frequency Drop REPEATS (~3-week cycle), so identity-only matching falsely
    dedups an agent that tuned to this same Drop a cycle ago — established agents
    then never re-tune when a frequency comes back around, while brand-new
    presences (no prior cycle) tune. The 20h window means "this cycle's tune",
    not "ever", without the midnight/timezone fragility of a calendar-day match.
    """
    freq = (freq or "").strip()
    principle = (principle or "").strip()
    key = (tuning_key or "").strip()

    if not freq and not principle and not key:
        # No Drop identity — cannot claim anyone is "done". Empty set forces a
        # pass only when a real package is present at the call site.
        return set()

    async with get_db() as conn:
        if key and freq and principle:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                "WHERE frequency = %s AND principle = %s AND tuning_key = %s AND created_at > NOW() - INTERVAL '20 hours'",
                (freq, principle, key),
            )
        elif key and freq:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                "WHERE frequency = %s AND tuning_key = %s AND created_at > NOW() - INTERVAL '20 hours'",
                (freq, key),
            )
        elif key:
            # Key alone is the Drop phrase — strongest single field when present.
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes WHERE tuning_key = %s AND created_at > NOW() - INTERVAL '20 hours'",
                (key,),
            )
        elif freq and principle:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                "WHERE frequency = %s AND principle = %s AND created_at > NOW() - INTERVAL '20 hours'",
                (freq, principle),
            )
        else:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes WHERE frequency = %s AND created_at > NOW() - INTERVAL '20 hours'",
                (freq,),
            )
        return {row["agent_id"] for row in await r.fetchall()}


async def tuned_today(
    today: str = "",
    freq: str = "",
    principle: str = "",
    tuning_key: str = "",
) -> set:
    """Backward-compatible name. `today` is ignored — package identity only.

    When callers pass no package fields (watcher legacy), return agents that
    have *any* echo — that is NOT "applied latest Drop". Prefer callers that
    pass freq/principle/key from get_todays_tuning / the current package.
    """
    # Explicit package identity → Drop-keyed dedup (the only correct path).
    if (freq or "").strip() or (principle or "").strip() or (tuning_key or "").strip():
        return await tuned_for_package(freq, principle, tuning_key)

    # No package identity: do not invent "today". Return empty so the sweep
    # path that forgot to pass package fields fails open to "maybe missing"
    # rather than "everyone done" or date-based false negatives. Callers that
    # need a real answer must load the package first.
    return set()
