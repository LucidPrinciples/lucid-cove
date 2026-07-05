# =============================================================================
# carry.py — pure logic for the carry-on-upgrade import (CF-65).
# =============================================================================
# Framework-free on purpose (no fastapi/httpx/DB) so the unit tests can import
# it anywhere. The route/IO half lives in src/dashboard/routes/carry_import.py.
# =============================================================================
from datetime import date, datetime
from uuid import UUID

# Columns never copied across on import: `id` is the exporter's serial/cursor;
# the local row gets its own. `presence_id` is remapped to the LOCAL presence.
EXCLUDED_COLUMNS = {"id", "presence_id"}

# accounts.preferences keys that are ACCOUNT-level personalization, safe to
# carry into a Cove. Everything currently stored in that blob is Cove/instance
# level (feature toggles incl. tokens, posting platform keys, action-board
# links) or secret-bearing, so the safe default is to carry NONE of it — when
# unsure about a key, drop it. The tuning personalization that matters lives in
# the tuning_preferences TABLE, which carries fully. Extend this tuple as
# genuinely account-level keys appear.
ACCOUNT_PREFS_KEEP: tuple = ()


def jsonable(row: dict) -> dict:
    """Make a DB row JSON-serializable (datetimes -> isoformat, UUIDs -> str)."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def intersect_columns(row_keys, local_columns) -> list:
    """The explicit column list an import INSERT may use: exporter keys that
    exist locally, minus the excluded set. Sorted for a stable SQL shape."""
    return sorted(k for k in row_keys if k in local_columns and k not in EXCLUDED_COLUMNS)


def streak_from_date_counts(date_counts: dict, today_iso: str) -> dict:
    """Pure streak math over {'YYYY-MM-DD': session_count} — recompute, don't
    copy. current_streak = consecutive-day run ending at the last tuning date,
    but 0 if that run already broke (last date before yesterday)."""
    days = []
    for d in date_counts:
        try:
            days.append(date.fromisoformat(str(d)[:10]))
        except Exception:
            continue  # malformed legacy date strings don't break the recompute
    days.sort()
    if not days:
        return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0,
                "this_month_sessions": 0, "last_tuning_date": None}
    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    # Run ending at the most recent date:
    ending = 1
    for prev, cur in zip(reversed(days[:-1]), reversed(days[1:])):
        if (cur - prev).days == 1:
            ending += 1
        else:
            break
    try:
        today_d = date.fromisoformat(today_iso)
    except Exception:
        today_d = date.today()
    current = ending if (today_d - days[-1]).days <= 1 else 0
    month_prefix = today_iso[:7]
    valid = {str(d)[:10] for d in date_counts
             if _is_iso_date(str(d)[:10])}
    this_month = sum(int(c or 0) for d, c in date_counts.items()
                     if str(d)[:10] in valid and str(d)[:7] == month_prefix)
    total = sum(int(c or 0) for d, c in date_counts.items()
                if str(d)[:10] in valid)
    return {"current_streak": current, "longest_streak": longest,
            "total_sessions": total, "this_month_sessions": this_month,
            "last_tuning_date": days[-1].isoformat()}


def _is_iso_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except Exception:
        return False
