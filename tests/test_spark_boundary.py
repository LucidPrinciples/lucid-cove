"""THE SPARK BOUNDARY + PIN (2026-07-19 incident).

The LP Cove Onboarding key carries the install wizard (naming + wake), until
spark_wake_done is stamped (finalize alone is not enough — wake runs after
identity write). On LP's key the model is pinned to Kimi K2.5 via OpenRouter —
never the Cove brain, never a client model_id, never openrouter/auto.

Regression: the guided-key fallback was "any keyless operator, forever" (post-setup
Action Board tools billed to the onboarding key), and the spark inherited a
non-OpenRouter Cove brain → get_model_client provider-mismatch → openrouter/auto →
Opus 4.6 / GPT-5.6 on LP's dime.
"""
from pathlib import Path


def _src(rel: str) -> str:
    return Path(rel).read_text()


# ── The pin ──────────────────────────────────────────────────────────────────

def test_spark_model_is_pinned_kimi():
    from src.models import spark
    assert spark.SPARK_MODEL_ID == "kimi-k2.5-openrouter"
    assert spark.SPARK_MODEL_STRING == "moonshotai/kimi-k2.5"


def test_spark_fast_pin_for_naming_flows():
    from src.models.spark import resolve_spark_pin, SPARK_FAST_FLOW_IDS, SPARK_MODEL_ID
    mid, mstr, to = resolve_spark_pin("flow-agent-names")
    assert "flow-agent-names" in SPARK_FAST_FLOW_IDS
    assert mid != SPARK_MODEL_ID  # names must not use the heavy quality pin
    assert to <= 30.0
    qid, _, qto = resolve_spark_pin("flow-wake")
    assert qid == SPARK_MODEL_ID
    assert qto >= 60.0


def test_byok_default_is_never_openrouter_auto():
    # The router lottery has landed on Opus-class models; a named model or nothing.
    from src.models import provider
    assert "auto" not in provider.BYOK_DEFAULT_MODEL["openrouter"]
    assert provider.BYOK_DEFAULT_MODEL["openrouter"] == "moonshotai/kimi-k2.5"


def test_hub_spark_ignores_client_model_and_hub_brain():
    src = _src("src/dashboard/routes/registry.py")
    spark_fn = src.split("async def spark(")[1].split("@router.post")[0]
    # Pinned by flow_id via resolve_spark_pin — never body model_id / hub brain:
    assert "resolve_spark_pin" in spark_fn
    assert 'body.get("model_id")' not in spark_fn
    assert "current_cove_brain" not in spark_fn
    # The pin also rides the BYOK context so no resolution path can reroute:
    assert "set_request_byok(\"openrouter\", lp_key, model=model_pin_string)" in spark_fn


def test_hub_spark_uses_only_the_dedicated_onboarding_key():
    src = _src("src/dashboard/routes/registry.py")
    spark_fn = src.split("async def spark(")[1].split("@router.post")[0]
    assert "LP_GUIDED_OPENROUTER_KEY" in spark_fn
    # Never falls back to the hub's own brain key:
    assert 'env("OPENROUTER_API_KEY")' not in spark_fn


# ── The boundary ─────────────────────────────────────────────────────────────

def test_flow_chat_gates_lp_key_on_creation_state():
    src = _src("src/dashboard/routes/flow_chat.py")
    # The guided-key fallback must sit behind spark_allowed (creation-only), be
    # capped, and pin the model (client model_id ignored on LP's key).
    assert "spark_allowed" in src
    assert "spark_caps_ok" in src
    gate = src.split("LP_GUIDED_OPENROUTER_KEY")[1].split("set_request_byok")[0]
    assert "spark_allowed(request)" in gate
    assert "model_id = SPARK_MODEL_ID" in gate


def test_guided_complete_gates_and_pins_lp_tiers():
    src = _src("src/models/spark.py")
    body = src.split("async def guided_complete(")[1]
    assert "spark_allowed(request)" in body
    assert "spark_caps_ok(system_prompt, messages)" in body
    # Tier 3 (hub) sends the flow pin, not a free client model:
    assert "model_id=pin_id" in body or "resolve_spark_pin" in body


def test_spark_allowed_fails_closed():
    import asyncio
    from src.models.spark import spark_allowed

    class _NoSessionRequest:  # get_current_presence returns None w/o cookie machinery
        cookies = {}
        headers = {}

    # No session (or any error underneath) must mean NO spark — never a fallback open.
    assert asyncio.get_event_loop().run_until_complete(
        spark_allowed(_NoSessionRequest())) is False


def test_spark_allowed_holds_open_until_wake_done():
    """Finalize writes agent_identity before the wake UI; spark must still run for
    wake + wake-reflect, then close when spark_wake_done is stamped."""
    src = _src("src/models/spark.py")
    assert "spark_wake_done" in src
    # Boundary still fails closed without a session (tested above); the wake stamp
    # is the close after identity exists.
    assert "not bool(ac.get(\"spark_wake_done\"))" in src or "spark_wake_done" in src


# ── The caps ─────────────────────────────────────────────────────────────────

def test_spark_caps():
    from src.models.spark import spark_caps_ok, SPARK_MAX_MESSAGES
    ok_msgs = [{"role": "user", "content": "hi"}]
    assert spark_caps_ok("prompt", ok_msgs)
    assert not spark_caps_ok("x" * 9000, ok_msgs)                      # system too big
    assert not spark_caps_ok("p", ok_msgs * (SPARK_MAX_MESSAGES + 1))  # too many turns
    assert not spark_caps_ok("p", [{"role": "user", "content": "x" * 13000}])  # bulk


def test_hub_spark_has_daily_budget():
    src = _src("src/dashboard/routes/registry.py")
    assert "SPARK_DAILY_BUDGET" in src
    spark_fn = src.split("async def spark(")[1].split("@router.post")[0]
    assert "_spark_budget_ok" in spark_fn


# ── The callers ──────────────────────────────────────────────────────────────

def test_static_callers_use_real_registry_id():
    # 'kimi-k2.5' is NOT a registry id (it survives only via typo-recovery); the
    # real id is kimi-k2.5-openrouter. Silent-guess ids on billed paths are bugs.
    for rel in (
        "src/dashboard/static/js/action-board.js",
        "src/dashboard/static/js/flow-framework.js",
        "src/dashboard/static/action-board/site-builder.html",
        "src/dashboard/static/action-board/create-a-mirror.html",
    ):
        src = _src(rel)
        assert "'kimi-k2.5-openrouter'" in src, rel
        assert "model_id: 'kimi-k2.5'," not in src, rel
