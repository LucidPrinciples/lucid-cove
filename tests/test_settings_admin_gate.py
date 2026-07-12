"""AUDIT-F6 — cove-wide Settings mutations must be Admin-only in multi mode.

In multi mode OperatorAuthMiddleware admits ANY authenticated Presence to /api/*;
it does not distinguish Admin from Member. So each cove-wide write in settings.py
must self-gate for an Admin Presence. Single mode is mesh-trusted (no Presence) and
must still pass. These tests lock that contract for reload / team-models / cove /
features (PUT) and the github_pat branch of PATCH features.
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import src.dashboard.routes.settings as st  # noqa: E402


class _DummyRequest:
    """settings handlers only pass request through to get_current_presence
    (mocked below); patch_features additionally awaits request.json()."""
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


def _set_mode(monkeypatch, mode):
    monkeypatch.setattr(st, "env", lambda k, d=None: mode if k == "COVE_MODE" else d)


def _set_presence(monkeypatch, presence):
    async def _fake_get_current_presence(request):
        return presence
    # _is_admin_presence imports this symbol from the presence module at call time.
    import src.dashboard.routes.presence as pres
    monkeypatch.setattr(pres, "get_current_presence", _fake_get_current_presence)


def _status(result):
    return getattr(result, "status_code", 200)


# ── PUT /api/settings/features ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_features_member_denied_in_multi(monkeypatch):
    _set_mode(monkeypatch, "multi")
    _set_presence(monkeypatch, {"cove_role": "member"})
    monkeypatch.setattr(st, "save_cove_config", lambda *_a, **_k: True)

    result = await st.update_features(st.FeatureFlagsUpdate(tuning=False), _DummyRequest())
    assert _status(result) == 403


@pytest.mark.asyncio
async def test_features_admin_allowed_in_multi(monkeypatch):
    _set_mode(monkeypatch, "multi")
    _set_presence(monkeypatch, {"cove_role": "admin"})
    saved = {}
    monkeypatch.setattr(st, "save_cove_config", lambda changes: saved.update(changes) or True)

    result = await st.update_features(st.FeatureFlagsUpdate(tuning=False), _DummyRequest())
    assert _status(result) == 200
    assert result["ok"] is True
    assert saved == {"features": {"tuning": False}}


@pytest.mark.asyncio
async def test_features_single_mode_passes_without_presence(monkeypatch):
    _set_mode(monkeypatch, "single")
    _set_presence(monkeypatch, None)  # single mode has no Presence
    monkeypatch.setattr(st, "save_cove_config", lambda *_a, **_k: True)

    result = await st.update_features(st.FeatureFlagsUpdate(tuning=False), _DummyRequest())
    assert _status(result) == 200


# ── other cove-wide writes: member denied in multi ────────────────────────────

@pytest.mark.asyncio
async def test_reload_member_denied_in_multi(monkeypatch):
    _set_mode(monkeypatch, "multi")
    _set_presence(monkeypatch, {"cove_role": "member"})
    result = await st.reload_config(_DummyRequest())
    assert _status(result) == 403


@pytest.mark.asyncio
async def test_team_model_member_denied_in_multi(monkeypatch):
    _set_mode(monkeypatch, "multi")
    _set_presence(monkeypatch, {"cove_role": "member"})
    result = await st.update_team_model("stuart", st.TeamModelUpdate(primary="x"), _DummyRequest())
    assert _status(result) == 403


@pytest.mark.asyncio
async def test_cove_settings_member_denied_in_multi(monkeypatch):
    _set_mode(monkeypatch, "multi")
    _set_presence(monkeypatch, {"cove_role": "member"})
    result = await st.update_cove_settings(st.CoveSettingsUpdate(), _DummyRequest())
    assert _status(result) == 403


# ── PATCH features: github_pat is Cove-level → admin-only in multi ─────────────

@pytest.mark.asyncio
async def test_patch_github_pat_member_denied_in_multi(monkeypatch):
    _set_mode(monkeypatch, "multi")
    _set_presence(monkeypatch, {"cove_role": "member"})
    result = await st.patch_features(_DummyRequest({"github_pat": "ghp_evil"}))
    assert _status(result) == 403
