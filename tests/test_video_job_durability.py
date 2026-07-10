# #D39 — the async video-job registry must survive an app restart. It was
# in-memory only, so a job that FINISHED in pipecat showed the browser an error
# (its id 404'd after the restart). Jobs now mirror their STATE to video_jobs; a
# boot sweep orphan-marks still-running rows 'failed'; job_status falls back to
# the durable row when the in-memory job is gone.
import contextlib
import pathlib

import pytest

from src.dashboard.routes import video_jobs as vj


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows_per_call=None):
        self.calls = []
        self._rows = list(rows_per_call or [])

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        rows = self._rows.pop(0) if self._rows else []
        return _FakeResult(rows)


def _patch_db(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def _cm():
        yield conn
    monkeypatch.setattr("src.memory.database.get_db", lambda: _cm())


@pytest.fixture(autouse=True)
def _clean():
    vj._JOBS.clear()
    yield
    vj._JOBS.clear()


@pytest.mark.asyncio
async def test_persist_job_upserts_state(monkeypatch):
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    vj._JOBS["j1"] = {"kind": "transcribe", "state": "running", "phase": "transferring",
                      "error": None, "created_at": 1.0, "started_at": 2.0, "finished_at": None}
    await vj._persist_job("j1")
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "INSERT INTO video_jobs" in sql and "ON CONFLICT" in sql
    assert params[0] == "j1" and params[2] == "running"
    # the result payload is intentionally NOT persisted (keep the row light)
    assert "result" not in sql


@pytest.mark.asyncio
async def test_persist_missing_job_is_noop(monkeypatch):
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    await vj._persist_job("nope")
    assert conn.calls == []


@pytest.mark.asyncio
async def test_persist_never_raises_on_db_error(monkeypatch):
    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    monkeypatch.setattr("src.memory.database.get_db", lambda: _boom())
    vj._JOBS["j"] = {"kind": "analyze", "state": "running"}
    await vj._persist_job("j")  # must swallow


@pytest.mark.asyncio
async def test_boot_sweep_marks_running_failed(monkeypatch):
    conn = _FakeConn(rows_per_call=[[{"job_id": "a"}, {"job_id": "b"}]])
    _patch_db(monkeypatch, conn)
    n = await vj.sweep_orphaned_video_jobs()
    assert n == 2
    sql, _ = conn.calls[0]
    assert "UPDATE video_jobs" in sql
    assert "state = 'failed'" in sql
    # scoped: only queued/running rows, never done/failed
    assert "state IN ('queued','running')" in sql


@pytest.mark.asyncio
async def test_boot_sweep_returns_zero_on_error(monkeypatch):
    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    monkeypatch.setattr("src.memory.database.get_db", lambda: _boom())
    assert await vj.sweep_orphaned_video_jobs() == 0


@pytest.mark.asyncio
async def test_job_status_falls_back_to_durable_row(monkeypatch):
    # in-memory registry empty (post-restart) → serve the durable row, not a 404
    conn = _FakeConn(rows_per_call=[[{"state": "failed", "phase": "failed",
                                      "kind": "transcribe", "error": "interrupted by a restart",
                                      "created_at": 1.0, "started_at": 2.0, "finished_at": 3.0}]])
    _patch_db(monkeypatch, conn)
    out = await vj.job_status("gone", request=None)
    assert out["state"] == "failed"
    assert out["persisted"] is True
    assert out["job_id"] == "gone"


@pytest.mark.asyncio
async def test_job_status_404_when_truly_unknown(monkeypatch):
    conn = _FakeConn(rows_per_call=[[]])  # no durable row either
    _patch_db(monkeypatch, conn)
    out = await vj.job_status("ghost", request=None)
    assert getattr(out, "status_code", None) == 404


def test_ws_handles_disconnect_message_as_info():
    # voice/ws.py must detect the ASGI disconnect message and break, instead of
    # looping into a second receive() that raises RuntimeError and logs an ERROR
    # per page-close (the flood #D39 quiets).
    ws = pathlib.Path(__file__).resolve().parents[1] / "voice" / "src" / "ws.py"
    src = ws.read_text()
    assert 'message.get("type") == "websocket.disconnect"' in src
    assert "break" in src.split('websocket.disconnect')[1][:120]
