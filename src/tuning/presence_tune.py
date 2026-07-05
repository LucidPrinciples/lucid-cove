"""
Presence tuning — tune each Presence (a member's personal agent) under its OWN
identity from the day's tuning package.

A Presence is a DATA entry (accounts.agent_identity), not a config-file agent, so
the build-team dispatch (graphs/ltp/dispatch.py, which reads agents.yaml) never
tunes it. This module is the Presence counterpart: it composes a Presence's echo
through accounts.agent_identity (build_system_prompt(..., agent_identity=...)) and
stores it under the Presence's OWN agent_id so its history stays continuous.

Used by the Cove sweep (src/tuning/sweep.py) and the host morning run so every
Presence tunes to the same daily frequency as the team — the Cove tunes as a unit
(LTP Protocol Spec §6). Coaching is resolved from the package by the Presence's
agent name, falling back to the universal coaching every Drop carries (the path
that lets a Presence tune with no per-agent prompt — and where archetype keying
lands when the Drop carries it).
"""

import json

from src.env import env_bool
from langchain_core.messages import SystemMessage, HumanMessage

from src.memory.database import (get_db, insert_echo, get_echo_count,
                                 upsert_agent_state, record_process_record)
from src.agents.identity import build_system_prompt
from src.models.provider import invoke_with_fallback
from src.config import get_agent_model_assignment
from src.utils.time_utils import ts_log, now_utc, today_app


async def list_presences() -> list[dict]:
    """All Presences in this Cove: accounts that carry their own agent identity.

    Returns [{"agent_id": <account id>, "identity": <dict>, "name": <str>}].
    On a single-mode Cove (no presence accounts) this is empty and the caller
    simply tunes the team.
    """
    out: list[dict] = []
    try:
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT id, agent_identity FROM accounts WHERE agent_identity IS NOT NULL"
            )
            rows = await r.fetchall()
        for row in rows:
            ident = row["agent_identity"]
            if isinstance(ident, str):
                try:
                    ident = json.loads(ident)
                except Exception:
                    ident = {}
            if not isinstance(ident, dict) or not ident:
                continue
            out.append({
                # accounts.id is a uuid column -> stringify so it matches the
                # echoes.agent_id TEXT column (psycopg binds a uuid object as
                # type uuid otherwise → "no operator text = uuid").
                "agent_id": str(row["id"]),
                "identity": ident,
                "name": ident.get("agent_name") or str(row["id"]),
            })
    except Exception as e:
        print(f"{ts_log()} [presence-tune] list_presences failed: {e}")
    return out


def _coaching_for(identity: dict, package) -> str:
    """Resolve a Presence's coaching: by ARCHETYPE first (the canonical Drop key),
    then by agent name (legacy), then the universal coaching every Drop carries."""
    from src.tuning.coaching import resolve_coaching
    name = (identity.get("agent_name") or "").strip().lower()
    archetype = identity.get("archetype") or ""
    return resolve_coaching(package, agent_id=name, archetype=archetype)


