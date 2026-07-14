# Install-pass: multi-mode presence with empty agent_identity still scopes
# personal chat / brain-acknowledge to the presence id (not container primary).
import types

import pytest


class _Req:
    def __init__(self, presence=None):
        self.state = types.SimpleNamespace(presence=presence)


@pytest.mark.asyncio
async def test_personal_agent_id_uses_presence_when_identity_empty(monkeypatch):
    monkeypatch.setenv("COVE_MODE", "multi")
    from src.dashboard.routes import chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_primary_agent_id", lambda: "PRIMARY")

    presence = {
        "id": "uuid-coadmin-1",
        "username": "jude",
        "agent_name": "Jude",
        "agent_identity": {},  # empty mid-onboarding
        "cove_role": "member",
    }
    aid = await chat_mod._personal_agent_id(_Req(presence))
    assert aid == "uuid-coadmin-1"


@pytest.mark.asyncio
async def test_personal_agent_id_single_mode_primary(monkeypatch):
    monkeypatch.setenv("COVE_MODE", "single")
    from src.dashboard.routes import chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_primary_agent_id", lambda: "PRIMARY")
    aid = await chat_mod._personal_agent_id(_Req({"id": "x", "agent_identity": {"n": 1}}))
    assert aid == "PRIMARY"
