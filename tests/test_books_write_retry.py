"""Ledger WebDAV writes retry a transient Nextcloud 423 lock."""

import asyncio
import copy
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.dashboard import books as bk
from src.dashboard import books_jobs as bjobs
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


@pytest.mark.asyncio
async def test_ledger_lock_is_one_per_presence():
    routes._LEDGER_LOCKS.clear()
    a = await routes._ledger_lock("11111111-1111-1111-1111-111111111111")
    b = await routes._ledger_lock("11111111-1111-1111-1111-111111111111")
    c = await routes._ledger_lock("22222222-2222-2222-2222-222222222222")
    assert a is b
    assert a is not c
    assert a.locked() is False


class _MemLedger:
    def __init__(self, payload):
        self.payload = copy.deepcopy(payload)

    async def read(self, request, path):
        return copy.deepcopy(self.payload), path, None, 200

    async def write(self, request, path, payload):
        self.payload = copy.deepcopy(payload)
        return None, 200


@pytest.mark.asyncio
async def test_process_job_keeps_line_edit_made_during_run(monkeypatch):
    path = "Bookkeeping/Organize/checking.mapped.json"
    start = {
        "account": "Checking",
        "filing_book": "chords",
        "working_chart": [
            {"label": "Advertising", "code": None, "layer": "official"},
            {"label": "Office expense", "code": None, "layer": "official"},
        ],
        "vendor_map": [],
        "rows": [
            {
                "origin": "statement",
                "disabled": False,
                "needs_review": False,
                "match_rule": "operator",
                "source_file": "old.pdf",
                "category_label": "Office expense",
                "fields": {"Date": "2025-01-10", "Name": "Staples Store", "Debit/Credit": -9.0},
            }
        ],
    }
    store = _MemLedger(start)
    started = threading.Event()
    gate = threading.Event()

    def slow_extract(blob, kind, year, cap):
        started.set()
        assert gate.wait(timeout=5)
        return (
            [{"date": "2025-02-01", "payee": "New Vendor LLC", "amount": -3.0}],
            "text",
            [],
        )

    monkeypatch.setattr(routes, "_read_ledger", store.read)
    monkeypatch.setattr(routes, "_write_ledger_unlocked", store.write)
    monkeypatch.setattr(routes, "_presence_id_for_lock", AsyncMock(return_value="11111111-1111-1111-1111-111111111111"))
    monkeypatch.setattr(routes, "_read_drop_bytes", AsyncMock(return_value=(b"stmt", None, 200)))
    monkeypatch.setattr(routes.bk, "extract_and_parse_statement", slow_extract)

    req = MagicMock()
    created = bjobs.create_process_job(actor_id="p1", path=path, files=1)
    task = asyncio.create_task(
        routes._run_process_job(
            req,
            created["job_id"],
            ledger_path=path,
            clean=path,
            payload=copy.deepcopy(start),
            folder="Bookkeeping/Drop/Checking",
            label="Checking",
            book="chords",
            year_i=2025,
            work=["feb.txt"],
            seen_src=set(),
            ledger_book_before="chords",
        )
    )
    assert await asyncio.to_thread(started.wait, 5)

    def recode(fresh):
        updated, err = bk.apply_category(fresh, 0, "Advertising")
        return updated, None, err

    edited, _clean, err, status, _extra = await routes._mutate_ledger(req, path, recode)
    assert err is None and status == 200
    assert edited["rows"][0]["category_label"] == "Advertising"
    gate.set()
    await task

    rows = store.payload["rows"]
    assert rows[0]["category_label"] == "Advertising"
    assert rows[0]["fields"]["Name"] == "Staples Store"
    assert any(r.get("fields", {}).get("Name") == "New Vendor LLC" for r in rows)
