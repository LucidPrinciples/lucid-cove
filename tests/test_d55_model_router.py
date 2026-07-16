"""#D55 — local/API escalate-on-hard router unit tests."""

from src.models.router import (
    THRESHOLD_API,
    THRESHOLD_LOCAL,
    clear_failure_memory,
    plan_hops,
    record_hop_failure,
    score_turn,
)


def setup_function():
    clear_failure_memory()


def test_easy_steward_scores_below_api_threshold():
    sc = score_turn(
        agent_id="stuart",
        message_text="what's next on the board and check the calendar tomorrow",
        message_count=4,
        approx_tokens=500,
        operation_type="channel",
    )
    assert sc.score < THRESHOLD_API
    assert any("role_floor:stuart" in r for r in sc.reasons)
    assert any("lang:easy" in r for r in sc.reasons)


def test_hard_build_scores_high():
    sc = score_turn(
        agent_id="archimedes",
        message_text="design and implement a multi-file refactor to debug the security auth path",
        message_count=30,
        approx_tokens=9000,
        tool_names=["git_commit", "git_push", "run_shell", "create_github_pr"],
        operation_type="build",
    )
    assert sc.score >= THRESHOLD_API
    assert any("tools:mutation" in r for r in sc.reasons)
    assert any("lang:hard" in r for r in sc.reasons)


def test_prefer_cloud_bias_raises_score():
    base = score_turn(agent_id="stuart", message_text="status please", routing_bias="balanced")
    hot = score_turn(agent_id="stuart", message_text="status please", routing_bias="prefer-cloud")
    assert hot.score > base.score


def test_local_first_on_easy_when_both_available(monkeypatch):
    monkeypatch.setattr("src.models.router._runnable", lambda mid: True)
    monkeypatch.setattr("src.models.router._is_local_id", lambda mid: str(mid).startswith("local-"))
    monkeypatch.setattr("src.models.router._provider_of", lambda mid: "ollama" if str(mid).startswith("local-") else "openrouter")
    monkeypatch.setattr("src.models.router._installed_local", lambda: "local-qwen")

    plan = plan_hops(
        agent_id="stuart",
        primary_id="kimi-cloud",
        fallback_id="local-qwen",
        message_text="add milk to the grocery list",
        message_count=2,
        operation_type="channel",
        allow_cloud_middle=False,
    )
    assert plan.mode == "local+api"
    assert plan.first_id == "local-qwen"
    assert plan.chain[0] == "local-qwen"
    assert "kimi-cloud" in plan.chain


def test_api_first_on_hard_when_both_available(monkeypatch):
    monkeypatch.setattr("src.models.router._runnable", lambda mid: True)
    monkeypatch.setattr("src.models.router._is_local_id", lambda mid: str(mid).startswith("local-"))
    monkeypatch.setattr(
        "src.models.router._provider_of",
        lambda mid: "ollama" if str(mid).startswith("local-") else "openrouter",
    )
    monkeypatch.setattr("src.models.router._installed_local", lambda: "local-qwen")

    plan = plan_hops(
        agent_id="archimedes",
        primary_id="kimi-cloud",
        fallback_id="local-qwen",
        message_text="design and implement a multi-file security debug refactor",
        message_count=25,
        approx_tokens=10000,
        tool_names=["git_push", "run_shell"],
        operation_type="build",
        allow_cloud_middle=False,
    )
    assert plan.score.score >= THRESHOLD_API
    assert plan.first_id == "kimi-cloud"
    assert plan.chain[0] == "kimi-cloud"
    assert "local-qwen" in plan.chain


def test_api_only_mode_skips_local(monkeypatch):
    monkeypatch.setattr("src.models.router._runnable", lambda mid: True)
    monkeypatch.setattr("src.models.router._is_local_id", lambda mid: False)
    monkeypatch.setattr("src.models.router._provider_of", lambda mid: "openrouter")
    monkeypatch.setattr("src.models.router._installed_local", lambda: None)

    plan = plan_hops(
        agent_id="stuart",
        primary_id="kimi-cloud",
        fallback_id="deepseek-cloud",
        message_text="hello",
        allow_cloud_middle=False,
    )
    assert plan.mode == "api-only"
    assert plan.first_id == "kimi-cloud"
    assert all(not str(x).startswith("local") for x in plan.chain)


def test_local_only_mode(monkeypatch):
    monkeypatch.setattr("src.models.router._runnable", lambda mid: True)
    monkeypatch.setattr("src.models.router._is_local_id", lambda mid: True)
    monkeypatch.setattr("src.models.router._provider_of", lambda mid: "ollama")
    monkeypatch.setattr("src.models.router._installed_local", lambda: "local-qwen")

    plan = plan_hops(
        agent_id="stuart",
        primary_id="local-qwen",
        fallback_id=None,
        message_text="design a complex system",  # hard language still can't invent API
        allow_cloud_middle=False,
    )
    assert plan.mode == "local-only"
    assert plan.first_id == "local-qwen"


def test_recent_failures_raise_score_and_log_reason():
    record_hop_failure("kimi-cloud", "moonshot")
    record_hop_failure("kimi-cloud", "moonshot")
    sc = score_turn(agent_id="stuart", message_text="status")
    # fail pressure applied in plan_hops, not score_turn — check plan
    from src.models.router import plan_hops as ph

    # monkeypatch pool
    import src.models.router as r

    r._runnable = lambda mid: True  # type: ignore
    r._is_local_id = lambda mid: str(mid).startswith("local-")  # type: ignore
    r._provider_of = lambda mid: "ollama" if str(mid).startswith("local-") else "moonshot"  # type: ignore
    r._installed_local = lambda: "local-qwen"  # type: ignore

    plan = ph(
        agent_id="stuart",
        primary_id="kimi-cloud",
        fallback_id="local-qwen",
        message_text="status",
        allow_cloud_middle=False,
    )
    assert plan.score.score >= sc.score
    assert any("recent_fail" in x for x in plan.score.reasons)


def test_thresholds_ordered():
    assert 0 < THRESHOLD_LOCAL < THRESHOLD_API < 100
