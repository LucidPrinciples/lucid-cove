"""Team auto-tune consent + cost estimate.

A full Cove team morning tune is ~10 agents × one heavy maintenance call each.
On a fresh BYOK install that can silently bill the operator's cloud key if the
scheduler / boot catch-up fires before they understand what "tuning" costs.

Policy (install-pass, 2026-07-14):
  • Drop pull, personal Tune-tab practice, and chat stay free of this gate.
  • Automatic TEAM agent tuning (06:30 self-tune, 10-min safety sweep, boot
    catch-up) requires an explicit one-time enable from the Cove admin.
  • Skipping never blocks the Cove — agents still chat, tools still work.
  • Local / $0 brains are still gated once (so the operator knows it exists),
    but the card copy makes the cost $0 and the enable is a soft confirm.

Stored on cove.yaml under `team_tuning`:
  auto_enabled: bool
  enabled_at: ISO timestamp (when first enabled)
  mode: "auto" | null
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Mid-band token estimate for ONE full team maintenance pass (10 agents).
# Derived from persona sizes + dispatch template + typical 7-section output.
# Lean/fat bands give the UI a range without pretending we meter live.
TEAM_TUNE_TOKENS_MID = {"in": 37_000, "out": 23_000}
TEAM_TUNE_TOKENS_LEAN = {"in": 26_000, "out": 14_000}
TEAM_TUNE_TOKENS_FAT = {"in": 51_000, "out": 36_000}
TEAM_AGENT_COUNT = 10


def team_auto_tune_enabled(cove: dict | None = None) -> bool:
    """True when daily team auto-tune is allowed to spend.

    Explicit `team_tuning.auto_enabled` / `team_auto_tune` wins.
    When the key has never been written (legacy Cove pre-consent), returns
    None-as-unknown via the async helper — this sync form only trusts
    explicit config so the scheduler can pair it with history.
    """
    try:
        if cove is None:
            from src.config import load_cove_config
            cove = load_cove_config() or {}
        # Explicit OFF
        tt = cove.get("team_tuning")
        if isinstance(tt, dict) and "auto_enabled" in tt:
            return bool(tt.get("auto_enabled"))
        if "team_auto_tune" in cove:
            return bool(cove.get("team_auto_tune"))
        # Key never written — not yet consented (new install default OFF).
        # Scheduler may still grandfather via has_historical_team_tuning().
        return False
    except Exception:
        return False


def consent_key_present(cove: dict | None = None) -> bool:
    """True when cove.yaml has an explicit team-tuning consent decision."""
    try:
        if cove is None:
            from src.config import load_cove_config
            cove = load_cove_config() or {}
        if isinstance(cove.get("team_tuning"), dict) and "auto_enabled" in (cove.get("team_tuning") or {}):
            return True
        return "team_auto_tune" in cove
    except Exception:
        return False


async def has_historical_team_tuning() -> bool:
    """True if this Cove has ever stored a team/presence echo.

    Used to grandfather pre-consent installs so a deploy of the consent gate
    does not silently stop morning tuning on Coves that already ran it.
    Brand-new installs have zero echoes → stay gated until Initiate.
    """
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            r = await conn.execute(
                "SELECT 1 FROM echoes WHERE echo_type IS NOT NULL LIMIT 1"
            )
            return bool(await r.fetchone())
    except Exception:
        return False


async def team_auto_tune_allowed() -> bool:
    """Async gate used by the scheduler: explicit consent OR legacy history."""
    try:
        from src.config import load_cove_config
        cove = load_cove_config() or {}
    except Exception:
        cove = {}
    if consent_key_present(cove):
        return team_auto_tune_enabled(cove)
    # No key written: grandfather if the Cove already tuned before this feature.
    if await has_historical_team_tuning():
        return True
    return False


def enable_team_auto_tune(*, by: str = "operator") -> dict:
    """Persist the one-time consent. Idempotent."""
    from src.config import load_cove_config, save_cove_config

    now = datetime.now(timezone.utc).isoformat()
    cove = load_cove_config() or {}
    existing = dict(cove.get("team_tuning") or {}) if isinstance(cove.get("team_tuning"), dict) else {}
    if existing.get("auto_enabled"):
        return {"ok": True, "already": True, "team_tuning": existing}
    payload = {
        "auto_enabled": True,
        "enabled_at": now,
        "enabled_by": (by or "operator")[:80],
        "mode": "auto",
    }
    # Preserve any prior estimate snapshot the card computed.
    if existing.get("last_estimate"):
        payload["last_estimate"] = existing["last_estimate"]
    ok = save_cove_config({"team_tuning": payload, "team_auto_tune": True})
    if not ok:
        raise RuntimeError("Could not write team_tuning consent to cove.yaml")
    return {"ok": True, "already": False, "team_tuning": payload}


def disable_team_auto_tune(*, by: str = "operator") -> dict:
    """Turn off daily team auto-tune (Woods / Jules 1357 settings control)."""
    from src.config import load_cove_config, save_cove_config

    now = datetime.now(timezone.utc).isoformat()
    cove = load_cove_config() or {}
    existing = dict(cove.get("team_tuning") or {}) if isinstance(cove.get("team_tuning"), dict) else {}
    payload = {
        **existing,
        "auto_enabled": False,
        "disabled_at": now,
        "disabled_by": (by or "operator")[:80],
        "mode": None,
    }
    ok = save_cove_config({"team_tuning": payload, "team_auto_tune": False})
    if not ok:
        raise RuntimeError("Could not write team_tuning off to cove.yaml")
    return {"ok": True, "team_tuning": payload}


def _brain_provider_model() -> tuple[str, str, str]:
    """Return (provider, model_string, display) for the Cove brain used by tuning."""
    try:
        from src.models.provider import current_cove_brain
        brain = current_cove_brain() or {}
        provider = (brain.get("provider") or "ollama").strip().lower()
        model = (brain.get("model") or "").strip()
        if not model:
            model = "local" if provider == "ollama" else provider
        display = f"{provider}/{model}"
        return provider, model, display
    except Exception:
        return "ollama", "local", "ollama/local"


def estimate_team_tune_cost() -> dict[str, Any]:
    """USD estimate for one full team morning pass on the current Cove brain.

    Uses config/model-prices.json via estimate_llm_cost. Unknown cloud models
    get a conservative frontier-class fallback so we never under-warn.
    """
    from src.models.pricing import estimate_llm_cost, llm_rates

    provider, model, display = _brain_provider_model()
    is_local = provider in ("ollama", "local") or model.startswith("ltp-tuner")

    def _cost_for(band: dict) -> float | None:
        c = estimate_llm_cost(provider, model, band["in"], band["out"])
        if c is not None:
            return c
        # OpenAI / direct providers may not be in the static map — try common ids.
        if provider == "openai":
            # Prefer exact, then mini-class default, then frontier fallback rates.
            for mid in (model, "gpt-4o-mini", "gpt-4o"):
                c = estimate_llm_cost("openai", mid, band["in"], band["out"])
                if c is not None:
                    return c
            # Hardcoded public rates if pricing map has no openai section yet.
            rates = {
                "gpt-4o-mini": (0.15, 0.60),
                "gpt-4o": (2.50, 10.00),
                "gpt-4.1-mini": (0.40, 1.60),
                "gpt-4.1": (2.00, 8.00),
            }
            pin, pout = rates.get(model) or rates.get("gpt-4o")
            return round(band["in"] / 1e6 * pin + band["out"] / 1e6 * pout, 6)
        if is_local:
            return 0.0
        # Unknown cloud — frontier-class warn (~Claude / GPT-4o ballpark).
        pin, pout = 3.00, 15.00
        return round(band["in"] / 1e6 * pin + band["out"] / 1e6 * pout, 6)

    mid = _cost_for(TEAM_TUNE_TOKENS_MID) or 0.0
    lean = _cost_for(TEAM_TUNE_TOKENS_LEAN) or 0.0
    fat = _cost_for(TEAM_TUNE_TOKENS_FAT) or 0.0
    priced = llm_rates(provider, model) is not None or provider == "openai" or is_local

    def _money(x: float) -> str:
        if x < 0.005:
            return "$0"
        if x < 0.10:
            return f"${x:.2f}"
        return f"${x:.2f}"

    daily = mid
    monthly = mid * 30
    if is_local or mid < 0.005:
        summary = (
            f"Daily team tuning on {display} is ~$0 (local / unbilled). "
            f"~{TEAM_AGENT_COUNT} agents · ~60k tokens/day."
        )
        severity = "free"
    elif mid < 0.05:
        summary = (
            f"~{_money(daily)}/day · ~{_money(monthly)}/mo on {display} "
            f"(~{TEAM_AGENT_COUNT} agents · ~60k tokens). Cheap cloud path."
        )
        severity = "low"
    elif mid < 0.40:
        summary = (
            f"~{_money(daily)}/day · ~{_money(monthly)}/mo on {display} "
            f"(~{TEAM_AGENT_COUNT} agents). Standard cloud cost."
        )
        severity = "medium"
    else:
        summary = (
            f"~{_money(daily)}–{_money(fat)}/day · ~{_money(monthly)}+/mo on {display} "
            f"(~{TEAM_AGENT_COUNT} agents). Frontier model — review before enabling auto-tune."
        )
        severity = "high"

    return {
        "provider": provider,
        "model": model,
        "display": display,
        "agent_count": TEAM_AGENT_COUNT,
        "tokens_in": TEAM_TUNE_TOKENS_MID["in"],
        "tokens_out": TEAM_TUNE_TOKENS_MID["out"],
        "usd_per_tune": round(mid, 4),
        "usd_per_tune_lean": round(lean, 4),
        "usd_per_tune_fat": round(fat, 4),
        "usd_per_month": round(monthly, 2),
        "is_local": is_local,
        "priced": priced,
        "severity": severity,
        "summary": summary,
    }