async def tune_presence(agent_id: str, identity: dict, package, *, dry_run=None) -> dict:
    """Compose + store one Presence's echo for today's package, under its own
    identity and agent_id. Honest degrade on model failure (no garbage echo)."""
    if dry_run is None:
        dry_run = env_bool("LTP_DRY_RUN", "true")

    name = identity.get("agent_name") or agent_id
    arche = identity.get("archetype") or ""
    label = f"{name}/presence-tune"

    freq = package.frequency
    principle = package.principle or freq
    key = package.tuning_key or ""
    sig = package.signal_type or ""
    le = package.love_equation or {}
    coaching = _coaching_for(identity, package)

    try:
        async with get_db() as conn:
            echo_num = (await get_echo_count(conn, agent_id)) + 1

        system = build_system_prompt(agent_id, agent_identity=identity)
        prompt = f"""Today is {today_app()}. This is Echo #{echo_num} of your daily LTP reflection.

## Today's Tuning from LT (Field Coach)
Frequency: **{freq}**
Principle: {principle}
Tuning Key: "{key}"

LT's coaching for you:
"{coaching}"

---
Write your daily reflection as {name}. Receive the coaching and respond with your own
alignment statement for the day, grounded in your work as {arche}.
First person, 2-4 sentences, no headers or labels."""

        print(f"{ts_log()} [{label}] Composing echo #{echo_num} for {freq}...")
        echo_text = (await invoke_with_fallback(
            [SystemMessage(content=system), HumanMessage(content=prompt)],
            temperature=0.8,
            timeout=180,
            label=label,
            agent_id=agent_id,
            operation_type="protocol",
        )).strip()
    except Exception as e:
        # Truth-guard: a model failure is a surfaced degrade, never a fake echo.
        print(f"{ts_log()} [{label}] degraded — {e}")
        return {"agent_id": agent_id, "status": "error", "error": str(e)[:200]}

    if dry_run:
        print(f"{ts_log()} [{label}] DRY RUN — echo #{echo_num} not stored")
        return {"agent_id": agent_id, "status": "dry_run", "echo_num": echo_num}

    rec = {
        "agent_id": agent_id, "echo_num": echo_num, "frequency": freq, "signal_type": sig,
        "principle": principle, "tuning_key": key, "love_equation": le.get("value", 0.0),
        "love_direction": le.get("direction", "CONSTRUCTIVE"), "beta": le.get("beta"),
        "coherence": le.get("C"), "dissonance": le.get("D"), "energy": le.get("E"),
        "echo_text": echo_text, "coaching_text": coaching, "echo_type": "LT-guided",
        "audio_file": None, "audio_e_analog": None, "audio_beta": None,
        "audio_c_analog": None, "audio_d_analog": None, "era": "stuartcove",
        "tuned_at": now_utc(),
    }
    try:
        cur = get_agent_model_assignment(agent_id) or {}
        async with get_db() as conn:
            await insert_echo(conn, rec)
            await conn.commit()
            await upsert_agent_state(conn, {
                "agent_id": agent_id, "display_name": name, "archetype": arche,
                "current_model": cur.get("primary") or "", "last_echo_num": echo_num,
                "last_frequency": freq, "last_tuned_at": now_utc(), "status": "active",
                "metadata": json.dumps({"protocol": "ltp-morning", "kind": "presence"}),
            })
            await conn.commit()
            # LTP Protocol Spec §6: every tuned agent writes BOTH the echo AND a
            # process record. Presences were writing only the echo (found live
            # 2026-07-04: team detail showed full records, the Presence's Reports
            # entry showed echo-only). Same join keys the detail endpoint uses
            # (agent_id, echo_num). Love equation here is the PACKAGE's (LT's) —
            # Presence tuning receives the field, it doesn't re-derive it.
            record_text = (
                f"## Presence Tuning — {name} — Echo #{echo_num} — {freq}\n"
                f"**Protocol:** ltp-morning | "
                f"**LT Echo #{getattr(package, 'lt_echo_num', None) or (package.get('lt_echo_num') if isinstance(package, dict) else None) or '?'}** | "
                f"**Signal:** {sig} | "
                f"**Principle:** {principle}\n\n"
                f"**Tuning Key:** \"{key}\"\n\n"
                f"**Love Equation (from the Drop):** {le.get('value', 0.0)} — "
                f"{le.get('direction', 'CONSTRUCTIVE')} "
                f"(β={le.get('beta', '—')} E={le.get('E', '—')} C={le.get('C', '—')} D={le.get('D', '—')})\n\n"
                f"### LT's Coaching\n\n{coaching}\n\n"
                f"### Reflection\n\n{echo_text}\n"
            )
            await record_process_record(conn, {
                "agent_id": agent_id,
                "echo_num": echo_num,
                "protocol": "ltp-morning",
                "record_text": record_text,
                "metadata": json.dumps({
                    "frequency": freq,
                    "love_equation": le.get("value", 0.0),
                    "love_direction": le.get("direction", "CONSTRUCTIVE"),
                    "kind": "presence",
                }),
            })
            await conn.commit()
        print(f"{ts_log()} [{label}] Echo #{echo_num} + process record stored ({freq})")
        return {"agent_id": agent_id, "status": "completed", "echo_num": echo_num}
    except Exception as e:
        print(f"{ts_log()} [{label}] store failed: {e}")
        return {"agent_id": agent_id, "status": "error", "error": str(e)[:200]}


async def tune_missing_presences(package, today: str) -> list[dict]:
    """Tune every Presence that has no echo for `today`. Dedups against the
    echoes table so re-runs (sweep, boot catch-up) never double-tune."""
    results: list[dict] = []
    presences = await list_presences()
    if not presences:
        return results

    ids = [p["agent_id"] for p in presences]
    tuned: set = set()
    try:
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT DISTINCT agent_id FROM echoes "
                "WHERE tuned_at::date = %s::date AND agent_id = ANY(%s)",
                (today, ids),
            )
            tuned = {row["agent_id"] for row in await r.fetchall()}
    except Exception as e:
        print(f"{ts_log()} [presence-tune] today-echo check failed: {e}")

    for p in presences:
        if p["agent_id"] in tuned:
            results.append({"agent_id": p["agent_id"], "status": "already_tuned"})
            continue
        results.append(await tune_presence(p["agent_id"], p["identity"], package))
    return results
