"""
Cost estimator + spend reports (#183 parts d/e).

Turns the price map (pricing.py) + flow profiles (flow_profiles) into:
  - GET /api/cost/estimate — a PRE-FLIGHT estimate + per-run CHOOSER: for a
    given flow, what it costs across the available models and compute backends
    ("≈ $0.40 Kimi / $2.10 Claude / $0 local").
  - GET /api/cost/report — spend/usage ROLLUPS from the cost_usd now logged on
    every call, grouped by flow / agent / day.

The estimate is a close decision-making number, not a bill. It reads expected
units from flow_profiles (self-updating) and prices them with the semi-live map.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.config import load_models_registry
from src.models import pricing
from src.models.flow_profiles import get_profile

router = APIRouter()

# Default input/output token split when a profile only tracks total tokens.
_DEFAULT_INPUT_SHARE = 0.75


def _resolve(model: dict) -> tuple[str, str]:
    """(provider, model_string) from a registry entry."""
    return model.get("provider", "ollama"), model.get("model_string", model.get("id", ""))


@router.get("/api/cost/estimate")
async def cost_estimate(flow: str, input_share: float = _DEFAULT_INPUT_SHARE):
    """Pre-flight cost of one run of `flow`, across models + compute backends.

    Reads expected units (llm_tokens / asr_minutes / gpu_minutes) from the flow
    profile and prices each option. Returns a chooser the UI can render before
    the user commits to a run.
    """
    try:
        rows = await get_profile(flow)
        units = {"llm_tokens": 0.0, "asr_minutes": 0.0, "gpu_minutes": 0.0}
        samples = 0
        for r in rows:
            kind = r.get("unit_kind")
            if kind in units:
                units[kind] += float(r.get("avg_units") or 0.0)
                samples = max(samples, int(r.get("sample_count") or 0))
        has_data = any(v > 0 for v in units.values())

        pm = pricing.get_price_map()

        # LLM options. ASSUME THE CLOUD COST as the baseline (cost_cloud_usd):
        # for a cloud model that's its own price; for a model you *could* run
        # locally, it's the open-model cloud-equivalent (ollama-cloud rate). The
        # $0 local figure is shown ONLY when you have the hardware (type=local).
        llm_options = []
        tok = units["llm_tokens"]
        if tok > 0:
            t_in = tok * max(0.0, min(1.0, input_share))
            t_out = tok - t_in

            def _cost(rates):
                if not rates:
                    return None
                return round((t_in / 1e6) * rates[0] + (t_out / 1e6) * rates[1], 4)

            cloud_equiv = pricing.llm_rates("ollama-cloud", "*")  # open-model cloud rate
            for m in load_models_registry():
                prov, mstr = _resolve(m)
                is_local = m.get("type") == "local"
                cloud_rates = cloud_equiv if is_local else pricing.llm_rates(prov, mstr)
                cost_cloud = _cost(cloud_rates)
                cost_local = 0.0 if is_local else None
                if cost_cloud is None and cost_local is None:
                    continue
                llm_options.append({
                    "model_id": m.get("id"), "label": m.get("name") or m.get("id"),
                    "provider": prov, "type": m.get("type"),
                    "cost_cloud_usd": cost_cloud,        # the assumed baseline
                    "cost_local_usd": cost_local,        # $0 if you have the GPU
                    "runs_local": is_local,
                })
            llm_options.sort(key=lambda o: (o["cost_cloud_usd"] is None, o["cost_cloud_usd"] or 0))

        # ASR + compute: same logic — cloud services are the assumed cost,
        # 'local' (own GPU) is the $0 option.
        asr_options = []
        if units["asr_minutes"] > 0:
            for svc in pm.get("asr", {}):
                cost = round(pricing.estimate_asr_cost(units["asr_minutes"], svc), 4)
                asr_options.append({"service": svc, "cost_usd": cost,
                                    "runs_local": svc == "local"})
            asr_options.sort(key=lambda o: o["cost_usd"])
        compute_options = []
        if units["gpu_minutes"] > 0:
            for backend in pm.get("compute", {}):
                cost = round(pricing.estimate_compute_cost(units["gpu_minutes"], backend), 4)
                compute_options.append({"backend": backend, "cost_usd": cost,
                                        "runs_local": backend == "local-gpu"})
            compute_options.sort(key=lambda o: o["cost_usd"])

        # Headline reads as the CLOUD baseline (real numbers), noting that local
        # hardware drops it to $0.
        headline = None
        if llm_options:
            priced = [o for o in llm_options if o["cost_cloud_usd"] is not None]
            if priced:
                cheap, rich = priced[0], priced[-1]
                local_available = any(o["runs_local"] for o in llm_options)
                headline = {
                    "cloud_from": cheap, "cloud_to": rich,
                    "cloud_span": f"${cheap['cost_cloud_usd']:.2f}–${rich['cost_cloud_usd']:.2f}",
                    "local_available": local_available,
                    "summary": f"${cheap['cost_cloud_usd']:.2f}–${rich['cost_cloud_usd']:.2f} cloud"
                               + (" · $0 on local hardware" if local_available else ""),
                }

        return {
            "flow": flow,
            "has_data": has_data,
            "samples": samples,
            "expected_units": {k: round(v, 1) for k, v in units.items()},
            "llm_options": llm_options,
            "asr_options": asr_options,
            "compute_options": compute_options,
            "headline": headline,
            "note": None if has_data else
                    "No profile yet for this flow — estimate appears after a first run.",
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Starter-Cove monthly estimate (CF-72 polish) ─────────────────────────────
# A fresh Cove has NO flow profiles yet, so the pre-flight estimator can't answer
# the operator's first cost question: "what does running this thing cost me a
# month?" This composes a typical STARTER month from explicit assumptions and
# prices it with the same registry + price map. A decision-making range, not a
# bill — the assumptions ride along in the response so nothing is hidden.

STARTER_ASSUMPTIONS = {
    "daily_tunings": 2,          # the operator's tuning + their agent's echo
    "tokens_per_tuning": 6000,
    "daily_chats": 20,           # ordinary chat turns with the agent, per day
    "tokens_per_chat": 1500,
    "days": 30,
}


def starter_month_estimate() -> dict:
    """Monthly cloud-cost range for a starter Cove (solo operator + one agent),
    plus the $0-local note when a local model is in the registry. Never raises."""
    try:
        a = STARTER_ASSUMPTIONS
        tokens = a["days"] * (a["daily_tunings"] * a["tokens_per_tuning"]
                              + a["daily_chats"] * a["tokens_per_chat"])
        t_in = tokens * _DEFAULT_INPUT_SHARE
        t_out = tokens - t_in

        def _cost(rates):
            if not rates:
                return None
            return round((t_in / 1e6) * rates[0] + (t_out / 1e6) * rates[1], 2)

        cloud_equiv = pricing.llm_rates("ollama-cloud", "*")
        costs, local_available = [], False
        for m in load_models_registry():
            prov, mstr = _resolve(m)
            if m.get("type") == "local":
                local_available = True
                c = _cost(cloud_equiv)
            else:
                c = _cost(pricing.llm_rates(prov, mstr))
            if c is not None:
                costs.append(c)
        if not costs:
            return {"ok": False}
        lo, hi = min(costs), max(costs)
        span = (f"~${lo:.2f}/mo" if lo == hi else f"~${lo:.2f}–${hi:.2f}/mo")
        return {
            "ok": True, "monthly_tokens": tokens,
            "cloud_low_usd": lo, "cloud_high_usd": hi,
            "local_available": local_available,
            "assumptions": dict(a),
            "summary": (f"A starter Cove (daily tunings + everyday chat) runs {span} "
                        f"on cloud keys" + (" · about $0 on a local model" if local_available else "")),
        }
    except Exception:
        return {"ok": False}


@router.get("/api/cost/starter")
async def cost_starter():
    """The starter-Cove monthly estimate — what the compute chooser surfaces."""
    return starter_month_estimate()


@router.get("/api/cost/report")
async def cost_report(days: int = 7, group_by: str = "flow"):
    """Spend/usage rollup from jw_metrics.cost_usd over the last `days`.

    group_by: 'flow' (operation_label), 'agent' (agent_id), or 'day'.
    """
    col = {
        "flow": "operation_label",
        "agent": "agent_id",
        "day": "recorded_at::date",
    }.get(group_by, "operation_label")
    days = max(1, min(365, int(days)))
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(
                f"""SELECT {col} AS bucket,
                           COUNT(*) AS calls,
                           COALESCE(SUM(cost_usd), 0) AS cost_usd,
                           COALESCE(SUM(tokens_total), 0) AS tokens
                    FROM jw_metrics
                    WHERE recorded_at >= CURRENT_DATE - make_interval(days => %s)
                    GROUP BY bucket
                    ORDER BY cost_usd DESC
                    LIMIT 100""",
                (days,),
            )
            rows = await result.fetchall()
            out = []
            total = 0.0
            for r in rows:
                d = dict(r)
                for k, v in d.items():
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                    elif v is not None and hasattr(v, "__float__"):
                        d[k] = float(v)
                total += d.get("cost_usd") or 0.0
                out.append(d)
        return {"days": days, "group_by": group_by,
                "total_cost_usd": round(total, 4), "rows": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
