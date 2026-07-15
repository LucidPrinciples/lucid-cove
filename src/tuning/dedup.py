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

"Today" is the Cove calendar day (app timezone), NOT the Postgres UTC date of
tuned_at. Bug (Mann / Jules 2026-07-15): after ~20:00 America/New_York,
`tuned_at::date` (UTC) is already the *next* calendar day while today_app()
is still yesterday local — every 30m safety sweep re-tuned the whole Cove
(three Courage echoes overnight). Compare
`(tuned_at AT TIME ZONE cove_tz)::date` to today_app() instead.

LTP Protocol Spec §6 conformance: the spec's dedup guard exists so the Cove
tunes as a coherent unit — "everyone tunes together on the same frequency."
Keying dedup to the day's actual frequency is that requirement, stated
precisely; date-only was an approximation that broke on stale-Drop days.
"""

from src.memory.database import get_db


def _cove_tz_name() -> str:
    """IANA timezone for Cove calendar-day boundaries (matches today_app())."""
    try:
        from src.config import get_instance
        return (get_instance().get("timezone") or "America/New_York").strip() or "America/New_York"
    except Exception:
        try:
            from src.env import env
            return (env("APP_TIMEZONE") or "America/New_York").strip() or "America/New_York"
        except Exception:
            return "America/New_York"


async def tuned_today(today: str, freq: str = "", principle: str = "") -> set:
    """Agent ids that already have TODAY's echo tuned off the CURRENT Drop.

    Keyed on the Cove calendar date AND the Drop's frequency/principle. Falls
    back to date-only if the Drop's key can't be identified (freq empty).

    `today` must be today_app() (Cove-local YYYY-MM-DD). Echo timestamps are
    stored UTC; we convert to Cove local before taking ::date so evening
    ET/PT does not look "untuned" and re-fire the 30m sweep.
    """
    freq = (freq or "").strip()
    principle = (principle or "").strip()
    tz = _cove_tz_name()
    # timestamptz AT TIME ZONE zone → timestamp without tz in that zone, then ::date
    day_expr = "(tuned_at AT TIME ZONE %s)::date = %s::date"
    async with get_db() as conn:
        if freq and principle:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                f"WHERE {day_expr} AND frequency = %s AND principle = %s",
                (tz, today, freq, principle))
        elif freq:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                f"WHERE {day_expr} AND frequency = %s",
                (tz, today, freq))
        else:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                f"WHERE {day_expr}",
                (tz, today))
        return {row["agent_id"] for row in await r.fetchall()}
