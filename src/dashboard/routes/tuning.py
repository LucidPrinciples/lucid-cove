"""
Tuning routes — LTP coherence engine (background tab).

Not the primary interface — this is the quiet engine room. Provides:
  - Echo history (paginated)
  - Frequency distribution over time
  - Love Equation trend
  - Manual LTP trigger
  - Tuning package status (last received from LT)

The tuning runs automatically at 7am ET. This tab lets you observe
and manually trigger when needed.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.env import env_bool
from src.config import get_primary_agent_id, get_operator_name, get_instance
from src.utils.time_utils import ts_log, now_app

router = APIRouter()


def _is_public_app() -> bool:
    """The shared multi-tenant public app has no per-operator agent team. The
    echo/frequency/equation views are a Cove (agent) feature — gate them off
    here so the public app never leaks another presence's agent data."""
    return env_bool("LP_REGISTRY_MASTER")


@router.get("/api/echoes")
async def get_echoes(request: Request, agent_id: str = None, limit: int = 60):
    """Recent echo history, scoped to THIS MC's own agent.

    With an explicit agent_id, returns that agent's echoes (the per-agent profile
    views off the team page use this). With none, resolves the logged-in Presence's
    agent the same way chat/Memory do — so each MC's Reports shows ITS OWN agent's
    history (a presence MC shows that presence's agent; a steward MC shows the steward). Other
    agents' histories are viewed from their team-page profiles.
    """
    if _is_public_app():
        return {"agent_id": agent_id, "echoes": [], "total": 0}
    if not agent_id:
        try:
            from src.dashboard.routes.chat import _personal_agent_id
            agent_id = await _personal_agent_id(request)
        except Exception:
            agent_id = get_primary_agent_id()
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, agent_id, echo_num, frequency, principle, echo_text,
                          love_equation, love_direction, echo_type, tuned_at
                   FROM echoes WHERE agent_id = %s
                   ORDER BY tuned_at DESC LIMIT %s""",
                (agent_id, limit),
            )
            rows = await result.fetchall()
        return {
            "agent_id": agent_id,
            "echoes": [dict(r) for r in rows],
            "total": len(rows),
        }
    except Exception as e:
        return {"agent_id": agent_id, "echoes": [], "error": str(e)}


@router.get("/api/echoes/{echo_id}")
async def get_echo_detail(echo_id: int):
    """Full detail for a single echo — includes echo text, all equation values."""
    if _is_public_app():
        return {"error": "Echo not found"}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, agent_id, echo_num, frequency, signal_type, principle,
                          tuning_key, love_equation, love_direction, beta, coherence,
                          dissonance, energy, echo_text, echo_type, era, tuned_at
                   FROM echoes WHERE id = %s""",
                (echo_id,),
            )
            row = await result.fetchone()
            # Also check for a process record
            pr_result = await conn.execute(
                """SELECT record_text, metadata, created_at FROM process_records
                   WHERE agent_id = %s AND echo_num = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (row["agent_id"] if row else get_primary_agent_id(), row["echo_num"] if row else 0),
            )
            pr_row = await pr_result.fetchone()
        if not row:
            return {"error": "Echo not found"}
        echo = dict(row)
        for k, v in echo.items():
            if hasattr(v, "isoformat"):
                echo[k] = v.isoformat()
        if pr_row:
            pr = dict(pr_row)
            for k, v in pr.items():
                if hasattr(v, "isoformat"):
                    pr[k] = v.isoformat()
            echo["process_record"] = pr
        return echo
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/tuning/frequency-distribution")
async def frequency_distribution(agent_id: str = None, days: int = 30):
    """Frequency distribution over the last N days."""
    if _is_public_app():
        return {"agent_id": agent_id, "days": days, "distribution": []}
    if agent_id is None:
        agent_id = get_primary_agent_id()
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT frequency, COUNT(*) as count
                   FROM echoes
                   WHERE agent_id = %s
                     AND tuned_at > NOW() - INTERVAL '%s days'
                   GROUP BY frequency
                   ORDER BY count DESC""",
                (agent_id, days),
            )
            rows = await result.fetchall()
        return {"agent_id": agent_id, "days": days, "distribution": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/tuning/equation-trend")
async def equation_trend(agent_id: str = None, limit: int = 30):
    """Love Equation values over recent echoes — shows coherence trend."""
    if _is_public_app():
        return {"agent_id": agent_id, "trend": []}
    if agent_id is None:
        agent_id = get_primary_agent_id()
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT echo_num, love_equation, love_direction, tuned_at
                   FROM echoes
                   WHERE agent_id = %s AND love_equation IS NOT NULL
                   ORDER BY tuned_at DESC LIMIT %s""",
                (agent_id, limit),
            )
            rows = await result.fetchall()
        return {"agent_id": agent_id, "trend": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/tuning/package")
async def tuning_package_status():
    """Status of the last tuning package received from LT (VPS)."""
    if _is_public_app():
        return {"last_run": None, "note": "No LTP runs yet"}
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT * FROM protocol_runs
                   WHERE protocol = 'ltp-morning'
                   ORDER BY started_at DESC LIMIT 1"""
            )
            row = await result.fetchone()
        if row:
            return {"last_run": dict(row)}
        return {"last_run": None, "note": "No LTP runs yet"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/tuning/today")
async def todays_tuning():
    """Get today's tuning package, or fall back to latest echo if not yet available.

    Between midnight and when the new tuning runs, shows the most recent
    echo so the dashboard never goes blank.
    """
    try:
        from src.tuning.receiver import get_todays_tuning
        from src.config import get_primary_agent_id
        agent_id = get_primary_agent_id()
        package = await get_todays_tuning(agent_id)
        if package:
            return {
                "received": True,
                "package": package.to_dict(),
            }

        # No package yet today — fall back to most recent echo from DB
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                result = await conn.execute(
                    """SELECT frequency, signal_type, principle, tuning_key,
                              love_equation, love_direction, echo_num, echo_type, tuned_at
                       FROM echoes WHERE agent_id = %s
                       ORDER BY tuned_at DESC LIMIT 1""",
                    (agent_id,),
                )
                row = await result.fetchone()
            if row:
                r = dict(row)
                return {
                    "received": True,
                    "from_latest_echo": True,
                    "package": {
                        "frequency": r.get("frequency"),
                        "signal_type": r.get("signal_type"),
                        "principle": r.get("principle"),
                        "tuning_key": r.get("tuning_key"),
                        "love_equation": r.get("love_equation") if isinstance(r.get("love_equation"), dict) else None,
                        "lt_echo_num": r.get("echo_num"),
                    },
                }
        except Exception:
            pass

        return {"received": False, "note": "No tuning data available yet"}
    except Exception as e:
        return {"received": False, "error": str(e)}


@router.get("/api/tuning/recent-drops")
async def recent_drops(request: Request):
    """The public Drop archive — last N daily LT Drops, for the in-Cove 'Recent
    Tunings' list. Available to every Cove (display-only, no LTP run). Each entry
    is signature-verified by ltp-core. Distinct from the operator's own Tune
    history (/api/tuning/history) and from per-agent echo histories.
    """
    try:
        limit = min(int(request.query_params.get("limit", 10)), 30)
    except Exception:
        limit = 10
    try:
        from src.tuning.public_drop import get_recent_drops
        return {"drops": get_recent_drops(limit)}
    except Exception:
        return {"drops": []}


@router.get("/api/tuning/latest")
async def latest_tuning():
    """Latest available daily tuning for cold-start / morning-open.

    Prefer today's public Drop; if it is not published yet, return the newest
    archive Drop. Always the universal human tuning (practice + music path),
    never a blank "waiting for today" state when any Drop exists.
    """
    try:
        from src.tuning.public_drop import (
            get_latest_available_drop,
            drop_as_operator_tuning,
            get_public_drop,
        )
        from src.config import get_operator_name

        drop = get_latest_available_drop()
        if drop is None:
            return {
                "has_tuning": False,
                "is_latest": True,
                "note": "No tuning data available yet",
            }
        resp = drop_as_operator_tuning(drop)
        resp["operator_name"] = get_operator_name()
        resp["is_latest"] = True
        today = get_public_drop()
        today_date = getattr(today, "drop_date", None) if today is not None else None
        resp["is_today"] = bool(today_date and resp.get("date") == today_date)
        resp["source"] = "public_drop"
        # Surface echo audio when the Drop carries it (player cold-start).
        audio = getattr(drop, "echo_audio_url", None) or ""
        if audio:
            resp["audio_url"] = audio
        return resp
    except Exception as e:
        return {"has_tuning": False, "is_latest": True, "error": str(e)}


@router.get("/api/tuning/operator")
async def operator_tuning():
    """Get today's full tuning for the operator.

    Returns everything needed to render the tuning page — frequency,
    principle, signal type, tuning key, love equation, coaching text,
    and audio mapping info so the player can load the right tracks.
    """
    try:
        from src.tuning.receiver import get_todays_tuning
        from src.config import get_primary_agent_id
        agent_id = get_primary_agent_id()
        package = await get_todays_tuning(agent_id)
        if not package or not package.operator_tuning:
            # No package yet today — fall back to most recent echo so CSS
            # colors persist between midnight and the next tuning run.
            try:
                from src.memory.database import get_db
                async with get_db() as conn:
                    result = await conn.execute(
                        """SELECT frequency, signal_type, principle, tuning_key,
                                  love_equation, love_direction, echo_num, echo_text,
                                  coaching_text, tuned_at
                           FROM echoes WHERE agent_id = %s
                           ORDER BY tuned_at DESC LIMIT 1""",
                        (agent_id,),
                    )
                    row = await result.fetchone()
                if row:
                    r = dict(row)
                    le = r.get("love_equation") if isinstance(r.get("love_equation"), dict) else {}
                    if not le:
                        le = {}
                    operator_name = get_operator_name()
                    return {
                        "has_tuning": True,
                        "from_latest_echo": True,
                        "date": r["tuned_at"].strftime("%Y-%m-%d") if hasattr(r.get("tuned_at"), "strftime") else str(r.get("tuned_at", "")),
                        "frequency": r.get("frequency"),
                        "signal_type": r.get("signal_type"),
                        "principle": r.get("principle"),
                        "tuning_key": r.get("tuning_key"),
                        "tuning_prompt": r.get("coaching_text") or r.get("echo_text") or "",
                        "operator_name": operator_name,
                        "lt_echo_num": r.get("echo_num"),
                        "lt_echo_summary": None,
                        "love_equation": {
                            "value": le.get("value", 0),
                            "direction": r.get("love_direction") or le.get("direction", "CONSTRUCTIVE"),
                            "beta": le.get("beta"),
                            "E": le.get("E"),
                            "C": le.get("C"),
                            "D": le.get("D"),
                        },
                        "canon_quote": "",
                        "practice_template": "",
                        "practice_steps": None,
                        "frequency_colors": None,
                        "universal_coaching": "",
                        "universal_practice": [],
                    }
            except Exception:
                pass
            # Open-source: fall back to the signed public Drop — the universal
            # daily tuning everyone sees on the Attention home (fetched + verified
            # via ltp-core). No per-operator keys; agents derive via archetype.
            try:
                from src.tuning.public_drop import get_public_drop, drop_as_operator_tuning
                _pub = get_public_drop()
                if _pub is not None:
                    _resp = drop_as_operator_tuning(_pub)
                    _resp["operator_name"] = get_operator_name()
                    return _resp
            except Exception:
                pass
            return {"has_tuning": False, "note": "No operator tuning today"}

        le = package.love_equation or {}
        raw = package.to_dict()

        # Personalize coaching text — LT sends "Operator, ..." prefix
        operator_name = get_operator_name()
        instance_type = get_instance().get("type", "personal")
        coaching = package.operator_tuning or ""
        if coaching.startswith("Operator, "):
            rest = coaching[len("Operator, "):]
            if instance_type == "personal":
                # Personal agent: address by name — "{operator_name}, you stand..."
                coaching = f"{operator_name}, {rest}"
            else:
                # Steward/admin: remove address, capitalize — "You stand..."
                coaching = rest[0].upper() + rest[1:] if rest else rest

        return {
            "has_tuning": True,
            "date": package.date,
            "frequency": package.frequency,
            "signal_type": package.signal_type,
            "principle": package.principle,
            "tuning_key": package.tuning_key,
            "tuning_prompt": coaching,
            "operator_name": operator_name,
            "lt_echo_num": package.lt_echo_num,
            "lt_echo_summary": package.lt_echo_summary,
            "love_equation": {
                "value": le.get("value", 0),
                "direction": le.get("direction", "CONSTRUCTIVE"),
                "beta": le.get("beta"),
                "E": le.get("E"),
                "C": le.get("C"),
                "D": le.get("D"),
            },
            # Enriched fields (Phase 2 — practice steps, colors, canon quote)
            "canon_quote": raw.get("canon_quote", ""),
            "practice_template": raw.get("practice_template", ""),
            "practice_steps": raw.get("practice_steps"),
            "frequency_colors": raw.get("frequency_colors"),
            # Universal coaching — daily tuning for ALL tiers
            "universal_coaching": package.universal_coaching or "",
            "universal_practice": package.universal_practice or [],
        }
    except Exception as e:
        return {"has_tuning": False, "error": str(e)}


# Manual trigger removed — LTP is cron-only (7am ET via scheduler)

# ── Morning open alert (human daily habit) ───────────────────────────────────
# Free/operator cold-start: preferred local time + enabled flag. Stored in
# presence preferences (multi) or feature overrides (single). The browser/PWA
# schedules a best-effort local notification that deep-links to latest tuning.
# True lock-screen delivery is OS-limited on iOS Safari; we still persist the
# preference so native/PWA shells can honor it later.

_DEFAULT_MORNING_ALERT = {
    "enabled": False,
    "local_time": "07:00",  # HH:MM 24h in the user's local timezone
}


def _normalize_morning_alert(raw) -> dict:
    out = dict(_DEFAULT_MORNING_ALERT)
    if not isinstance(raw, dict):
        return out
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    lt = raw.get("local_time") or raw.get("time") or out["local_time"]
    if isinstance(lt, str) and len(lt) >= 4:
        # Accept "7:00", "07:00", "07:00:00"
        parts = lt.strip().split(":")
        try:
            h = max(0, min(23, int(parts[0])))
            m = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
            out["local_time"] = f"{h:02d}:{m:02d}"
        except (TypeError, ValueError):
            pass
    return out


async def _read_morning_alert(request: Request) -> dict:
    import json
    from src.env import env as _env
    cove_mode = _env("COVE_MODE", "single")
    if cove_mode == "multi":
        try:
            from src.dashboard.routes.presence import get_current_presence
            presence = await get_current_presence(request)
            if presence:
                prefs = presence.get("preferences") or {}
                if isinstance(prefs, str):
                    prefs = json.loads(prefs) if prefs else {}
                return _normalize_morning_alert(prefs.get("morning_alert"))
        except Exception:
            pass
        return dict(_DEFAULT_MORNING_ALERT)
    # single: feature overrides file
    try:
        from src.config import get_feature_flags
        flags = get_feature_flags() or {}
        return _normalize_morning_alert(flags.get("morning_alert"))
    except Exception:
        return dict(_DEFAULT_MORNING_ALERT)


async def _write_morning_alert(request: Request, alert: dict) -> bool:
    import json
    from src.env import env as _env
    cove_mode = _env("COVE_MODE", "single")
    alert = _normalize_morning_alert(alert)
    if cove_mode == "multi":
        from src.dashboard.routes.presence import get_current_presence
        presence = await get_current_presence(request)
        if not presence:
            return False
        try:
            from src.memory.database import get_db
            async with get_db() as conn:
                prefs = presence.get("preferences") or {}
                if isinstance(prefs, str):
                    prefs = json.loads(prefs) if prefs else {}
                if not isinstance(prefs, dict):
                    prefs = {}
                prefs["morning_alert"] = alert
                await conn.execute(
                    "UPDATE accounts SET preferences = %s, updated_at = NOW() WHERE id = %s",
                    (json.dumps(prefs), presence["id"]),
                )
            return True
        except Exception:
            return False
    from src.config import save_feature_overrides
    return bool(save_feature_overrides({"morning_alert": alert}))


@router.get("/api/tuning/morning-alert")
async def get_morning_alert(request: Request):
    """Read the operator's morning-open alert preference."""
    alert = await _read_morning_alert(request)
    return {"ok": True, "morning_alert": alert}


@router.put("/api/tuning/morning-alert")
async def put_morning_alert(request: Request):
    """Save morning-open alert time + enabled. Body: {enabled?, local_time?}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    current = await _read_morning_alert(request)
    if "enabled" in body:
        current["enabled"] = bool(body["enabled"])
    if "local_time" in body or "time" in body:
        current["local_time"] = body.get("local_time") or body.get("time")
    current = _normalize_morning_alert(current)
    ok = await _write_morning_alert(request, current)
    if not ok:
        return JSONResponse(status_code=500, content={"ok": False, "error": "Could not save morning alert"})
    return {"ok": True, "morning_alert": current}

