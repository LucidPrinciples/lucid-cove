# #D26 — the interactive chat turn ran on LangGraph's default recursion_limit (25); a
# heavy tool-using turn hit the ceiling and surfaced as a generic error. Chat must set a
# ceiling at least as high as delegation's, wire it into the graph config, and surface a
# clear message when the limit is genuinely hit. No SSE harness in-repo → assert the
# contract + wiring on the source.
import pathlib

from src.dashboard.routes.chat import CHAT_RECURSION_LIMIT
from src.tools.delegation_tools import DELEGATION_RECURSION_LIMIT

CHAT = pathlib.Path(__file__).resolve().parents[1] / "src" / "dashboard" / "routes" / "chat.py"


def test_chat_recursion_limit_at_least_delegation():
    # "the same or higher" — never fall back to the default 25.
    assert CHAT_RECURSION_LIMIT >= DELEGATION_RECURSION_LIMIT
    assert CHAT_RECURSION_LIMIT > 25


def test_cfg_wires_recursion_limit():
    src = CHAT.read_text()
    assert '"recursion_limit": CHAT_RECURSION_LIMIT' in src


def test_recursion_error_is_surfaced_clearly():
    src = CHAT.read_text()
    assert "except GraphRecursionError" in src
    assert "from langgraph.errors import GraphRecursionError" in src
    # a distinct, human message + machine code rather than a bare stack string
    assert "step ceiling" in src
    assert '"code": "recursion_limit"' in src
