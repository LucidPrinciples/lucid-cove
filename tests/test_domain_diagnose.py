from src.dashboard.routes.domain import _diagnosis_task, _clean_reply


def test_task_includes_error_and_step_and_guardrail():
    t = _diagnosis_task("boom: DNS record not found", "set the Cove address")
    assert "boom: DNS record not found" in t
    assert "set the Cove address" in t
    assert "dead end" in t.lower()


def test_clean_reply_strips_think_block():
    assert _clean_reply("<think>hmm let me see</think>Check your DNS A record.") == "Check your DNS A record."


def test_clean_reply_empty_is_empty():
    assert _clean_reply("   ") == ""
    assert _clean_reply(None) == ""
