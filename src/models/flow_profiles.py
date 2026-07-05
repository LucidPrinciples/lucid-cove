"""
Flow profiles (#183 build #1, part c).

Expected resource use per flow/step, BY KIND, as a self-updating rolling
average. A "flow" is a named multi-step operation (e.g. 'ltp-morning',
'video-pipeline'); each step consumes some number of units of one kind:

  - llm_tokens   -> total tokens for an LLM call/step
  - asr_minutes  -> minutes of audio transcribed
  - gpu_minutes  -> minutes of GPU/compute time

The pre-flight estimator multiplies these expected units by the price map
(pricing.py) to show a cost before a run. Profiles start from a seed
(jw_metrics history + video durations) and tighten as real runs accrue.

operation_label convention: "flow/step" (e.g. "stuart/ltp-morning"). A label
with no slash is treated as flow with step '*'.
"""

# Running-mean upsert: new_avg = (avg*count + units) / (count+1)
_UPSERT_SQL = """
    INSERT INTO flow_profiles (flow, step, unit_kind, avg_units, sample_count, last_units)
    VALUES (%s, %s, %s, %s, 1, %s)
    ON CONFLICT (flow, step, unit_kind) DO UPDATE SET
        avg_units = (flow_profiles.avg_units * flow_profiles.sample_count
                     + EXCLUDED.last_units) / (flow_profiles.sample_count + 1),
        sample_count = flow_profiles.sample_count + 1,
        last_units = EXCLUDED.last_units,
        updated_at = NOW()
"""

_VALID_KINDS = {"llm_tokens", "asr_minutes", "gpu_minutes"}


def parse_label(operation_label: str) -> tuple[str, str]:
    """Split an operation_label into (flow, step). No slash -> step '*'."""
    label = (operation_label or "").strip() or "unknown"
    if "/" in label:
        flow, step = label.split("/", 1)
        return flow.strip() or "unknown", step.strip() or "*"
    return label, "*"


async def record_observation(flow: str, step: str, unit_kind: str,
                             units: float) -> None:
    """Fold one observation into the rolling average. Never raises."""
    if unit_kind not in _VALID_KINDS or units is None or units <= 0:
        return
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            await conn.execute(
                _UPSERT_SQL,
                (flow, step or "*", unit_kind, float(units), float(units)),
            )
    except Exception as e:
        print(f"[flow_profiles] record failed (non-fatal): {e}")


async def record_llm(operation_label: str, tokens_total: int | None) -> None:
    """Convenience: record an LLM token observation from an operation label."""
    if not tokens_total:
        return
    flow, step = parse_label(operation_label)
    await record_observation(flow, step, "llm_tokens", tokens_total)


async def get_profile(flow: str) -> list[dict]:
    """All step/kind rows for one flow."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT flow, step, unit_kind, avg_units, sample_count,
                          last_units, updated_at
                   FROM flow_profiles WHERE flow = %s
                   ORDER BY step, unit_kind""",
                (flow,),
            )
            return await result.fetchall()
    except Exception as e:
        print(f"[flow_profiles] read failed (non-fatal): {e}")
        return []


async def get_all_profiles() -> list[dict]:
    """Every profile row — for the spend/usage reports rollup."""
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                """SELECT flow, step, unit_kind, avg_units, sample_count,
                          last_units, updated_at
                   FROM flow_profiles ORDER BY flow, step, unit_kind"""
            )
            return await result.fetchall()
    except Exception as e:
        print(f"[flow_profiles] read failed (non-fatal): {e}")
        return []
