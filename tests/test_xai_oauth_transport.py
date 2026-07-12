"""AUDIT-F3/F4/F5 — xAI OAuth transport robustness.

F4: poll_for_token must not block the event loop on slow_down (await asyncio.sleep).
F3: the 403 handler must not crash on a non-dict / non-JSON error body.
F5: sync _generate must not call asyncio.run() from inside a running loop, and
    _astream must yield ChatGenerationChunk.
"""
import pathlib
import sys

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import src.models.xai_oauth as xo  # noqa: E402


class _MockResp:
    def __init__(self, status_code, json_data=None, text="", raise_json=False):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not JSON")
        return self._json


class _MockClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return self._resp


def _install_client(monkeypatch, resp):
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _MockClient(resp))


# ── F4 ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_slow_down_uses_async_sleep(monkeypatch):
    monkeypatch.setattr(xo, "_get_oauth_config", lambda: {"client_id": "x", "client_secret": "y"})
    _install_client(monkeypatch, _MockResp(400, {"error": "slow_down"}))

    slept = {"async": 0}

    async def _fake_async_sleep(_secs):
        slept["async"] += 1

    def _boom_sync_sleep(_secs):
        raise AssertionError("time.sleep would block the event loop")

    monkeypatch.setattr(xo.asyncio, "sleep", _fake_async_sleep)
    monkeypatch.setattr(xo.time, "sleep", _boom_sync_sleep)

    result = await xo.poll_for_token("devcode")
    assert result is None            # keep polling
    assert slept["async"] == 1       # awaited, not blocked


# ── F3 ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_403_string_error_body(monkeypatch):
    """error as a STRING (not a dict) must not raise AttributeError."""
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install_client(monkeypatch, _MockResp(403, {"error": "SuperGrok subscription required"}))

    model = xo.ChatXAI(model="grok-build-0.1")
    with pytest.raises(RuntimeError) as ei:
        await model._agenerate([HumanMessage(content="hi")])
    assert "subscription" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_403_non_json_body(monkeypatch):
    """A 403 with an HTML/empty body (resp.json raises) must surface a clean RuntimeError."""
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    _install_client(monkeypatch, _MockResp(403, text="<html>forbidden</html>", raise_json=True))

    model = xo.ChatXAI(model="grok-build-0.1")
    with pytest.raises(RuntimeError) as ei:
        await model._agenerate([HumanMessage(content="hi")])
    assert "403" in str(ei.value)


# ── F5 ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_generate_from_running_loop(monkeypatch):
    """_generate called while an event loop is running must not raise the asyncio.run
    'cannot be called from a running event loop' error — it bridges via a thread."""
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    ok = _MockResp(200, {"output": [
        {"type": "message", "content": [{"type": "text", "text": "hello world"}]}
    ]})
    _install_client(monkeypatch, ok)

    model = xo.ChatXAI(model="grok-build-0.1")
    result = model._generate([HumanMessage(content="hi")])   # sync call inside running loop
    assert isinstance(result, ChatResult)
    assert result.generations[0].message.content == "hello world"


@pytest.mark.asyncio
async def test_astream_yields_chunks(monkeypatch):
    monkeypatch.setattr(xo, "get_valid_access_token", _async_return("tok"))
    ok = _MockResp(200, {"output": [
        {"type": "message", "content": [{"type": "text", "text": "streamed"}]}
    ]})
    _install_client(monkeypatch, ok)

    model = xo.ChatXAI(model="grok-build-0.1")
    chunks = [c async for c in model._astream([HumanMessage(content="hi")])]
    assert chunks and all(isinstance(c, ChatGenerationChunk) for c in chunks)
    assert "".join(c.message.content for c in chunks) == "streamed"


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f
