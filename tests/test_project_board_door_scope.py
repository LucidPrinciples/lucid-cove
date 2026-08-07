"""Project board scope is by MC door, not cove_role alone."""

from pathlib import Path
from unittest.mock import MagicMock

from src.dashboard.routes import projects as pr


ROOT = Path(__file__).resolve().parents[1]


def test_presence_filter_personal_requires_attach():
    sql, params = pr._presence_filter("pres-1", full_cove_board=False)
    assert "project_members" in sql
    assert params == ("pres-1", "pres-1")


def test_presence_filter_full_board_sees_all_cove():
    sql, params = pr._presence_filter("pres-1", full_cove_board=True)
    assert "project_members" not in sql
    assert "IS NULL" in sql
    assert params == ("pres-1",)


def test_full_cove_board_manager_door(monkeypatch):
    presence = {"id": "x", "username": "alice", "cove_role": "admin"}
    req = MagicMock()
    req.query_params = {}
    monkeypatch.setattr(pr, "COVE_MODE", "multi")
    monkeypatch.setattr(
        "src.dashboard.host_context.resolve_host_context",
        lambda host, cove: {"kind": "manager", "label": "stuart"},
    )
    monkeypatch.setattr(
        "src.dashboard.host_context.request_host",
        lambda r: "stuart.example.test",
    )
    monkeypatch.setattr(
        "src.config.load_cove_config",
        lambda: {
            "steward_channel": {"name": "stuart"},
            "merchant_channel": {"name": "mercer"},
        },
    )
    assert pr._full_cove_board(req, presence) is True


def test_full_cove_board_personal_handle_even_if_admin(monkeypatch):
    presence = {"id": "x", "username": "alice", "cove_role": "admin"}
    req = MagicMock()
    req.query_params = {}
    monkeypatch.setattr(pr, "COVE_MODE", "multi")
    monkeypatch.setattr(
        "src.dashboard.host_context.resolve_host_context",
        lambda host, cove: {"kind": "handle", "label": "alice"},
    )
    monkeypatch.setattr(
        "src.dashboard.host_context.request_host",
        lambda r: "alice.example.test",
    )
    monkeypatch.setattr(
        "src.config.load_cove_config",
        lambda: {
            "steward_channel": {"name": "stuart"},
            "merchant_channel": {"name": "mercer"},
        },
    )
    assert pr._full_cove_board(req, presence) is False


def test_full_cove_board_as_own_forces_personal(monkeypatch):
    presence = {"id": "x", "username": "alice", "cove_role": "admin"}
    req = MagicMock()
    req.query_params = {"as": "alice"}
    monkeypatch.setattr(pr, "COVE_MODE", "multi")
    monkeypatch.setattr(
        "src.dashboard.host_context.resolve_host_context",
        lambda host, cove: {"kind": "cove", "label": None},
    )
    monkeypatch.setattr(
        "src.dashboard.host_context.request_host",
        lambda r: "localhost",
    )
    monkeypatch.setattr(
        "src.config.load_cove_config",
        lambda: {
            "steward_channel": {"name": "stuart"},
            "merchant_channel": {"name": "mercer"},
        },
    )
    assert pr._full_cove_board(req, presence) is False


def test_full_cove_board_as_stuart_manager(monkeypatch):
    presence = {"id": "x", "username": "alice", "cove_role": "admin"}
    req = MagicMock()
    req.query_params = {"as": "stuart"}
    monkeypatch.setattr(pr, "COVE_MODE", "multi")
    monkeypatch.setattr(
        "src.dashboard.host_context.resolve_host_context",
        lambda host, cove: {"kind": "cove", "label": None},
    )
    monkeypatch.setattr(
        "src.dashboard.host_context.request_host",
        lambda r: "localhost",
    )
    monkeypatch.setattr(
        "src.config.load_cove_config",
        lambda: {
            "steward_channel": {"name": "stuart"},
            "merchant_channel": {"name": "mercer"},
        },
    )
    assert pr._full_cove_board(req, presence) is True


def test_src_documents_door_scope_rule():
    src = (ROOT / "src/dashboard/routes/projects.py").read_text()
    assert "def _full_cove_board" in src
    assert 'kind == "manager"' in src
