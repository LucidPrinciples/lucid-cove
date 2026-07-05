"""
batch-10 #4c — Haven invite must report real delivery failures.

The T9 federation invite failure was invisible: _invite swallowed non-M_FORBIDDEN
errors and the endpoint always returned a flat "Invited.". _invite now returns a list
of failure dicts so the endpoint can surface a truthful message; already-invited/joined
(M_FORBIDDEN) is not a failure.
"""

import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.matrix_haven as mh  # noqa: E402


def _fake_http(results):
    """results: dict keyed by (status,errcode) sequence -> returns them in order."""
    seq = list(results)

    async def _http(method, path, token=None, body=None):
        return seq.pop(0)

    return _http


@pytest.mark.asyncio
async def test_invite_collects_real_failures(monkeypatch):
    # Two rooms x one user: first invite 403 M_LIMIT-ish, second 200.
    monkeypatch.setattr(mh, "_http", _fake_http([
        (500, {"error": "federation timeout", "errcode": "M_UNKNOWN"}),
        (200, {}),
    ]))
    failures = await mh._invite("tok", ["!space:x", "!commons:x"], ["@ernie:matrix.muller"])
    assert len(failures) == 1
    assert failures[0]["user"] == "@ernie:matrix.muller"
    assert failures[0]["error"] == "federation timeout"


@pytest.mark.asyncio
async def test_already_invited_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(mh, "_http", _fake_http([
        (403, {"errcode": "M_FORBIDDEN", "error": "already in the room"}),
        (403, {"errcode": "M_FORBIDDEN"}),
    ]))
    failures = await mh._invite("tok", ["!space:x", "!commons:x"], ["@sam:x"])
    assert failures == []


@pytest.mark.asyncio
async def test_all_delivered_no_failures(monkeypatch):
    monkeypatch.setattr(mh, "_http", _fake_http([(200, {}), (200, {})]))
    failures = await mh._invite("tok", ["!space:x", "!commons:x"], ["@sam:x"])
    assert failures == []
