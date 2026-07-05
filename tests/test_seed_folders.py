"""batch8 #7b / CF-107 — provisioner stops seeding dead dirs (Flows/Actions),
keeps Shared/Context/Content."""
from src.dashboard.routes.nextcloud import PRESENCE_FOLDERS, STEWARD_SHARED_FOLDERS


def test_flows_and_actions_no_longer_seeded():
    assert "AgentSkills/Flows" not in PRESENCE_FOLDERS
    assert "AgentSkills/Flows/Archive" not in PRESENCE_FOLDERS
    assert "AgentSkills/Actions" not in PRESENCE_FOLDERS
    assert "AgentSkills/Actions/Archive" not in PRESENCE_FOLDERS


def test_shared_and_essentials_still_seeded():
    # Shared stays (future cross-Presence share, CF-113); the working dirs stay.
    assert "AgentSkills/Shared" in PRESENCE_FOLDERS
    assert "AgentSkills/Context" in PRESENCE_FOLDERS
    assert "AgentSkills/Content" in PRESENCE_FOLDERS
    assert "AgentSkills/Sites" in PRESENCE_FOLDERS


def test_actions_dropped_from_steward_shares_shared_kept():
    assert "AgentSkills/Actions" not in STEWARD_SHARED_FOLDERS
    assert "AgentSkills/Shared" in STEWARD_SHARED_FOLDERS
    assert "AgentSkills/Content" in STEWARD_SHARED_FOLDERS
