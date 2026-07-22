"""batch8 #7b / CF-107 / #CF-113 — provisioner folder seeds."""
from src.dashboard.routes.nextcloud import (
    PRESENCE_FOLDERS,
    STEWARD_SHARED_FOLDERS,
    STEWARD_COVE_SHARED_FOLDER,
    STEWARD_KB_FOLDER,
    _is_operator_presence,
    _COVE_SHARED_RW_PERMS,
)


def test_flows_and_actions_no_longer_seeded():
    assert "AgentSkills/Flows" not in PRESENCE_FOLDERS
    assert "AgentSkills/Flows/Archive" not in PRESENCE_FOLDERS
    assert "AgentSkills/Actions" not in PRESENCE_FOLDERS
    assert "AgentSkills/Actions/Archive" not in PRESENCE_FOLDERS


def test_essentials_still_seeded_shared_stub_retired():
    # #CF-113: per-presence AgentSkills/Shared stubs retired
    assert "AgentSkills/Shared" not in PRESENCE_FOLDERS
    assert "AgentSkills/Context" in PRESENCE_FOLDERS
    assert "AgentSkills/Content" in PRESENCE_FOLDERS
    assert "AgentSkills/Sites" in PRESENCE_FOLDERS


def test_actions_and_shared_dropped_from_steward_shares():
    assert "AgentSkills/Actions" not in STEWARD_SHARED_FOLDERS
    assert "AgentSkills/Shared" not in STEWARD_SHARED_FOLDERS
    assert "AgentSkills/Content" in STEWARD_SHARED_FOLDERS
    assert "AgentSkills/Sites" in STEWARD_SHARED_FOLDERS


def test_cf113_cove_shared_constants():
    assert STEWARD_COVE_SHARED_FOLDER == "CoveShared"
    assert STEWARD_KB_FOLDER == "AgentSkills/Knowledge Base"
    # RW without re-share: 1+2+4+8
    assert _COVE_SHARED_RW_PERMS == 15


def test_operator_presence_gate():
    assert _is_operator_presence("member", "member") is True
    assert _is_operator_presence("steward", "admin") is True
    assert _is_operator_presence("member", "guest") is False
    assert _is_operator_presence("steward", "guest") is False
    assert _is_operator_presence("", "member") is True
