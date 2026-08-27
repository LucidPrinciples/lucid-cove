"""Ledger WebDAV writes retry a transient Nextcloud 423 lock."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.dashboard.routes import bookkeeping as routes


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _ScriptedClient:
    def __init__(self, codes):
        self.codes = list(codes)
        self.puts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def put(self, *a, **k):
        self.puts += 1
        code = self.codes.pop(0) if self.codes else 500
        return _Resp(code)


async def _run_write(client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "_webdav",
        AsyncMock(return_value=("https://nc.example/dav", "user", ("user", "pw"), None)),
    )
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(routes.asyncio, "sleep", AsyncMock())
    with patch(
        "src.dashboard.routes.files._kb_write_guard",
        AsyncMock(return_value=None),
    ), patch(
        "src.dashboard.routes.files._operator_shared_agent_guard",
        AsyncMock(return_value=None),
    ):
        return await routes._write_ledger(
            MagicMock(),
            "Bookkeeping/Organize/a.mapped.json",
            {"rows": []},
        )


@pytest.mark.asyncio
async def test_write_ledger_retries_http_423(monkeypatch):
    client = _ScriptedClient([423, 204])
    err, status = await _run_write(client, monkeypatch)
    assert err is None
    assert status == 200
    assert client.puts == 2


@pytest.mark.asyncio
async def test_write_ledger_gives_up_on_persistent_423(monkeypatch):
    client = _ScriptedClient([423, 423, 423, 423, 423])
    err, status = await _run_write(client, monkeypatch)
    assert status == 502
    assert err == "Write failed: HTTP 423"
    assert client.puts == 5
