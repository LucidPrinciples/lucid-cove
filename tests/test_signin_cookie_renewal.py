"""SIGNIN1 — re-issue presence_token when the server session rolls.

Daily use already slides auth_sessions.expires_at. The browser cookie was only
set on /p/ (and sign-in), so a 90-day Max-Age still signed a daily user out.
These tests lock the cookie helper, the once-a-day roll flag, and middleware
renewal — no live DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from starlette.requests import Request
from starlette.responses import Response

ROOT = Path(__file__).resolve().parents[1]
PRESENCE_SRC = (ROOT / "src/dashboard/routes/presence.py").read_text(encoding="utf-8")
APP_SRC = (ROOT / "src/dashboard/app.py").read_text(encoding="utf-8")
ONBOARD_SRC = (ROOT / "src/dashboard/routes/onboarding.py").read_text(encoding="utf-8")
INVITE_SRC = (ROOT / "src/dashboard/routes/presence_invite.py").read_text(encoding="utf-8")


def test_set_presence_cookie_is_the_only_presence_token_issuer():
    """Door, onboarding, invite, and middleware must share one helper."""
    assert "def set_presence_cookie(" in PRESENCE_SRC
    assert "set_presence_cookie(response, request, token)" in PRESENCE_SRC
    assert "set_presence_cookie(response, request, raw)" in ONBOARD_SRC
    assert "set_presence_cookie(resp, request, raw)" in INVITE_SRC
    assert "maybe_renew_presence_cookie(request, response)" in APP_SRC
    # Callers must not set the cookie inline; only the helper does.
    assert "response.set_cookie(" not in ONBOARD_SRC.split("set_presence_cookie(response, request, raw)")[0][-400:]
    assert "resp.set_cookie(\n        key=COOKIE_NAME" not in INVITE_SRC
    door_tail = PRESENCE_SRC.split("response = RedirectResponse(redirect_to)")[-1]
    assert "set_presence_cookie(response, request, token)" in door_tail
    assert "response.set_cookie(" not in door_tail.split("return response")[0]


def test_middleware_renews_only_on_session_rolled_flag():
    assert "maybe_renew_presence_cookie" in APP_SRC
    assert "def maybe_renew_presence_cookie(" in PRESENCE_SRC
    assert 'getattr(request.state, "session_rolled", False)' in PRESENCE_SRC


def test_lookup_selects_expires_at_for_the_roll_flag():
    assert "s.expires_at AS session_expires_at" in PRESENCE_SRC
    assert "mark_session_rolled(request, row.get(\"session_expires_at\"))" in PRESENCE_SRC


def test_session_due_for_roll_at_88_days_not_90():
    import src.dashboard.routes.presence as p

    now = datetime.now(timezone.utc)
    assert p._session_due_for_roll(now + timedelta(days=88)) is True
    assert p._session_due_for_roll(now + timedelta(days=90)) is False
    assert p._session_due_for_roll(None) is False


def test_mark_session_rolled_is_sticky():
    import src.dashboard.routes.presence as p

    now = datetime.now(timezone.utc)
    req = SimpleNamespace(state=SimpleNamespace())
    p.mark_session_rolled(req, now + timedelta(days=88))
    assert req.state.session_rolled is True
    # A later lookup after the CASE has already slid must not clear the flag.
    p.mark_session_rolled(req, now + timedelta(days=90))
    assert req.state.session_rolled is True


def test_set_presence_cookie_matches_door_flags():
    import src.dashboard.routes.presence as p

    req = MagicMock(spec=Request)
    req.headers = {"host": "localhost:8100"}
    req.url.scheme = "http"
    resp = Response()
    p.set_presence_cookie(resp, req, "raw-token-value")
    header = resp.headers.get("set-cookie") or ""
    assert "presence_token=raw-token-value" in header
    assert "Max-Age=7776000" in header  # 90 days
    assert "HttpOnly" in header
    assert "Path=/" in header
    assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()
    assert "Secure" not in header  # plain HTTP must still work on mesh


def test_set_presence_cookie_secure_on_https():
    import src.dashboard.routes.presence as p

    req = MagicMock(spec=Request)
    req.headers = {"x-forwarded-proto": "https", "host": "example.lucidcove.org"}
    req.url.scheme = "http"  # proxy terminated
    resp = Response()
    p.set_presence_cookie(resp, req, "tok")
    header = resp.headers.get("set-cookie") or ""
    assert "Secure" in header


def test_maybe_renew_sets_cookie_when_session_rolled():
    import src.dashboard.routes.presence as p

    req = MagicMock(spec=Request)
    req.headers = {"host": "localhost"}
    req.url.scheme = "http"
    req.cookies = {"presence_token": "raw-token-value"}
    req.state = SimpleNamespace(session_rolled=True)
    resp = Response()
    p.maybe_renew_presence_cookie(req, resp)
    header = resp.headers.get("set-cookie") or ""
    assert "presence_token=raw-token-value" in header
    assert "Max-Age=7776000" in header


def test_maybe_renew_skips_same_day():
    import src.dashboard.routes.presence as p

    req = MagicMock(spec=Request)
    req.headers = {"host": "localhost"}
    req.url.scheme = "http"
    req.cookies = {"presence_token": "raw-token-value"}
    req.state = SimpleNamespace(session_rolled=False)
    resp = Response()
    p.maybe_renew_presence_cookie(req, resp)
    assert not (resp.headers.get("set-cookie") or "")
