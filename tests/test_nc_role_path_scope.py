"""Phase 2 — role-scoped NC path access in the shared admin space.

Enforcement lives in src/tools/nextcloud_tools.py (tool layer). These tests
cover the pure decision surface: ContextVar set/clear, role resolution,
rw/ro matrix, cross-Team deny, fail-safe, and presence (unscoped) passthrough.
No live WebDAV.
"""
from __future__ import annotations

import pytest

from src.tools import nextcloud_tools as nc


@pytest.fixture(autouse=True)
def _clear_acting_channel():
    """Every test starts with no acting channel (presence/unbound default)."""
    tok = nc.set_acting_channel(None)
    yield
    nc.clear_acting_channel(tok)


@pytest.fixture
def _stub_channel_roles(monkeypatch):
    """Map channel strings to roles without loading full agent config."""

    def is_steward(ch: str) -> bool:
        return ch == "stuart" or ch.startswith("stuart-")

    def is_merchant(ch: str) -> bool:
        return ch == "mercer" or ch.startswith("mercer-")

    def team_key(ch: str):
        if "-" not in ch:
            return None
        key, _, sub = ch.rpartition("-")
        if sub not in ("day", "deep") or not key:
            return None
        if is_steward(ch) or is_merchant(ch):
            return None
        if key in nc._AGENT_ROLE:
            return key
        return None

    monkeypatch.setattr("src.graphs.channels._is_steward_channel", is_steward, raising=False)
    monkeypatch.setattr("src.graphs.channels._is_merchant_channel", is_merchant, raising=False)
    monkeypatch.setattr("src.graphs.channels._team_agent_key", team_key, raising=False)
    # resolve_acting_role imports from graphs.channels first
    import src.graphs.channels as chmod
    monkeypatch.setattr(chmod, "_is_steward_channel", is_steward)
    monkeypatch.setattr(chmod, "_is_merchant_channel", is_merchant)
    monkeypatch.setattr(chmod, "_team_agent_key", team_key)


def _act(channel: str):
    return nc.set_acting_channel(channel)


# ── ContextVar set / clear ──────────────────────────────────────────────────


def test_acting_channel_set_and_clear():
    assert nc.get_acting_channel() is None
    tok = nc.set_acting_channel("archimedes-day")
    assert nc.get_acting_channel() == "archimedes-day"
    nc.clear_acting_channel(tok)
    assert nc.get_acting_channel() is None


def test_clear_acting_channel_tolerates_none():
    nc.clear_acting_channel(None)  # must not raise


# ── Role resolution ─────────────────────────────────────────────────────────


def test_resolve_steward(_stub_channel_roles):
    _act("stuart-day")
    assert nc.resolve_acting_role() == ("steward", "stuart")


def test_resolve_merchant(_stub_channel_roles):
    _act("mercer-deep")
    assert nc.resolve_acting_role() == ("merchant", "mercer")


def test_resolve_builder(_stub_channel_roles):
    _act("archimedes-day")
    assert nc.resolve_acting_role() == ("builder", "archimedes")


def test_resolve_presence_unscoped(_stub_channel_roles):
    _act("day")  # presence channel — not team
    assert nc.resolve_acting_role() == (None, None)


def test_resolve_unset_channel_unscoped():
    assert nc.resolve_acting_role() == (None, None)


# ── Steward unrestricted ────────────────────────────────────────────────────


def test_steward_write_anywhere_under_admin(_stub_channel_roles):
    _act("stuart-day")
    for path in (
        "AgentSkills/Team/archimedes/notes.md",
        "AgentSkills/Reports/q1.md",
        "AgentSkills/Knowledge Base/x.md",
        "Context/brain.md",
        "Inbox/drop.md",
        "AgentSkills/Sites/foo.html",
    ):
        assert nc.check_nc_path_access(path, write=True) is None, path


