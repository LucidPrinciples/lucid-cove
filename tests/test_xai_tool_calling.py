"""grok-tool-calling-spec Part A — tool calling in ChatXAI (Responses API).

The base BaseChatModel.bind_tools raises NotImplementedError, so before this
change every agent turn (channels.py binds tools on every call) died and fell
back to local qwen. These tests prove ChatXAI now honors the LangChain tool
contract on the xAI Responses API (FLATTENED tool shape).

Runs two ways:
  - `pytest tests/test_xai_tool_calling.py`  (mocked, no token, no network)
  - `python tests/test_xai_tool_calling.py`  (standalone; offline checks, then
    an OPTIONAL live grok-4-5 round-trip if a real xAI token is cached — for
    in-container verification on Clearfield where pytest deps aren't installed).
"""
import json
import pathlib
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import src.models.xai_oauth as xo  # noqa: E402


# ── shared tool + mock plumbing (mirrors test_xai_oauth_transport.py) ──────────

@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f


class _MockResp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class _CaptureClient:
    """Async httpx client stand-in that records the last request payload and
    returns scripted responses in order. The queue is CLASS-level because a new
    client instance is created per `async with httpx.AsyncClient()` — a per-turn
    tool loop opens a fresh client each round, so the index must persist."""
    last_payload = None
    _responses: list = []
    _i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        type(self).last_payload = k.get("json")
        cls = _CaptureClient
        resp = cls._responses[min(cls._i, len(cls._responses) - 1)]
        cls._i += 1
        return resp


def _install(monkeypatch, responses):
    _CaptureClient.last_payload = None
    _CaptureClient._responses = responses if isinstance(responses, list) else [responses]
    _CaptureClient._i = 0
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _CaptureClient())


# ── unit: tool schema + tool_choice + bind_tools ──────────────────────────────

def test_tool_schema_is_flattened():
    schema = xo.ChatXAI._format_tool_for_xai(add)
    assert schema["type"] == "function"
    assert "function" not in schema           # NOT the nested chat-completions shape
    assert schema["name"] == "add"
    assert schema["parameters"]["type"] == "object"
    assert schema["description"]


def test_tool_choice_resolution():
    r = xo.ChatXAI._resolve_tool_choice
    assert r(None) is None
    assert r("auto") == "auto"
    assert r("required") == "required"
    assert r("any") == "required"
    assert r(True) == "required"
    assert r(False) == "none"
    assert r("add") == {"type": "function", "name": "add"}
    assert r({"type": "function", "name": "x"}) == {"type": "function", "name": "x"}


def test_bind_tools_no_notimplemented():
    """The whole point: bind_tools must NOT raise NotImplementedError, and must
    carry the flattened tools + tool_choice into the bound Runnable's kwargs."""
    model = xo.ChatXAI(model="grok-4-5")
    bound = model.bind_tools([add], tool_choice="auto")
    kw = getattr(bound, "kwargs", {})
    assert kw["tools"][0]["name"] == "add"
    assert kw["tool_choice"] == "auto"


# ── unit: message conversion (request build + tool return leg) ─────────────────

def test_build_input_orders_function_call_before_output():
    model = xo.ChatXAI(model="grok-4-5")
    msgs = [
        SystemMessage(content="You are Stuart."),
        HumanMessage(content="check the board"),
        AIMessage(content="", tool_calls=[
            {"name": "get_board", "args": {"lane": "NOW"}, "id": "call_abc", "type": "tool_call"},
        ]),
        ToolMessage(content="3 items", tool_call_id="call_abc"),
    ]
    instructions, items = model._build_responses_input(msgs)
    assert instructions == "You are Stuart."
    assert items[0] == {"role": "user", "content": "check the board"}
    fc = next(i for i, it in enumerate(items) if it.get("type") == "function_call")
    fo = next(i for i, it in enumerate(items) if it.get("type") == "function_call_output")
    assert fc < fo                                              # call precedes output
    assert items[fc]["arguments"] == json.dumps({"lane": "NOW"})
    assert items[fc]["call_id"] == "call_abc"
    assert items[fo]["call_id"] == "call_abc"
    assert items[fo]["output"] == "3 items"


