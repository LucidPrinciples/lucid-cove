# #D23 — the approval-resolution injection queried a nonexistent `thread_state` table, so
# #D14's auto-continuation silently no-opped (bare except swallowed the error). It must
# query chat_threads (channel + status='active'), the same source of truth as
# delegation_tools._report_back, and must not swallow failures silently. No DB/graph
# harness for this path in-repo, so guard the source directly.
import pathlib
import re

HOME = pathlib.Path(__file__).resolve().parents[1] / "src" / "dashboard" / "routes" / "home.py"


def _inject_fn_source():
    src = HOME.read_text()
    start = src.index("async def _inject_approval_resolution_message")
    # up to the next top-level def/decorator after it
    rest = src[start + 1:]
    end = re.search(r"\n@router\.|\n# ==========", rest)
    body = rest[: end.start()] if end else rest
    # strip line comments so we test the CODE, not the explanatory prose
    return "\n".join(line.split("#", 1)[0] for line in body.splitlines())


def test_does_not_query_nonexistent_thread_state_table():
    code = _inject_fn_source()
    assert "FROM thread_state" not in code
    assert "thread_state" not in code


def test_queries_active_chat_threads():
    code = _inject_fn_source()
    assert "FROM chat_threads" in code
    assert "status = 'active'" in code


def test_failures_are_surfaced_not_swallowed():
    # the outer handler must log the failure, not `pass`
    code = _inject_fn_source()
    assert "approval-inject" in HOME.read_text()
    # no bare "except Exception:\n <indent> pass" swallow in the function
    assert not re.search(r"except Exception:\s*\n\s*pass", code)
