"""
#D55 — Local/API model router (escalate-on-hard).

Pre-route scorer + hop planner. Failure rescue (#D16) still owns timeout/empty/error;
this module only chooses the *first* hop and an ordered chain.

Design source: Working/Specs/reliability-three-pillars-and-d55.md
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

# ── Thresholds (tunable; logged every turn) ─────────────────────────────────
THRESHOLD_LOCAL = 40
THRESHOLD_API = 70

# Role floors added into escalate pressure before turn-shape signals.
ROLE_FLOOR: dict[str, int] = {
    "stuart": 25,
    "mercer": 25,
    "archimedes": 55,
    "arthur": 45,
    "vera": 45,
    "gabe": 35,
    "ezra": 30,
    "julian": 30,
    "iris": 35,
    "soren": 35,
}

# Personal routing bias (optional; presence settings). Shifts score only.
BIAS_SHIFT = {
    "prefer-local": -20,
    "local": -20,
    "balanced": 0,
    "prefer-cloud": 25,
    "cloud": 25,
    "api-first": 25,
}

_MUTATION_RE = re.compile(
    r"\b(git_|create_github|docker_|run_shell|deploy|site_deploy|db_execute|"
    r"run_shell_destructive|approve|merge|provision|security|auth|oauth)\b",
    re.I,
)
_HARD_LANG_RE = re.compile(
    r"\b(design|architect|debug|diagnos|compare|trade.?off|refactor|"
    r"security|implement|multi-?file|investigate|root.?cause|build (a|the|this))\b",
    re.I,
)
_EASY_LANG_RE = re.compile(
    r"\b(calendar|grocery|list|schedule|what's next|whats next|board|ticket|"
    r"status|remind|appointment|errand|tomorrow|today)\b",
    re.I,
)

# Recent failure memory: model_id/provider → monotonic timestamps of fails
_fail_lock = threading.Lock()
_recent_fails: dict[str, list[float]] = {}
_FAIL_WINDOW_S = 900.0  # 15 minutes
_FAIL_COUNT_HOT = 2


@dataclass
class ScoreResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    role: str = ""
    bias: str = "balanced"


@dataclass
class HopPlan:
    first_id: str | None
    chain: list[str]  # ordered unique model ids to try (first included)
    score: ScoreResult
    mode: str  # local+api | api-only | local-only | none
    detail: str = ""


def record_hop_failure(model_id: str | None, provider: str | None = None) -> None:
    """Record a failed hop so the next score biases away from it."""
    keys = [k for k in (model_id, provider) if k]
    if not keys:
        return
    now = time.monotonic()
    with _fail_lock:
        for k in keys:
            bucket = _recent_fails.setdefault(str(k), [])
            bucket.append(now)
            # prune
            _recent_fails[str(k)] = [t for t in bucket if now - t <= _FAIL_WINDOW_S]


def clear_failure_memory() -> None:
    """Test helper."""
    with _fail_lock:
        _recent_fails.clear()


def _fail_pressure(model_id: str | None, provider: str | None = None) -> tuple[int, list[str]]:
    now = time.monotonic()
    reasons: list[str] = []
    pressure = 0
    with _fail_lock:
        for k in (model_id, provider):
            if not k:
                continue
            hits = [t for t in _recent_fails.get(str(k), []) if now - t <= _FAIL_WINDOW_S]
            if len(hits) >= _FAIL_COUNT_HOT:
                pressure += 25
                reasons.append(f"recent_fail:{k}x{len(hits)}")
            elif hits:
                pressure += 10
                reasons.append(f"recent_fail:{k}x{len(hits)}")
    return min(pressure, 40), reasons


def _role_key(agent_id: str | None) -> str:
    if not agent_id:
        return ""
    # agent ids may be "stuart" or "stuart-clearfield" style
    base = str(agent_id).strip().lower().split("-")[0]
    return base


def score_turn(
    *,
    agent_id: str = "stuart",
    message_text: str = "",
    message_count: int = 0,
    approx_tokens: int = 0,
    tool_names: Iterable[str] | None = None,
    operation_type: str = "channel",
    routing_bias: str = "balanced",
    expected_tools: bool = False,
) -> ScoreResult:
    """Return escalate pressure 0–100 and human-readable reason codes."""
    reasons: list[str] = []
    score = 0
    role = _role_key(agent_id)
    floor = ROLE_FLOOR.get(role, 30)
    score += floor
    reasons.append(f"role_floor:{role or 'default'}={floor}")

    bias_key = (routing_bias or "balanced").strip().lower()
    shift = BIAS_SHIFT.get(bias_key, 0)
    if shift:
        score += shift
        reasons.append(f"bias:{bias_key}={shift:+d}")

    tools = list(tool_names or [])
    if tools:
        joined = " ".join(tools)
        if _MUTATION_RE.search(joined):
            score += 35
            reasons.append("tools:mutation")
        elif len(tools) >= 6:
            score += 20
            reasons.append("tools:many")
        else:
            score += 8
            reasons.append("tools:present")
    elif expected_tools:
        score += 10
        reasons.append("tools:channel_bound")

    text = message_text or ""
    if _HARD_LANG_RE.search(text):
        score += 25
        reasons.append("lang:hard")
    if _EASY_LANG_RE.search(text) and not _HARD_LANG_RE.search(text):
        score -= 15
        reasons.append("lang:easy")

    if approx_tokens >= 12000 or message_count >= 40:
        score += 20
        reasons.append("context:heavy")
    elif approx_tokens >= 6000 or message_count >= 20:
        score += 10
        reasons.append("context:medium")

    ot = (operation_type or "").lower()
    if ot in ("tuning",):
        # tuning stays on tuning slot; mild escalate only
        score += 5
        reasons.append("op:tuning")
    elif ot in ("protocol", "task", "delegation", "build"):
        score += 15
        reasons.append(f"op:{ot}")

    # Clamp
    score = max(0, min(100, score))
    return ScoreResult(score=score, reasons=reasons, role=role, bias=bias_key)


def _provider_of(model_id: str | None) -> str:
    if not model_id:
        return ""
    try:
        from src.models.provider import _resolve_model_string
        p, _ = _resolve_model_string(model_id)
        return (p or "").lower()
    except Exception:
        return ""


def _is_local_id(model_id: str | None) -> bool:
    if not model_id:
        return False
    p = _provider_of(model_id)
    if p == "ollama":
        return True
    s = str(model_id).lower()
    return s.startswith("ollama") or s.startswith("local")


def _runnable(model_id: str | None) -> bool:
    if not model_id:
        return False
    try:
        from src.models.provider import model_is_runnable
        return bool(model_is_runnable(model_id))
    except Exception:
        # If registry lookup fails, be conservative: allow try (existing paths did).
        return True


def _installed_local() -> str | None:
    try:
        from src.models.local_fallback import resolve_local_fallback_model, LocalModelUnavailable
        try:
            return resolve_local_fallback_model()
        except LocalModelUnavailable:
            return None
    except Exception:
        return None


def plan_hops(
    *,
    agent_id: str = "stuart",
    primary_id: str | None = None,
    fallback_id: str | None = None,
    score: ScoreResult | None = None,
    message_text: str = "",
    message_count: int = 0,
    approx_tokens: int = 0,
    tool_names: Iterable[str] | None = None,
    operation_type: str = "background",
    routing_bias: str = "balanced",
    cloud_middle_id: str | None = None,
    allow_cloud_middle: bool = True,
) -> HopPlan:
    """Cove Model / Fallback / floor. Grid, score, and cloud-middle do not pick.

    Unused kwargs stay so callers do not break. Chat (operation_type channel/chat)
    is Model only. Everything else is Model, Fallback, named floor.
    """
    from src.models.cove_hops import load_cove_settings, plan_cove_chain, hop_kind

    kind = hop_kind(operation_type)
    settings = load_cove_settings()
    chain = plan_cove_chain(kind=kind, settings=settings)
    return HopPlan(
        first_id=chain[0] if chain else None,
        chain=chain,
        score=ScoreResult(score=0, reasons=["cove_settings"], role="", bias=""),
        mode=kind,
        detail=settings.detail,
    )


def plan_for_agent(
    agent_id: str,
    *,
    message_text: str = "",
    message_count: int = 0,
    approx_tokens: int = 0,
    operation_type: str = "channel",
    routing_bias: str = "balanced",
    personal_primary: str | None = None,
    personal_fallback: str | None = None,
    tool_names: Iterable[str] | None = None,
) -> HopPlan:
    """Shared entry. Presence stored picks are ignored until the operator names them."""
    return plan_hops(
        agent_id=agent_id,
        message_text=message_text,
        message_count=message_count,
        approx_tokens=approx_tokens,
        tool_names=tool_names,
        operation_type=operation_type,
        routing_bias=routing_bias,
    )


def format_plan_log(plan: HopPlan, label: str = "router") -> str:
    sc = plan.score
    return (
        f"[{label}] #D55 router score={sc.score} mode={plan.mode} detail={plan.detail} "
        f"first={plan.first_id} chain={plan.chain} reasons={','.join(sc.reasons)}"
    )