# ── Per-role RW / RO ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "channel,ok_write,deny_write,ok_read",
    [
        (
            "archimedes-day",
            [
                "AgentSkills/Team/archimedes/scratch.md",
                "AgentSkills/Sites/landing.html",
            ],
            [
                "AgentSkills/Reports/rev.md",
                "AgentSkills/Content/post.md",
                "AgentSkills/Ops/runbook.md",
                "AgentSkills/Knowledge Base/canon.md",
                "AgentSkills/Team/gabe/x.md",
                "Context/secret.md",
                "Inbox/jules.md",
            ],
            [
                "AgentSkills/Knowledge Base/canon.md",
                "AgentSkills/Reports/rev.md",
                "AgentSkills/Team/archimedes/scratch.md",
            ],
        ),
        (
            "mercer-day",
            [
                "AgentSkills/Team/mercer/notes.md",
                "AgentSkills/Reports/sales.md",
            ],
            [
                "AgentSkills/Sites/x.html",
                "AgentSkills/Content/x.md",
                "AgentSkills/Ops/x.md",
                "AgentSkills/Team/archimedes/x.md",
                "AgentSkills/Knowledge Base/x.md",
            ],
            [
                "AgentSkills/Knowledge Base/x.md",
                "AgentSkills/Content/x.md",
                "AgentSkills/Sites/x.html",
            ],
        ),
        (
            "gabe-day",
            [
                "AgentSkills/Team/gabe/research.md",
            ],
            [
                "AgentSkills/Content/dump.md",  # strict scout: Content is RO
                "AgentSkills/Sites/x.html",
                "AgentSkills/Reports/x.md",
                "AgentSkills/Team/julian/x.md",
            ],
            [
                "AgentSkills/Content/dump.md",
                "AgentSkills/Knowledge Base/x.md",
            ],
        ),
        (
            "vera-day",
            ["AgentSkills/Team/vera/review.md"],
            [
                "OperatorShared/x.md",
                "AgentSkills/Reports/x.md",
                "AgentSkills/Team/archimedes/x.md",
            ],
            [
                "AgentSkills/Reports/x.md",
                "AgentSkills/Knowledge Base/x.md",
            ],
        ),
        (
            "soren-day",
            ["AgentSkills/Team/soren/obs.md"],
            [
                "OperatorShared/x.md",
                "AgentSkills/Ops/x.md",
                "AgentSkills/Content/x.md",
            ],
            [
                "AgentSkills/Ops/x.md",
                "AgentSkills/Knowledge Base/x.md",
            ],
        ),
        (
            "julian-day",
            [
                "AgentSkills/Team/julian/draft.md",
                "AgentSkills/Content/post.md",
            ],
            [
                "AgentSkills/Reports/x.md",  # Reports RO for scribe
                "AgentSkills/Sites/x.html",
                "AgentSkills/Ops/x.md",
            ],
            ["AgentSkills/Reports/x.md", "AgentSkills/Knowledge Base/x.md"],
        ),
        (
            "ezra-day",
            [
                "AgentSkills/Team/ezra/x.md",
                "AgentSkills/Ops/runbook.md",
            ],
            [
                "AgentSkills/Reports/x.md",
                "AgentSkills/Sites/x.html",
                "AgentSkills/Content/x.md",
            ],
            ["AgentSkills/Knowledge Base/x.md", "AgentSkills/Reports/x.md"],
        ),
    ],
)
def test_role_rw_ro_matrix(channel, ok_write, deny_write, ok_read, _stub_channel_roles):
    _act(channel)
    for path in ok_write:
        err = nc.check_nc_path_access(path, write=True)
        assert err is None, f"{channel} should WRITE {path}: {err}"
    for path in deny_write:
        err = nc.check_nc_path_access(path, write=True)
        assert err is not None, f"{channel} must NOT write {path}"
        assert "Access denied" in err
    for path in ok_read:
        err = nc.check_nc_path_access(path, write=False)
        assert err is None, f"{channel} should READ {path}: {err}"


# ── Cross-Team write blocked ────────────────────────────────────────────────


def test_cross_team_write_blocked(_stub_channel_roles):
    _act("archimedes-day")
    err = nc.check_nc_path_access("AgentSkills/Team/gabe/stolen.md", write=True)
    assert err and "Team/gabe" in err


def test_own_team_write_allowed(_stub_channel_roles):
    _act("archimedes-day")
    assert nc.check_nc_path_access("AgentSkills/Team/archimedes/ok.md", write=True) is None


# ── Explicit denials ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "Context/brain.md",
        "Inbox/jules.md",
        "AgentSkills/Knowledge Base/canon.md",
        "Business Docs/outside.md",  # outside AgentSkills
    ],
)
def test_hard_denials_for_non_steward(path, _stub_channel_roles):
    _act("archimedes-day")
    err = nc.check_nc_path_access(path, write=True)
    assert err and "Access denied" in err


# ── Fail-safe for unresolved role ───────────────────────────────────────────


def test_unknown_role_failsafe_team_only(monkeypatch, _stub_channel_roles):
    # Force resolve to an unknown role with a known agent id
    monkeypatch.setattr(
        nc, "resolve_acting_role", lambda: ("unknown-role", "mystery")
    )
    # write own team ok
    assert nc.check_nc_path_access("AgentSkills/Team/mystery/x.md", write=True) is None
    # write Shared stub / OperatorShared denied for unknown role
    err = nc.check_nc_path_access("AgentSkills/Shared/x.md", write=True)
    assert err and "Access denied" in err
    err = nc.check_nc_path_access("OperatorShared/x.md", write=True)
    assert err and "Access denied" in err
    # read KB ok (fail-safe includes RO Knowledge Base)
    assert nc.check_nc_path_access("AgentSkills/Knowledge Base/x.md", write=False) is None


# ── Presence space unscathed ────────────────────────────────────────────────


def test_presence_unscoped_writes_anything(_stub_channel_roles):
    # No team channel bound → no scoping
    assert nc.get_acting_channel() is None
    for path in (
        "Inbox/note.md",
        "Context/x.md",
        "AgentSkills/Team/anyone/x.md",
        "random/path.md",
    ):
        assert nc.check_nc_path_access(path, write=True) is None


def test_presence_channel_unscoped(_stub_channel_roles):
    _act("day")
    assert nc.check_nc_path_access("Inbox/note.md", write=True) is None


