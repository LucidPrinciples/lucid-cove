# batch-9 #5 (D1): the operator's intro-yourself bio is fed into the SPARK context so the
# waking agent knows who it serves from minute one.
# batch-10 #6 extends this: the wizard now SAVES the bio to presence_profiles.bio, and the
# wake reads it from the profile as a fallback when the setup chain didn't thread it through.
import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.flow_cove as fc  # noqa: E402


def test_operator_bio_enters_wake_context_first():
    ctx = fc._wake_context({
        "operator_bio": "I'm Jamie, a night-shift nurse who paints on weekends.",
        "reflection": "somewhere calm to think",
        "qualities": ["steady"],
    })
    assert "How they introduced themselves" in ctx
    assert "night-shift nurse" in ctx
    # bio leads (minute-one identity), reflection still present
    assert ctx.index("introduced themselves") < ctx.index("reached for")


def test_bio_alias_accepted():
    ctx = fc._wake_context({"bio": "Just me."})
    assert "Just me." in ctx


def test_no_bio_is_clean():
    ctx = fc._wake_context({"reflection": "a quiet place"})
    assert "introduced themselves" not in ctx
    assert "reached for" in ctx


# ── batch-10 #6: the profile-bio fallback the wake uses when operator_bio isn't in body ──

class _Conn:
    def __init__(self, bio):
        self._bio = bio

    async def execute(self, sql, params=None):
        self._params = params
        return self

    async def fetchone(self):
        return None if self._bio is None else {"bio": self._bio}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch(monkeypatch, presence, bio):
    async def _gcp(_req):
        return presence
    monkeypatch.setattr("src.dashboard.routes.presence.get_current_presence", _gcp)
    import src.memory.database as db
    monkeypatch.setattr(db, "get_db", lambda: _Conn(bio))


@pytest.mark.asyncio
async def test_profile_fallback_returns_saved_bio(monkeypatch):
    _patch(monkeypatch, {"username": "Dennis"}, "I run a small farm.")
    assert await fc._operator_bio_from_profile(object()) == "I run a small farm."


@pytest.mark.asyncio
async def test_profile_fallback_empty_when_no_operator(monkeypatch):
    _patch(monkeypatch, None, "ignored")
    assert await fc._operator_bio_from_profile(object()) == ""


@pytest.mark.asyncio
async def test_profile_fallback_empty_when_no_bio_row(monkeypatch):
    _patch(monkeypatch, {"username": "Dennis"}, None)
    assert await fc._operator_bio_from_profile(object()) == ""


@pytest.mark.asyncio
async def test_profile_fallback_never_raises(monkeypatch):
    async def _boom(_req):
        raise RuntimeError("db down")
    monkeypatch.setattr("src.dashboard.routes.presence.get_current_presence", _boom)
    assert await fc._operator_bio_from_profile(object()) == ""
