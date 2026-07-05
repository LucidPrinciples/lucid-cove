"""
market_rates.py — the marketplace estimator (display-only trust surface, C5/M20).

Posted flat rates ("what your $12 buys") shown on listings and as a per-use estimate on
the active Tools card. This is a TRUST surface, not a meter: NO balance gate, no wallet —
you paid a fixed price for exactly what you booked. The numbers live in
config/market-rates.json so they're editable without a code change (the launch spec's
"posted rates are editable" requirement). Distinct from cost.py / model-prices.json, which
is the internal per-run cost estimator; this is the customer-facing posted price list.

Single module (config JSON + one route), per the batch's C5 shape.
"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)

# Resolve config/market-rates.json across the per-install instance config dir then the
# repo mount — the same instance->cove-core fallback pricing.py / config.py use, so a
# deployed Cove (whose /app/config is empty of repo static assets) still finds it.
_INSTANCE_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"   # = /app/config in container
_CORE_CONFIG_DIR = Path("/cove-core/config")                            # repo-bundled via /cove-core:ro

_FALLBACK = {
    "_meta": {"currency": "USD", "note": "built-in fallback (config/market-rates.json not found)"},
    "asr": {"label": "Transcription (ASR)", "unit": "per video-minute", "rate": 0.02},
    "llm": {"label": "Model inference", "unit": "per 1K tokens",
            "classes": {"standard": {"label": "Standard", "rate": 0.004}}},
    "gpu": {"label": "GPU compute", "unit": "per GPU-minute", "rate": 0.02},
    "what_12_buys": [],
}


def _load_rates() -> dict:
    for base in (_INSTANCE_CONFIG_DIR, _CORE_CONFIG_DIR):
        p = base / "market-rates.json"
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("market-rates.json load failed at %s: %s", p, e)
    return _FALLBACK


def what_amount_buys(usd: float) -> list:
    """A small 'what $X buys' breakdown across the posted rates. Display-only."""
    rates = _load_rates()
    out = []
    asr = rates.get("asr") or {}
    if asr.get("rate"):
        out.append({"capability": asr.get("label", "Transcription"),
                    "amount": f"~{int(usd / float(asr['rate']))} {asr.get('unit', 'units')}"})
    gpu = rates.get("gpu") or {}
    if gpu.get("rate"):
        out.append({"capability": gpu.get("label", "GPU compute"),
                    "amount": f"~{int(usd / float(gpu['rate']))} {gpu.get('unit', 'units')}"})
    std = ((rates.get("llm") or {}).get("classes") or {}).get("standard") or {}
    if std.get("rate"):
        ktoks = int(usd / float(std["rate"]))
        out.append({"capability": "Standard model inference",
                    "amount": f"~{ktoks}K tokens" if ktoks < 1000 else f"~{ktoks // 1000}M tokens"})
    return out


@router.get("/api/market/rates")
async def market_rates(usd: float = 12.0):
    """The posted rate table + a 'what your $X buys' breakdown (default $12, the floor).
    Display only — no identity, no balance, no gate."""
    rates = _load_rates()
    posted = rates.get("what_12_buys") if abs(usd - 12.0) < 0.01 else None
    return {
        "ok": True,
        "currency": (rates.get("_meta") or {}).get("currency", "USD"),
        "rates": {k: v for k, v in rates.items() if not k.startswith("_") and k != "what_12_buys"},
        "what_it_buys": posted or what_amount_buys(usd),
        "usd": usd,
        "note": (rates.get("_meta") or {}).get("note", ""),
    }
