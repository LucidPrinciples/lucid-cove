"""Retry/backoff around the GitHub deploy calls (github._gh_send).

A big site's `POST git/trees` 502'd with no retry and killed the whole deploy
(chordsoftruth, 864 files). _gh_send retries transient 5xx + network errors with
exponential backoff. Runs standalone (`python tests/test_github_retry.py`) and
under pytest.
"""
import asyncio
import pathlib
import sys
import types

import httpx

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.utils import github as gh  # noqa: E402


class _Resp:
    def __init__(self, status):
        self.status_code = status


class _Client:
    """Returns each queued status in order (last one repeats); an Exception in
    the queue is raised instead of returned."""
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def request(self, method, url, headers=None, **kw):
        item = self.seq[min(self.calls, len(self.seq) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def _no_sleep():
    async def _s(*a, **k):
        return None
    return _s


def _run(seq, retries=4):
    """Run _gh_send against a fake client with sleep stubbed; return (resp, client)."""
    gh.asyncio = types.SimpleNamespace(sleep=_no_sleep())  # no real backoff waits
    client = _Client(seq)
    try:
        resp = asyncio.new_event_loop().run_until_complete(
            gh._gh_send(client, "POST", "https://api.github.com/x", headers={}, retries=retries)
        )
    finally:
        gh.asyncio = asyncio  # restore
    return resp, client


def test_retries_transient_502_then_succeeds():
    resp, client = _run([502, 502, 201])
    assert resp.status_code == 201
    assert client.calls == 3  # two 502s retried, third ok


def test_exhausts_retries_returns_last_5xx():
    resp, client = _run([502, 502, 502, 502], retries=4)
    assert resp.status_code == 502   # returned so caller's raise_for_status fires
    assert client.calls == 4


def test_no_retry_on_4xx():
    resp, client = _run([422, 201])
    assert resp.status_code == 422   # 422 is not retryable (branch-exists path)
    assert client.calls == 1


def test_retries_network_error_then_succeeds():
    resp, client = _run([httpx.ConnectError("boom"), 201])
    assert resp.status_code == 201
    assert client.calls == 2


def test_success_first_try():
    resp, client = _run([201])
    assert resp.status_code == 201
    assert client.calls == 1


if __name__ == "__main__":
    tests = [
        ("retries_502_then_succeeds", test_retries_transient_502_then_succeeds),
        ("exhausts_retries_returns_last_5xx", test_exhausts_retries_returns_last_5xx),
        ("no_retry_on_4xx", test_no_retry_on_4xx),
        ("retries_network_error", test_retries_network_error_then_succeeds),
        ("success_first_try", test_success_first_try),
    ]
    ok = True
    for name, fn in tests:
        try:
            fn()
            print("PASS -", name)
        except Exception as e:  # noqa: BLE001
            ok = False
            print("FAIL -", name, "::", repr(e))
    print("\nALL GITHUB RETRY TESTS PASSED" if ok else "\nSOME TESTS FAILED")
    sys.exit(0 if ok else 1)