def test_build_input_assistant_text_before_call():
    model = xo.ChatXAI(model="grok-4-5")
    msgs = [AIMessage(content="Let me check.", tool_calls=[
        {"name": "get_board", "args": {}, "id": "c1", "type": "tool_call"},
    ])]
    _instr, items = model._build_responses_input(msgs)
    assert items[0] == {"role": "assistant", "content": "Let me check."}
    assert items[1]["type"] == "function_call"


# ── unit: response parsing ─────────────────────────────────────────────────────

def test_parse_plain_text():
    m = xo.ChatXAI._parse_response(
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hi there"}]}]}
    )
    assert m.content == "hi there"
    assert not m.tool_calls


def test_parse_function_call():
    m = xo.ChatXAI._parse_response(
        {"output": [{"type": "function_call", "name": "add",
                     "arguments": '{"a": 2, "b": 3}', "call_id": "call_x"}]}
    )
    assert m.tool_calls[0]["name"] == "add"
    assert m.tool_calls[0]["args"] == {"a": 2, "b": 3}
    assert m.tool_calls[0]["id"] == "call_x"


def test_parse_mixed_text_and_call():
    m = xo.ChatXAI._parse_response({"output": [
        {"type": "message", "content": [{"type": "output_text", "text": "working"}]},
        {"type": "function_call", "name": "add", "arguments": "{}", "call_id": "c9"},
    ]})
    assert m.content == "working"
    assert len(m.tool_calls) == 1 and m.tool_calls[0]["id"] == "c9"


def test_parse_bad_args_is_empty_dict():
    m = xo.ChatXAI._parse_response(
        {"output": [{"type": "function_call", "name": "x", "arguments": "not json", "call_id": "c"}]}
    )
    assert m.tool_calls[0]["args"] == {}


def test_parse_chat_completions_fallback():
    m = xo.ChatXAI._parse_response({"choices": [{"message": {
        "content": "hi",
        "tool_calls": [{"id": "t1", "function": {"name": "add", "arguments": '{"a": 1}'}}],
    }}]})
    assert m.content == "hi"
    assert m.tool_calls[0]["name"] == "add" and m.tool_calls[0]["args"] == {"a": 1}


# ── integration (mocked): full ainvoke round-trip through LangChain ───────────

@pytest.mark.asyncio
async def test_agenerate_requests_tool(monkeypatch):
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install(monkeypatch, _MockResp(200, {"output": [
        {"type": "function_call", "name": "add", "arguments": '{"a": 2, "b": 3}', "call_id": "call_1"},
    ]}))
    model = xo.ChatXAI(model="grok-4-5")
    bound = model.bind_tools([add])
    resp = await bound.ainvoke([SystemMessage(content="sys"), HumanMessage(content="add 2 and 3")])
    assert isinstance(resp, AIMessage)
    assert resp.tool_calls, "expected tool_calls"
    tc = resp.tool_calls[0]
    assert tc["name"] == "add" and tc["args"] == {"a": 2, "b": 3} and tc["id"] == "call_1"
    # the flattened tool was actually sent
    assert _CaptureClient.last_payload["tools"][0]["name"] == "add"


@pytest.mark.asyncio
async def test_agenerate_reasoning_and_tools_coexist(monkeypatch):
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install(monkeypatch, _MockResp(200, {"output": [
        {"type": "message", "content": [{"type": "output_text", "text": "ok"}]},
    ]}))
    model = xo.ChatXAI(model="grok-4-5")
    bound = model.bind_tools([add])
    await bound.ainvoke([HumanMessage(content="hi")])
    pay = _CaptureClient.last_payload
    assert pay["reasoning"] == {"effort": "medium"}   # grok-4.x keeps reasoning
    assert pay["tools"][0]["name"] == "add"           # AND tools


