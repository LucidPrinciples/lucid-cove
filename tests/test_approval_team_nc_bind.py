"""Approved tool re-execution must bind team NC like chat/delegation.

Without the bind, nextcloud_* runs as the founding-operator fallback and
admin-tree paths 404 as Not found after Approve.
"""
import inspect
import pathlib
import re

import pytest

APPROVAL = pathlib.Path(__file__).resolve().parents[1] / "src" / "tools" / "approval.py"


def _execute_src() -> str:
    src = APPROVAL.read_text()
    start = src.index("async def execute_approved_tool")
    rest = src[start:]
    # next top-level def after this function
    end = re.search(r"\n\ndef |\n\nasync def |\n# =====", rest[1:])
    body = rest[: end.start() + 1] if end else rest
    return body


def test_execute_approved_binds_team_nc_creds():
    body = _execute_src()
    assert "set_team_nc_creds" in body
    assert "set_acting_channel" in body
    assert "clear_request_nc_creds" in body
    assert "clear_acting_channel" in body
    assert "_is_manager_channel" in body
    assert "_team_agent_key" in body


def test_execute_approved_clears_nc_bind_in_finally():
    body = _execute_src()
    assert "finally:" in body
    # clear must run after invoke, not only on the happy path
    finally_idx = body.index("finally:")
    assert "clear_request_nc_creds" in body[finally_idx:]
    assert "clear_acting_channel" in body[finally_idx:]


def test_execute_approved_reads_channel_from_row():
    body = _execute_src()
    assert 'row["channel"]' in body or "row.get(\"channel\"" in body or 'row["channel"]' in body
    # channel drives manager/team bind
    assert "channel" in body


@pytest.mark.asyncio
async def test_execute_approved_calls_set_team_nc_for_manager_channel(monkeypatch):
    """Runtime: steward-day approval binds admin NC before ainvoke."""
    from src.tools import approval as appr

    calls = {"team_nc": 0, "acting": [], "ainvoke": 0, "cleared_nc": 0, "cleared_ch": 0}

    class _Row(dict):
        def keys(self):
            return super().keys()

    row = _Row(
        request_id="ab12cd34",
        tool_name="nextcloud_delete",
        args={"path": "Documents/Projects/Warehouse Liquidation/plan.md"},
        status="approved",
        channel="stuart-day",
    )

    class _Result:
        def __init__(self, value):
            self._value = value

        async def fetchone(self):
            return self._value

    class _Conn:
        async def execute(self, sql, params=None):
            if "SELECT" in sql:
                return _Result(row)
            return _Result(None)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _get_db():
        return _Conn()

    class _Tool:
        name = "nextcloud_delete"

        async def ainvoke(self, args):
            calls["ainvoke"] += 1
            from src.tools import nextcloud_tools as nc
            # Creds must already be bound when the tool runs.
            user = nc._nc_user()
            calls["bound_user"] = user
            return f"deleted as {user}"

    class _AT:
        ALL_FILE_TOOLS = [_Tool()]

        @staticmethod
        def get_agent_tools(agent_id):
            return [_Tool()]

    monkeypatch.setattr(appr, "get_db", _get_db, raising=False)
    monkeypatch.setattr("src.memory.database.get_db", _get_db)

    import src.tools.agent_tools as at_mod
    monkeypatch.setattr(at_mod, "ALL_FILE_TOOLS", [_Tool()], raising=False)
    # ensure dir() picks it up — patch module attrs used by execute loop
    for name in dir(at_mod):
        if name.startswith("ALL_") and name.endswith("_TOOLS"):
            monkeypatch.setattr(at_mod, name, [], raising=False)
    monkeypatch.setattr(at_mod, "ALL_FILE_TOOLS", [_Tool()], raising=False)
    monkeypatch.setattr(at_mod, "get_agent_tools", lambda agent_id: [_Tool()])

    import src.tools.nextcloud_tools as nc

    real_set_request = nc.set_request_nc_creds
    real_set_acting = nc.set_acting_channel
    real_clear_nc = nc.clear_request_nc_creds
    real_clear_ch = nc.clear_acting_channel

    def _team():
        calls["team_nc"] += 1
        return real_set_request("http://nc.test", "coveadmin", "secret")

    def _acting(ch):
        calls["acting"].append(ch)
        return real_set_acting(ch)

    def _clear_nc(tok):
        calls["cleared_nc"] += 1
        return real_clear_nc(tok)

    def _clear_ch(tok):
        calls["cleared_ch"] += 1
        return real_clear_ch(tok)

    monkeypatch.setattr(nc, "set_team_nc_creds", _team)
    monkeypatch.setattr(nc, "set_acting_channel", _acting)
    monkeypatch.setattr(nc, "clear_request_nc_creds", _clear_nc)
    monkeypatch.setattr(nc, "clear_acting_channel", _clear_ch)

    # manager channel helper — force True for stuart-day
    import src.graphs.channels as chans
    monkeypatch.setattr(chans, "_is_manager_channel", lambda c: "stuart" in (c or ""))
    monkeypatch.setattr(chans, "_team_agent_key", lambda c: None)

    out = await appr.execute_approved_tool("ab12cd34")

    assert out.get("success") is True, out
    assert calls["team_nc"] == 1
    assert calls["acting"] == ["stuart-day"]
    assert calls["ainvoke"] == 1
    assert calls["bound_user"] == "coveadmin"
    assert calls["cleared_nc"] == 1
    assert calls["cleared_ch"] == 1
