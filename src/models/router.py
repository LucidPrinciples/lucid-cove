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
    operation_type: str = "channel",
    routing_bias: str = "balanced",
    cloud_middle_id: str | None = None,
    allow_cloud_middle: bool = True,
) -> HopPlan:
    """Build ordered hop chain from assignments + score + what's runnable."""
    # === EMERGENCY OVERRIDE (jules 2026-07-17) ===
    # The admin "force all chat to this model" override was checked only in
    # plan_for_agent(), but the LIVE chat path (channels.py) and invoke_with_fallback
    # (provider.py) call plan_hops() DIRECTLY — so the override was silently bypassed
    # and setting it did nothing (Stuart kept local-first routing to qwen/kimi despite
    # a Grok override). Check it HERE, the shared entry point every path uses, so a set
    # override truly forces the model. Same shape as plan_for_agent's check.
    try:
        from src.config import get_model_override, load_models_registry as _lmr
        _override = get_model_override()
        if _override:
            _valid_ids = {m.get("id") for m in _lmr() if m.get("id")}
            if _override in _valid_ids:
                return HopPlan(
                    first_id=_override,
                    chain=[_override],
                    score=ScoreResult(score=0, reasons=["admin_override"], role="", bias=""),
                    mode="override",
                    detail=f"admin_override:{_override}",
                )
            print(f"[router] WARNING: invalid model_override '{_override}' — ignoring")
    except Exception as _ovr_e:
        print(f"[router] override check skipped: {_ovr_e}")

    sc = score or score_turn(
        agent_id=agent_id,
        message_text=message_text,
        message_count=message_count,
        approx_tokens=approx_tokens,
        tool_names=tool_names,
        operation_type=operation_type,
        routing_bias=routing_bias,
    )

    # Failure pressure against configured primary
    fp, freasons = _fail_pressure(primary_id, _provider_of(primary_id))
    if fp:
        sc = ScoreResult(
            score=max(0, min(100, sc.score + fp)),
            reasons=list(sc.reasons) + freasons,
            role=sc.role,
            bias=sc.bias,
        )

    local_id = None
    api_ids: list[str] = []

    for mid in (primary_id, fallback_id):
        if not mid:
            continue
        if _is_local_id(mid):
            if local_id is None:
                local_id = mid
        else:
            if mid not in api_ids:
                api_ids.append(mid)

    # Prefer installed local if assignments didn't name one
    if local_id is None:
        local_id = _installed_local()

    # Optional cloud middle (different upstream) — only if runnable and distinct
    if allow_cloud_middle and cloud_middle_id:
        try:
            from src.models.provider import model_is_runnable
            if model_is_runnable(cloud_middle_id):
                cp = _provider_of(cloud_middle_id)
                if cloud_middle_id not in api_ids and all(_provider_of(a) != cp for a in api_ids):
                    api_ids.append(cloud_middle_id)
                elif cloud_middle_id not in api_ids:
                    # same-upstream twin risk — still allow as last api if nothing else
                    if not api_ids:
                        api_ids.append(cloud_middle_id)
        except Exception:
            pass

    # Filter to runnable
    api_ids = [a for a in api_ids if _runnable(a)]
    if local_id and not _runnable(local_id):
        # local tags are usually runnable without keys; if marked unrunnable keep try
        pass

    has_local = bool(local_id)
    has_api = bool(api_ids)
    if has_local and has_api:
        mode = "local+api"
    elif has_api:
        mode = "api-only"
    elif has_local:
        mode = "local-only"
    else:
        mode = "none"

    chain: list[str] = []

    def _add(mid: str | None):
        if mid and mid not in chain:
            chain.append(mid)

    if mode == "none":
        # Last ditch: whatever primary was, even if we couldn't classify
        _add(primary_id)
        _add(fallback_id)
        return HopPlan(
            first_id=chain[0] if chain else None,
            chain=chain,
            score=sc,
            mode=mode,
            detail="no_classified_hops",
        )

    if mode == "local-only":
        _add(local_id)
        _add(fallback_id)
        return HopPlan(first_id=chain[0], chain=chain, score=sc, mode=mode, detail="local_only")

    if mode == "api-only":
        # score still orders which api first if multiple
        if sc.score >= THRESHOLD_API and len(api_ids) > 1:
            # prefer non-recently-failed
            ordered = sorted(api_ids, key=lambda m: _fail_pressure(m, _provider_of(m))[0])
            for m in ordered:
                _add(m)
        else:
            for m in api_ids:
                _add(m)
        return HopPlan(first_id=chain[0], chain=chain, score=sc, mode=mode, detail="api_only")

    # local+api
    prefer_local_first = sc.score < THRESHOLD_API
    # high score → API first; mid → local if healthy else API; low → local
    primary_is_local = _is_local_id(primary_id)

    if sc.score >= THRESHOLD_API:
        for m in api_ids:
            _add(m)
        _add(local_id)
        detail = "api_first_hard"
    elif sc.score >= THRESHOLD_LOCAL:
        # mid: local if primary prefers local or local exists and not hot-failed
        loc_fail, _ = _fail_pressure(local_id, "ollama")
        if local_id and loc_fail < 25:
            _add(local_id)
            for m in api_ids:
                _add(m)
            detail = "local_first_mid"
        else:
            for m in api_ids:
                _add(m)
            _add(local_id)
            detail = "api_first_mid_local_unhealthy"
    else:
        _add(local_id)
        for m in api_ids:
            _add(m)
        detail = "local_first_easy"

    # If assignment primary is API and score is mid/low, we still may have put local
    # first — that's intentional for cost. If primary is API and score high, api first.

    if not chain:
        _add(primary_id)
        _add(fallback_id)
        _add(local_id)

    return HopPlan(
        first_id=chain[0] if chain else None,
        chain=chain,
        score=sc,
        mode=mode,
        detail=detail,
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
    """Resolve assignment + plan hops for an agent (protocol and channel shared entry)."""
    
    # === EMERGENCY OVERRIDE ===
    # Admin can force all chat to a specific model via cove.yaml model_override
    from src.config import get_model_override, load_models_registry
    override = get_model_override()
    if override:
        # Validate the override is a real model
        valid_ids = {m.get("id") for m in load_models_registry() if m.get("id")}
        if override in valid_ids:
            return HopPlan(
                first_id=override,
                chain=[override],
                score=ScoreResult(score=0, reasons=["admin_override"], role="", bias=""),
                mode="override",
                detail=f"admin_override:{override}",
            )
        # Invalid override logged but falls through to normal routing
        print(f"[router] WARNING: Invalid model_override '{override}' — ignoring")
    
    from src.config import get_agent_model_assignment
    try:
        from src.models.provider import current_cove_brain, CLOUD_FALLBACK_MODEL
    except Exception:
        CLOUD_FALLBACK_MODEL = None  # type: ignore
        def current_cove_brain():  # type: ignore
            return {}

    slot = "tuning" if (operation_type or "").lower() == "tuning" else None
    assignment = get_agent_model_assignment(agent_id, slot=slot) or {}
    primary = personal_primary or assignment.get("primary") or (current_cove_brain() or {}).get("model")
    fallback = personal_fallback or assignment.get("fallback")

    return plan_hops(
        agent_id=agent_id,
        primary_id=primary,
        fallback_id=fallback,
        message_text=message_text,
        message_count=message_count,
        approx_tokens=approx_tokens,
        tool_names=tool_names,
        operation_type=operation_type,
        routing_bias=routing_bias,
        cloud_middle_id=CLOUD_FALLBACK_MODEL,
        allow_cloud_middle=True,
    )


def format_plan_log(plan: HopPlan, label: str = "router") -> str:
    sc = plan.score
    return (
        f"[{label}] #D55 router score={sc.score} mode={plan.mode} detail={plan.detail} "
        f"first={plan.first_id} chain={plan.chain} reasons={','.join(sc.reasons)}"
    )