@pytest.mark.asyncio
async def test_full_tool_loop_replay(monkeypatch):
    """Round 1 asks for the tool; round 2 (with the ToolMessage fed back) must
    replay history as function_call -> function_call_output in order, and return
    the final text answer."""
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install(monkeypatch, [
        _MockResp(200, {"output": [
            {"type": "function_call", "name": "add", "arguments": '{"a": 2, "b": 3}', "call_id": "call_1"},
        ]}),
        _MockResp(200, {"output": [
            {"type": "message", "content": [{"type": "output_text", "text": "The sum is 5."}]},
        ]}),
    ])
    model = xo.ChatXAI(model="grok-4-5")
    bound = model.bind_tools([add])
    r1 = await bound.ainvoke([SystemMessage(content="sys"), HumanMessage(content="add 2 and 3")])
    r2 = await bound.ainvoke([
        SystemMessage(content="sys"),
        HumanMessage(content="add 2 and 3"),
        r1,
        ToolMessage(content="5", tool_call_id="call_1"),
    ])
    pay = _CaptureClient.last_payload["input"]
    fc = next(i for i, it in enumerate(pay) if it.get("type") == "function_call")
    fo = next(i for i, it in enumerate(pay) if it.get("type") == "function_call_output")
    assert fc < fo
    assert r2.content == "The sum is 5."
    assert not r2.tool_calls


@pytest.mark.asyncio
async def test_response_metadata_model_name_stamped(monkeypatch):
    """The returned message must carry response_metadata.model_name so channels.py
    labels the turn (and the UI badge shows) the real model — not blank/local Ollama."""
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install(monkeypatch, _MockResp(200, {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }))
    model = xo.ChatXAI(model="grok-4.5")
    resp = await model.ainvoke([HumanMessage(content="hi")])
    assert resp.response_metadata.get("model_name") == "grok-4.5"
    assert resp.response_metadata.get("token_usage") == {"input_tokens": 10, "output_tokens": 3}


@pytest.mark.asyncio
async def test_plain_chat_still_works(monkeypatch):
    """Regression: no tools bound -> plain text, no NotImplementedError."""
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install(monkeypatch, _MockResp(200, {"output": [
        {"type": "message", "content": [{"type": "output_text", "text": "GROK_OK"}]},
    ]}))
    model = xo.ChatXAI(model="grok-4-5")
    resp = await model.ainvoke([HumanMessage(content="hi")])
    assert resp.content == "GROK_OK"
    assert not resp.tool_calls


# ── standalone runner (no pytest) + optional LIVE grok round-trip ─────────────

def _run_offline():
    """Run the non-async unit checks + mocked async round-trips without pytest.
    Returns True if all pass."""
    import asyncio

    results = []

    def _c(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, repr(e)))

    # sync unit checks
    _c("tool_schema_flattened", test_tool_schema_is_flattened)
    _c("tool_choice_resolution", test_tool_choice_resolution)
    _c("bind_tools_no_notimplemented", test_bind_tools_no_notimplemented)
    _c("build_input_order", test_build_input_orders_function_call_before_output)
    _c("assistant_text_before_call", test_build_input_assistant_text_before_call)
    _c("parse_plain_text", test_parse_plain_text)
    _c("parse_function_call", test_parse_function_call)
    _c("parse_mixed", test_parse_mixed_text_and_call)
    _c("parse_bad_args", test_parse_bad_args_is_empty_dict)
    _c("parse_cc_fallback", test_parse_chat_completions_fallback)

    # mocked async round-trips via a tiny monkeypatch shim
    class _MP:
        """Minimal monkeypatch stand-in supporting both call shapes:
        setattr("mod.attr", value) and setattr(obj, "attr", value)."""
        def __init__(self): self._saved = []
        def setattr(self, *args):
            if len(args) == 2:
                target, value = args
                import importlib
                modname, attr = target.rsplit(".", 1)
                obj = importlib.import_module(modname)
            else:
                obj, attr, value = args
            self._saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, value)
        def undo(self):
            for obj, attr, old in reversed(self._saved):
                setattr(obj, attr, old)

    async def _run_async(fn):
        mp = _MP()
        try:
            await fn(mp)
        finally:
            mp.undo()

    for name, fn in [
        ("agenerate_requests_tool", test_agenerate_requests_tool),
        ("reasoning_and_tools_coexist", test_agenerate_reasoning_and_tools_coexist),
        ("full_tool_loop_replay", test_full_tool_loop_replay),
        ("response_metadata_model_name", test_response_metadata_model_name_stamped),
        ("plain_chat_still_works", test_plain_chat_still_works),
    ]:
        try:
            asyncio.run(_run_async(fn))
            results.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, repr(e)))

    ok = True
    for name, passed, extra in results:
        print(("PASS" if passed else "FAIL"), "-", name, ("" if passed else ":: " + extra))
        ok = ok and passed
    return ok


