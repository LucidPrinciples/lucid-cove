"""Tuning metadata endpoints — frequencies, contexts, history, session updates, events, streak.

Read-only reference endpoints plus session lifecycle (journal, signal check-in,
event tracking, streak).
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from .helpers import (
    _get_presence_id,
    _load_lt_reference,
    CONTEXT_SIGNAL_MAP,
)

router = APIRouter()


@router.get("/api/tuning/frequencies")
async def list_frequencies():
    """Return all available frequencies with their signal types."""
    try:
        ref = _load_lt_reference()
        freqs = []
        for name in ref["all_frequencies"]:
            data = ref["frequencies"][name]
            freqs.append({
                "name": name,
                "signal_type": data["signal_type"],
                "tuning_key_count": len(data.get("tuning_keys", [])),
            })
        return {"frequencies": freqs}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/tuning/contexts")
async def list_contexts():
    """Return all available contexts with their allowed signal types."""
    return {
        "contexts": [
            {"name": name, "allowed_signals": signals}
            for name, signals in CONTEXT_SIGNAL_MAP.items()
        ]
    }


# =============================================================================
# Tuning History
# =============================================================================

@router.get("/api/tuning/history")
async def tuning_history(request: Request):
    """Return recent tuning sessions for the current user."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    limit = min(int(request.query_params.get("limit", 30)), 100)
    offset = int(request.query_params.get("offset", 0))

    try:
        async with get_db() as conn:
            where = "1=1"
            params = []
            if presence_id:
                where = "presence_id = %s"
                params.append(presence_id)

            result = await conn.execute(
                f"""SELECT session_id, date, time, day_of_week,
                           entry_mode, initial_state, context,
                           principle_served as principle,
                           frequency_category as frequency,
                           echo_filename, echo_full_name, echo_album,
                           echo_signal_type as signal_type,
                           tuning_key_primary as tuning_key,
                           bpm, selection_method,
                           signal_before, signal_after,
                           journal_text, journal_at,
                           created_at
                    FROM tuning_sessions
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            rows = await result.fetchall()

            # Build audio URLs
            try:
                ref = _load_lt_reference()
                base_url = ref["audio_base_url"]
            except Exception:
                base_url = ""

            sessions = []
            for row in rows:
                r = dict(row)
                st = r.get("signal_type") or ""
                fn = (r.get("echo_filename") or "").replace(".mp3", "")
                r["audio_url"] = f"{base_url}/{st}_Signal/{fn}.mp3" if st and fn and base_url else ""
                # Serialize datetimes
                for k in ("created_at", "journal_at"):
                    if r.get(k) and hasattr(r[k], "isoformat"):
                        r[k] = r[k].isoformat()
                sessions.append(r)

            return {"sessions": sessions, "count": len(sessions), "offset": offset}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# =============================================================================
# Session Update (journal, signal check-in)
# =============================================================================

@router.patch("/api/tuning/session/{session_id}")
async def update_session(session_id: str, request: Request):
    """Update a tuning session with journal entry or signal check-in."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not body:
        raise HTTPException(status_code=400, detail="No update data provided")

    # Build SET clause dynamically from allowed fields
    allowed = {
        "signal_before": "signal_before",
        "signal_after": "signal_after",
        "journal_text": "journal_text",
        "entry_mode": "entry_mode",
        "initial_state": "initial_state",
        "end_state": "end_state",
    }

    sets = []
    params = []
    for body_key, col in allowed.items():
        if body_key in body:
            sets.append(f"{col} = %s")
            params.append(body[body_key])

    # Auto-set journal_at when journal_text is provided
    if "journal_text" in body:
        sets.append("journal_at = %s")
        params.append(datetime.now(timezone.utc))

    # Always touch updated_at
    sets.append("updated_at = NOW()")

    if not sets:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Scope to session_id + presence
    where = "session_id = %s"
    params.append(session_id)
    if presence_id:
        where += " AND presence_id = %s"
        params.append(presence_id)

    try:
        async with get_db() as conn:
            result = await conn.execute(
                f"UPDATE tuning_sessions SET {', '.join(sets)} WHERE {where} RETURNING session_id",
                params,
            )
            row = await result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")
            return {"updated": True, "session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# =============================================================================
# Tuning Events (granular tracking)
# =============================================================================

@router.post("/api/tuning/event")
async def log_tuning_event(request: Request):
    """Log a granular tuning event (play, pause, complete, streak, etc)."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    event_type = (body.get("event_type") or "").strip()
    if not event_type:
        return JSONResponse(status_code=400, content={"error": "event_type required"})

    now = datetime.now(timezone.utc)
    event_data = body.get("event_data") or {}

    try:
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO tuning_events (
                    presence_id,
                    event_type, event_data, session_id,
                    echo_name, echo_album, principle, frequency, signal_type,
                    context, bpm, play_duration, position_in_playlist,
                    tuning_key, play_source,
                    quantum_selection, selection_method,
                    user_tier, source_platform,
                    date, time
                ) VALUES (
                    %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s
                )""",
                (
                    presence_id,
                    event_type, json.dumps(event_data), body.get("session_id"),
                    body.get("echo_name"), body.get("echo_album"),
                    body.get("principle"), body.get("frequency"), body.get("signal_type"),
                    body.get("context"), body.get("bpm"),
                    body.get("play_duration"), body.get("position_in_playlist"),
                    body.get("tuning_key"), body.get("play_source"),
                    bool(body.get("quantum_selection")), body.get("selection_method"),
                    "tuner", "web",
                    now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                ),
            )
        return {"logged": True, "event_type": event_type}
    except Exception as e:
        # Best-effort -- don't fail client
        return JSONResponse(status_code=500, content={"error": str(e)})


# =============================================================================
# Streak
# =============================================================================

@router.get("/api/tuning/streak")
async def get_streak(request: Request):
    """Get the current user's tuning streak info."""
    from src.memory.database import get_db

    presence_id = await _get_presence_id(request)
    if not presence_id:
        return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0, "this_month_sessions": 0}

    try:
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT current_streak, longest_streak, last_tuning_date,
                          total_sessions, this_month_sessions
                   FROM tuning_streaks
                   WHERE presence_id = %s""",
                (presence_id,),
            )
            row = await result.fetchone()
            if row:
                return dict(row)
            return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0, "this_month_sessions": 0}
    except Exception:
        return {"current_streak": 0, "longest_streak": 0, "total_sessions": 0, "this_month_sessions": 0}
