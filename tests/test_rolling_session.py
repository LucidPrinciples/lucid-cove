"""
batch-10 #3 — rolling MC session expiry.

Locked design (2026-07-04): any authenticated visit slides the 90-day window forward,
so a device in regular use never expires; the 90d clock only kills ABANDONED devices.
The slide rides the existing throttled last_used touch and is itself throttled to at
most once/day per session (the expires_at CASE only fires when it has dropped below
NOW()+89d), so there is no per-request write. Sign-in links stay short-lived/one-time.

_touch_session runs a single atomic UPDATE against Postgres (INTERVAL arithmetic), so
we assert on the SQL it issues via a fake connection rather than standing up a DB.
"""

import sys
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.presence as p  # noqa: E402


class _FakeConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return None


def _norm(sql):
    return " ".join(sql.split())


@pytest.mark.asyncio
async def test_touch_extends_expiry_no_device():
    conn = _FakeConn()
    await p._touch_session(conn, "abc123")
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    # Rolling extend present, throttled to once/day via the 89-day guard.
    assert "expires_at = CASE WHEN expires_at < NOW() + INTERVAL '89 days'" in sql
    assert "THEN NOW() + INTERVAL '90 days' ELSE expires_at END" in sql
    # Still throttled per-request by the 60s last_used guard (no write storm).
    assert "last_used < NOW() - INTERVAL '60 seconds'" in sql
    assert params == ("abc123",)


@pytest.mark.asyncio
async def test_touch_extends_expiry_with_real_device():
    conn = _FakeConn()
    await p._touch_session(conn, "abc123", "iPhone")
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "expires_at = CASE WHEN expires_at < NOW() + INTERVAL '89 days'" in sql
    # The real-device branch also relabels placeholders in the same write.
    assert "device_label = CASE WHEN device_label IS NULL" in sql
    assert params == ("iPhone", "abc123")


@pytest.mark.asyncio
async def test_touch_never_raises_on_db_error():
    class _Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("deadlock")

    # Must swallow — the auth row is already fetched; a touch failure can't break auth.
    await p._touch_session(_Boom(), "abc123")


def test_placeholder_devices_do_not_relabel():
    # A placeholder device_label must NOT be treated as a real device (else it would
    # overwrite a good label). Sanity-check the helper the branch depends on.
    for ph in ("", "pending", "regenerated", "migrated", "unknown", "device"):
        assert p._is_placeholder_label(ph) is True
    assert p._is_placeholder_label("iPhone") is False