async def _run_live():
    """OPTIONAL live round-trip against grok-4-5 using the cached per-box token.
    Skips cleanly if no token is present. This is the acceptance test that proves
    a real tool-using turn runs on grok (not fallback)."""
    if not xo.TOKEN_CACHE_FILE.exists():
        print("SKIP - live grok round-trip (no cached xAI token at %s)" % xo.TOKEN_CACHE_FILE)
        return True

    # Resolve through the registry exactly as an agent does: the assignment key
    # "grok-4-5" maps (config/models.yaml) to provider xai-oauth + model_string
    # "grok-4.5" (the API rejects the dashed "grok-4-5" with Model not found).
    # This makes the live test a true acceptance check of Stuart's assignment.
    try:
        from src.models.provider import get_model_client
        model = get_model_client("grok-4-5")
    except Exception as e:  # noqa: BLE001
        print("NOTE - registry resolve unavailable (%r); using ChatXAI(model='grok-4.5')" % e)
        model = xo.ChatXAI(model="grok-4.5")
    bound = model.bind_tools([add])
    print("LIVE - round 1: asking grok-4-5 to add 2 and 3 (expect a tool call)...")
    r1 = await bound.ainvoke([
        SystemMessage(content="You can call tools. Use the add tool to add numbers."),
        HumanMessage(content="What is 2 + 3? Use the add tool."),
    ])
    if not getattr(r1, "tool_calls", None):
        print("FAIL - grok did not request the tool. content=%r" % (r1.content,))
        return False
    tc = r1.tool_calls[0]
    print("LIVE - grok requested:", tc["name"], tc["args"], "id=", tc["id"])
    tool_result = str(add.invoke(tc["args"]))
    print("LIVE - round 2: feeding tool result back (%s)..." % tool_result)
    r2 = await bound.ainvoke([
        SystemMessage(content="You can call tools. Use the add tool to add numbers."),
        HumanMessage(content="What is 2 + 3? Use the add tool."),
        r1,
        ToolMessage(content=tool_result, tool_call_id=tc["id"]),
    ])
    print("LIVE - grok final answer:", repr(r2.content))
    ok = "5" in (r2.content or "")
    print(("PASS" if ok else "FAIL"), "- live grok-4-5 tool round-trip")
    return ok


if __name__ == "__main__":
    import asyncio
    print("=== OFFLINE (no token / no network) ===")
    offline_ok = _run_offline()
    print("\n=== LIVE (grok-4-5, needs cached xAI token) ===")
    live_ok = asyncio.run(_run_live())
    print()
    if offline_ok and live_ok:
        print("ALL TOOL-CALLING TESTS PASSED")
        sys.exit(0)
    print("SOME TESTS FAILED")
    sys.exit(1)
