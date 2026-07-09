"""
Tuning dedup — the ONE definition of "already tuned today".

Both the sweep (src/tuning/sweep.py) and the team dispatcher
(src/graphs/ltp/dispatch.py) must agree on what "tuned" means, or they fight:
the sweep flags an agent as missing while the dispatcher vetoes the re-tune
(the 2026-07-08 deadlock — team_missing=10 all day, zero dispatched).

Definition: an agent is "tuned today" only if it has an echo dated today AND
tuned off the CURRENT Drop (matched on frequency/principle). An agent that
tuned earlier today off a STALE Drop (e.g. a post-midnight boot catch-up
before today's real Drop published) is NOT done — it re-tunes off the real
key. Falls back to date-only when the Drop's key can't be identified.

LTP Protocol Spec §6 conformance: the spec's dedup guard exists so the Cove
tunes as a coherent unit — "everyone tunes together on the same frequency."
Keying dedup to the day's actual frequency is that requirement, stated
precisely; date-only was an approximation that broke on stale-Drop days.
"""

from src.memory.database import get_db


async def tuned_today(today: str, freq: str = "", principle: str = "") -> set:
    """Agent ids that already have TODAY's echo tuned off the CURRENT Drop.

    Keyed on the date AND the Drop's frequency/principle. Falls back to
    date-only if the Drop's key can't be identified (freq empty)."""
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
