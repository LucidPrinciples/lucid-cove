"""A failed sign-in email must not hand the link back on the public hub.

SIGNIN_LINK_IN_RESPONSE is off by default. It is ignored when Host or
X-Forwarded-Host names app.lucidtuner.com. A self-host Cove can turn it on.
"""
import logging
import pathlib
import sys
import types

import pytest
from fastapi import HTTPException

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import src.dashboard.routes.account as account  # noqa: E402
import src.dashboard.routes.email as email  # noqa: E402
import src.dashboard.routes.presence as presence  # noqa: E402
import src.dashboard.routes.registry as registry  # noqa: E402

_MAIL_FAIL = "We couldn't send the email. Try again in a minute."
_HUB = "app.lucidtuner.com"
_P620 = "p620.local"
_FLAG = "SIGNIN_LINK_IN_RESPONSE"


class _Result:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, email_row):
        self.email_row = email_row

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        if "display_name" in compact and "WHERE email" in compact:
            return _Result(self.email_row)
        return _Result(None)


class _URL:
    scheme = "https"


class _Request:
    def __init__(self, body, host, forwarded_host=None):
        self._body = body
        self.headers = {"host": host, "x-forwarded-proto": "https"}
        if forwarded_host is not None:
            self.headers["x-forwarded-host"] = forwarded_host
        self.url = _URL()

    async def json(self):
        return self._body


def _wire(monkeypatch, *, sent):
    async def _send(*_a, **_k):
        return sent

    async def _noop(*_a, **_k):
        return None

    conn = _Conn({
        "id": "11111111-1111-1111-1111-111111111111",
        "display_name": "Ada",
        "active": True,
        "username": "ada",
    })

    # The route imports get_db inside the handler. The test venv has no
    # psycopg, so give that import a connection double instead of the driver.
    db_mod = types.ModuleType("src.memory.database")
    db_mod.get_db = lambda: conn
    monkeypatch.setitem(sys.modules, "src.memory.database", db_mod)
    monkeypatch.setattr(account, "COVE_MODE", "multi")
    monkeypatch.setattr(email, "send_signin_link", _send)
    monkeypatch.setattr(email, "add_to_brevo_list", _noop)
    monkeypatch.setattr(presence, "_create_session", _noop)
    monkeypatch.setattr(registry, "reserve_handle", _noop)
    monkeypatch.delenv("MARKETPLACE_API_URL", raising=False)


def _create_req(host, forwarded_host=None):
    return _Request(
        {"email": "ada@example.com", "username": "ada", "display_name": "Ada"},
        host,
        forwarded_host,
    )


def _signin_req(host, forwarded_host=None):
    return _Request({"email": "ada@example.com"}, host, forwarded_host)


async def _call(fn, request):
    try:
        body = await fn(request)
    except HTTPException as exc:
        return exc.status_code, {"detail": exc.detail}
    return 200, body


def _assert_withheld(status, body):
    assert status == 503
    assert body["detail"] == _MAIL_FAIL
    assert "signin_link" not in body
    assert "/p/" not in str(body["detail"])


def _assert_log_has_no_link(caplog):
    text = caplog.text
    assert "withholding the link" in text
    assert "/p/" not in text
    assert "https://" not in text
    assert "ada@example.com" not in text


async def _both(host, forwarded_host=None):
    create_status, create_body = await _call(
        account.create_account, _create_req(host, forwarded_host)
    )
    signin_status, signin_body = await _call(
        account.signin, _signin_req(host, forwarded_host)
    )
    return (create_status, create_body), (signin_status, signin_body)


@pytest.mark.asyncio
async def test_mail_fail_hub_settings_return_no_link(monkeypatch, caplog):
    """Unset flag and the public host: neither response carries the link."""
    monkeypatch.delenv(_FLAG, raising=False)
    _wire(monkeypatch, sent=False)
    caplog.set_level(logging.WARNING)

    (create_status, create_body), (signin_status, signin_body) = await _both(_HUB)

    _assert_withheld(create_status, create_body)
    _assert_withheld(signin_status, signin_body)
    _assert_log_has_no_link(caplog)
    assert "signup=True" in caplog.text
    assert "signup=False" in caplog.text


@pytest.mark.asyncio
async def test_mail_fail_flag_off_self_host_returns_no_link(monkeypatch, caplog):
    """The flag is off by default, including on a Cove that is not the hub."""
    _wire(monkeypatch, sent=False)
    caplog.set_level(logging.WARNING)

    for value in (None, "", "false", "0", "off"):
        if value is None:
            monkeypatch.delenv(_FLAG, raising=False)
        else:
            monkeypatch.setenv(_FLAG, value)
        (create_status, create_body), (signin_status, signin_body) = await _both(_P620)
        _assert_withheld(create_status, create_body)
        _assert_withheld(signin_status, signin_body)
    _assert_log_has_no_link(caplog)


@pytest.mark.asyncio
async def test_public_hub_ignores_dev_flag(monkeypatch, caplog):
    """The flag cannot turn the link on for app.lucidtuner.com."""
    monkeypatch.setenv(_FLAG, "1")
    _wire(monkeypatch, sent=False)
    caplog.set_level(logging.WARNING)

    hosts = [
        (_HUB, None),
        (_P620, _HUB),
        (f"{_P620}, {_HUB}", None),
        (_HUB, _P620),
    ]
    for host, forwarded in hosts:
        (create_status, create_body), (signin_status, signin_body) = await _both(
            host, forwarded
        )
        _assert_withheld(create_status, create_body)
        _assert_withheld(signin_status, signin_body)
    _assert_log_has_no_link(caplog)


@pytest.mark.asyncio
async def test_dev_flag_returns_link(monkeypatch, caplog):
    """A self-host Cove with the flag on gets the link when mail fails."""
    monkeypatch.setenv(_FLAG, "true")
    _wire(monkeypatch, sent=False)
    caplog.set_level(logging.WARNING)

    (create_status, create_body), (signin_status, signin_body) = await _both(_P620)

    for status, body in ((create_status, create_body), (signin_status, signin_body)):
        assert status == 200
        assert body["email_sent"] is False
        assert body["signin_link"].startswith(f"https://{_P620}/p/")
        assert body["signin_link"] not in caplog.text
    assert "withholding the link" not in caplog.text


@pytest.mark.asyncio
async def test_mail_sent_omits_link(monkeypatch):
    """A sent email is not also returned, on the hub or with the flag on."""
    _wire(monkeypatch, sent=True)

    monkeypatch.delenv(_FLAG, raising=False)
    (create_status, create_body), (signin_status, signin_body) = await _both(_HUB)
    for status, body in ((create_status, create_body), (signin_status, signin_body)):
        assert status == 200
        assert body["email_sent"] is True
        assert "signin_link" not in body

    monkeypatch.setenv(_FLAG, "true")
    (create_status, create_body), (signin_status, signin_body) = await _both(_P620)
    for status, body in ((create_status, create_body), (signin_status, signin_body)):
        assert status == 200
        assert "signin_link" not in body
