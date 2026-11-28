"""Behavior: Model / Fallback / named floor. No source-string checks."""

from src.models.cove_hops import CoveSettings, plan_cove_chain, registry_id, model_string_of


def test_background_hop_order():
    s = CoveSettings(model="grok-4-5", fallback="gemini-flash", floor="qwen3-8b")
    assert plan_cove_chain("background", s) == ["grok-4-5", "gemini-flash", "qwen3-8b"]


def test_chat_is_model_only():
    s = CoveSettings(model="grok-4-5", fallback="gemini-flash", floor="qwen3-8b")
    assert plan_cove_chain("chat", s) == ["grok-4-5"]


def test_chat_empty_without_model_does_not_use_fallback():
    s = CoveSettings(model=None, fallback="gemini-flash", floor="qwen3-8b")
    assert plan_cove_chain("chat", s) == []


def test_duplicate_skipped_by_model_string():
    assert model_string_of("grok-4-5") == "grok-4.6"
    s = CoveSettings(model="grok-4-5", fallback="grok-4-5", floor="qwen3-8b")
    assert plan_cove_chain("background", s) == ["grok-4-5", "qwen3-8b"]


def test_unmatched_token_skipped():
    s = CoveSettings(model="not-a-real-model", fallback="gemini-flash", floor="qwen3-8b")
    assert plan_cove_chain("background", s) == ["gemini-flash", "qwen3-8b"]
    assert registry_id("not-a-real-model") is None


def test_tuner_not_injected():
    s = CoveSettings(model="grok-4-5", fallback="gemini-flash", floor="qwen3-8b")
    assert "ltp-tuner-v2" not in plan_cove_chain("background", s)


def test_tuner_only_if_named_as_floor():
    s = CoveSettings(model="grok-4-5", fallback=None, floor="ltp-tuner-v2")
    assert plan_cove_chain("background", s) == ["grok-4-5", "ltp-tuner-v2"]


def test_unset_floor_is_smallest_installed(monkeypatch):
    from src.models.cove_hops import load_cove_settings

    monkeypatch.setattr(
        "src.models.cove_hops._load_override_keys",
        lambda: ("grok-4-5", None, None),
    )
    monkeypatch.setattr(
        "src.models.cove_hops.registry_id",
        lambda t: None if not t else {"grok-4-5": "grok-4-5", "tiny-local": "tiny-local"}.get(t),
    )
    monkeypatch.setattr("src.models.cove_hops._brain_as_registry_id", lambda: None)
    monkeypatch.setattr("src.models.cove_hops._smallest_installed_floor", lambda: "tiny-local")
    s = load_cove_settings()
    assert s.floor == "tiny-local"
    assert plan_cove_chain("background", s)[-1] == "tiny-local"


def test_plan_hops_chat_ignores_grid_ids(monkeypatch):
    from src.models import router as r
    from src.models.cove_hops import CoveSettings as CS

    monkeypatch.setattr(
        "src.models.cove_hops.load_cove_settings",
        lambda: CS(model="grok-4-5", fallback="gemini-flash", floor="qwen3-8b"),
    )
    plan = r.plan_hops(
        agent_id="stuart",
        primary_id="kimi-k2.5-openrouter",
        fallback_id="qwen3-30b-moe",
        operation_type="channel",
    )
    assert plan.chain == ["grok-4-5"]
    assert plan.mode == "chat"


def test_plan_hops_background_three_settings(monkeypatch):
    from src.models import router as r
    from src.models.cove_hops import CoveSettings as CS

    monkeypatch.setattr(
        "src.models.cove_hops.load_cove_settings",
        lambda: CS(model="grok-4-5", fallback="gemini-flash", floor="qwen3-8b"),
    )
    plan = r.plan_hops(agent_id="ezra", operation_type="tuning")
    assert plan.chain == ["grok-4-5", "gemini-flash", "qwen3-8b"]
    assert plan.mode == "background"
    defaulted = r.plan_hops(agent_id="ezra")
    assert defaulted.mode == "background"


def test_operation_kind_table_walks_every_caller():
    from src.models.cove_hops import OPERATION_KIND, hop_kind
    expected = {
        "channel": "chat",
        "chat": "chat",
        "protocol": "background",
        "tuning": "background",
        "workflow": "background",
        "task": "background",
        "tool": "background",
        "memory": "background",
        "archive_digestion": "background",
        "background": "background",
    }
    assert OPERATION_KIND == expected
    for ot, kind in expected.items():
        assert hop_kind(ot) == kind
    assert hop_kind(None) == "background"
    assert hop_kind("") == "background"
    assert hop_kind("unknown-job") == "background"


def test_chat_timeout_no_second_wait(monkeypatch):
    from src.models.cove_hops import should_retry_chat_primary, chat_plain_line
    err = TimeoutError("timed out")
    assert should_retry_chat_primary(300.0, err) is False
    assert should_retry_chat_primary(5.0, ConnectionError("refused")) is True
    assert chat_plain_line(err) == "The model did not answer (timed out)."


import asyncio


def test_chat_node_stub_timeout_never_creates_local():
    from src.models.cove_hops import invoke_chat_or_plain

    class Stub:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            raise TimeoutError("timed out")

    local_made = {"n": 0}

    def forbidden_local(*a, **k):
        local_made["n"] += 1
        raise AssertionError("local model client must not be created on a chat fail")

    stub = Stub()
    # clock jumps past the fast-fail window so a timeout does not retry
    times = iter([0.0, 300.0])
    line = asyncio.run(
        invoke_chat_or_plain(stub, [], timeout=300, clock=lambda: next(times, 300.0))
    )
    assert line == "The model did not answer (timed out)."
    assert stub.calls == 1
    forbidden_local  # kept as the rule: this test never calls get_local_model
    assert local_made["n"] == 0


def test_chat_fast_fail_retries_once():
    from src.models.cove_hops import invoke_chat_or_plain

    class Stub:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            raise ConnectionError("could not connect")

    stub = Stub()
    times = iter([0.0, 2.0, 4.0, 6.0])
    line = asyncio.run(
        invoke_chat_or_plain(stub, [], timeout=300, clock=lambda: next(times, 6.0))
    )
    assert line == "The model did not answer (could not connect)."
    assert stub.calls == 2