# ── Path normalization ──────────────────────────────────────────────────────


def test_leading_slash_and_agentskills_prefix(_stub_channel_roles):
    _act("archimedes-day")
    assert nc.check_nc_path_access("/AgentSkills/Sites/x.html", write=True) is None
    assert nc.check_nc_path_access("AgentSkills/Sites/x.html", write=True) is None


# ── Tool entry points return denial (no WebDAV) ─────────────────────────────


@pytest.mark.asyncio
async def test_upload_tool_returns_denial(_stub_channel_roles):
    _act("vera-day")
    # Invoke the underlying coroutine (LangChain tool wrapper)
    fn = nc.nextcloud_upload
    coro = fn.coroutine if hasattr(fn, "coroutine") else fn
    # LangChain StructuredTool: .ainvoke
    if hasattr(fn, "ainvoke"):
        result = await fn.ainvoke(
            {"path": "AgentSkills/Reports/x.md", "content": "nope"}
        )
    else:
        result = await coro("AgentSkills/Reports/x.md", "nope")
    assert "Access denied" in result


@pytest.mark.asyncio
async def test_mkdir_tool_returns_denial(_stub_channel_roles):
    _act("soren-day")
    fn = nc.nextcloud_mkdir
    if hasattr(fn, "ainvoke"):
        result = await fn.ainvoke({"path": "OperatorShared/x"})
    else:
        result = await fn.coroutine("OperatorShared/x")
    assert "Access denied" in result


@pytest.mark.asyncio
async def test_move_tool_returns_denial_on_src(_stub_channel_roles):
    """Vera cannot MOVE out of Reports (read-only / out of rw)."""
    _act("vera-day")
    fn = nc.nextcloud_move
    payload = {
        "src": "AgentSkills/Reports/x.md",
        "dest": "AgentSkills/Team/vera/x.md",
    }
    if hasattr(fn, "ainvoke"):
        result = await fn.ainvoke(payload)
    else:
        result = await fn.coroutine(**payload)
    assert "Access denied" in result


@pytest.mark.asyncio
async def test_move_tool_returns_denial_on_dest(_stub_channel_roles):
    """Builder cannot MOVE into another agent's Team folder."""
    _act("archimedes-day")
    fn = nc.nextcloud_move
    payload = {
        "src": "AgentSkills/Team/archimedes/x.md",
        "dest": "AgentSkills/Team/vera/x.md",
    }
    if hasattr(fn, "ainvoke"):
        result = await fn.ainvoke(payload)
    else:
        result = await fn.coroutine(**payload)
    assert "Access denied" in result


@pytest.mark.asyncio
async def test_delete_tool_returns_denial(_stub_channel_roles):
    _act("soren-day")
    fn = nc.nextcloud_delete
    if hasattr(fn, "ainvoke"):
        result = await fn.ainvoke({"path": "OperatorShared/x.md"})
    else:
        result = await fn.coroutine("OperatorShared/x.md")
    assert "Access denied" in result


def test_move_and_delete_registered_and_tiered():
    """Registry + approval tiers: move is NOTIFY, delete is APPROVE."""
    from src.tools.approval import Tier

    assert nc.nextcloud_move in nc.ALL_NEXTCLOUD_TOOLS
    assert nc.nextcloud_delete in nc.ALL_NEXTCLOUD_TOOLS
    # Decorators set _approval_tier on the underlying function; StructuredTool
    # keeps it on .coroutine or on the tool object depending on langchain version.
    move_fn = getattr(nc.nextcloud_move, "coroutine", None) or nc.nextcloud_move
    del_fn = getattr(nc.nextcloud_delete, "coroutine", None) or nc.nextcloud_delete
    move_tier = getattr(move_fn, "_approval_tier", None) or getattr(
        nc.nextcloud_move, "_approval_tier", None
    )
    del_tier = getattr(del_fn, "_approval_tier", None) or getattr(
        nc.nextcloud_delete, "_approval_tier", None
    )
    assert move_tier == Tier.NOTIFY
    assert del_tier == Tier.APPROVE


# ── Chokepoint wiring present in source ─────────────────────────────────────


def test_chat_chokepoint_sets_and_clears_acting_channel():
    src = open("src/dashboard/routes/chat.py").read()
    assert "set_acting_channel" in src
    assert "clear_acting_channel" in src
    assert "_ch_tok" in src


def test_delegation_chokepoint_sets_acting_channel():
    src = open("src/tools/delegation_tools.py").read()
    assert "set_acting_channel" in src
    assert "set_team_nc_creds" in src


# ── Cove override merge ─────────────────────────────────────────────────────


def test_cove_override_extends_role(monkeypatch, _stub_channel_roles):
    def fake_cove():
        return {
            "nc_path_scopes": {
                "builder": {
                    "rw": ["Team/archimedes/", "Sites/", "Shared/", "Content/"],
                    "ro": ["Knowledge Base/"],
                }
            }
        }

    monkeypatch.setattr("src.config.load_cove_config", fake_cove)
    _act("archimedes-day")
    # Content now RW via override
    assert nc.check_nc_path_access("AgentSkills/Content/x.md", write=True) is None
