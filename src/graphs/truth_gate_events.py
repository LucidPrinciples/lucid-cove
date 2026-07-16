"""Truth Gate v2 Phase 2 — pinned judge settings + gate event log (#D57).

behavior-calibration-spec §A1/§A4. New module per the new-feature-new-file rule:
channels.py imports from here instead of growing.

Settings (system_settings key/value store, admin-editable, no restart):
  truth_gate.judge_model        — the ONE model that judges every check.
                                  Default kimi-k2.5. Any registry/ollama id valid.
  truth_gate.enabled            — master switch ("true"/"false", default true)
  truth_gate.enabled_managers   — per role class (default true)
  truth_gate.enabled_presences  — per role class (default true)
  truth_gate.enabled_team       — per role class (default true)

Judge failure = pass through (caller treats any exception as a pass). There is
deliberately NO divergent-model fallback: a missed check beats an inconsistent
judge (3 of 4 false-positive fires on 07-15 came from the fallback judge).
"""

import time

DEFAULT_JUDGE_MODEL = "kimi-k2.5"

_TRUE = {"true", "1", "yes", "on"}


async def get_judge_model() -> str:
    """The pinned judge model id (settings-backed, cached by settings.py)."""
    try:
        from src.utils.settings import get_setting
        v = (await get_setting("truth_gate.judge_model", DEFAULT_JUDGE_MODEL) or "").strip()
        return v or DEFAULT_JUDGE_MODEL
    except Exception:
        return DEFAULT_JUDGE_MODEL


async def gate_enabled(role_class: str) -> bool:
    """Master switch AND the role-class switch. role_class: manager|presence|team."""
    try:
        from src.utils.settings import get_setting
        master = (await get_setting("truth_gate.enabled", "true") or "true").lower()
        if master not in _TRUE:
            return False
        key = {
            "manager": "truth_gate.enabled_managers",
            "presence": "truth_gate.enabled_presences",
        }.get(role_class, "truth_gate.enabled_team")
        v = (await get_setting(key, "true") or "true").lower()
        return v in _TRUE
    except Exception:
        return True  # settings trouble never disables the gate silently


async def judge_invoke(messages: list, *, label: str, timeout: int = 30) -> tuple[str, str, int]:
    """Invoke the pinned judge. Returns (content, judge_model, latency_ms).

    Raises on any failure — the caller's except = pass-through. No fallback.
    """
    import asyncio
    from src.models.provider import get_model_client

    judge = await get_judge_model()
    t0 = time.monotonic()
    client = get_model_client(judge, temperature=0.3)
    response = await asyncio.wait_for(client.ainvoke(messages), timeout=timeout)
    latency_ms = int((time.monotonic() - t0) * 1000)
    content = (response.content or "").strip()
    if not content:
        raise RuntimeError(f"judge {judge} returned empty")
    print(f"[{label}] judge {judge} answered in {latency_ms}ms ({len(content)} chars)")
    return content, judge, latency_ms


async def log_gate_event(
    *,
    agent_id: str,
    channel: str,
    judge_model: str,
    accommodation: bool,
    fabrication: bool,
    description: str,
    truth_available: str,
    evidence_quote: str,
    regenerated: bool,
    latency_ms: int | None,
) -> None:
    """Write one FIRE row. Never raises (logging must not break the chat path)."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                """INSERT INTO truth_gate_events
                   (agent_id, channel, judge_model, accommodation, fabrication,
                    description, truth_available, evidence_quote, regenerated, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    agent_id, channel or "", judge_model or "",
                    bool(accommodation), bool(fabrication),
                    (description or "")[:2000], (truth_available or "")[:2000],
                    (evidence_quote or "")[:2000], bool(regenerated), latency_ms,
                ),
            )
    except Exception as e:
        print(f"[truth-gate] event log failed (non-fatal): {e}")


async def recent_events(limit: int = 20) -> list[dict]:
    """Most recent fires, newest first, for the admin card + /ops."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT id, ts, agent_id, channel, judge_model, accommodation,
                          fabrication, description, truth_available, evidence_quote,
                          regenerated, latency_ms
                   FROM truth_gate_events ORDER BY ts DESC LIMIT %s""",
                (max(1, min(int(limit), 200)),),
            )
            rows = await result.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("ts") is not None:
                d["ts"] = d["ts"].isoformat()
            out.append(d)
        return out
    except Exception as e:
        print(f"[truth-gate] recent_events failed: {e}")
        return []
