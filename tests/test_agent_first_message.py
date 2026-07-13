"""#1626 — /api/agent/first-message exposes presence wake message for set-address UI."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from src.dashboard.routes.core import agent_first_message


class _Req:
    pass


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_public_app_returns_empty():
    with patch("src.dashboard.routes.core._is_public_app", return_value=True):
        out = _run(agent_first_message(_Req()))
    assert out == {"name": "", "first_message": ""}


def test_multi_presence_returns_identity_first_message():
    account = {
        "agent_name": "Holden",
        "agent_identity": {
            "agent_name": "Holden",
            "first_message": "Hey — I'm here. Want me to walk you through where everything is?",
        },
    }
    with patch("src.dashboard.routes.core._is_public_app", return_value=False), \
         patch("src.dashboard.routes.core.env", side_effect=lambda k, d=None: "multi" if k == "COVE_MODE" else d), \
         patch("src.dashboard.routes.presence.get_current_presence", new=AsyncMock(return_value=account)):
        out = _run(agent_first_message(_Req()))
    assert out["name"] == "Holden"
    assert "I'm here" in out["first_message"]


def test_multi_no_presence_falls_back_to_instance_name():
    with patch("src.dashboard.routes.core._is_public_app", return_value=False), \
         patch("src.dashboard.routes.core.env", side_effect=lambda k, d=None: "multi" if k == "COVE_MODE" else d), \
         patch("src.dashboard.routes.presence.get_current_presence", new=AsyncMock(return_value=None)), \
         patch("src.dashboard.routes.core.get_instance", return_value={"name": "Stuart"}), \
         patch("src.dashboard.routes.core.get_primary_agent_id", return_value="stuart"):
        out = _run(agent_first_message(_Req()))
    assert out["name"] == "Stuart"
    assert out["first_message"] == ""


def test_empty_first_message_still_returns_name():
    account = {"agent_name": "Atlas", "agent_identity": {"first_message": "  "}}
    with patch("src.dashboard.routes.core._is_public_app", return_value=False), \
         patch("src.dashboard.routes.core.env", side_effect=lambda k, d=None: "multi" if k == "COVE_MODE" else d), \
         patch("src.dashboard.routes.presence.get_current_presence", new=AsyncMock(return_value=account)):
        out = _run(agent_first_message(_Req()))
    assert out["name"] == "Atlas"
    assert out["first_message"] == ""
