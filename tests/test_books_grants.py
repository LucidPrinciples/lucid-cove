"""BOOKPRIV1 — human manage grants for another Presence's books."""

from pathlib import Path
from types import SimpleNamespace

from src.dashboard.books_access import (
    books_grant_allowed,
    grantable_cove_role,
    parse_books_presence_id,
    parse_requested_presence,
    requested_books_owner,
)


ROOT = Path(__file__).resolve().parents[1]
OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def test_migration_049_defines_books_grants():
    sql = (ROOT / "docker/migrations/049_books_grants.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS books_grants" in sql
    assert "owner_presence_id" in sql
    assert "grantee_presence_id" in sql
    base = (ROOT / "docker/init-base.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS books_grants" in base


def test_parse_and_own_books():
    assert parse_books_presence_id("not-a-uuid") == ""
    assert parse_books_presence_id(OWNER) == OWNER
    assert requested_books_owner(OWNER, "") == OWNER
    assert requested_books_owner(OWNER, OTHER) == OTHER
    assert books_grant_allowed(OWNER, OWNER, False) is True
    assert books_grant_allowed(OTHER, OWNER, False) is False
    assert books_grant_allowed(OTHER, OWNER, True) is True
    assert grantable_cove_role("member") is True
    assert grantable_cove_role("admin") is True
    assert grantable_cove_role("guest") is False


def test_parse_requested_presence_query_header_body():
    req = SimpleNamespace(
        query_params={"presence": OTHER},
        headers={},
    )
    assert parse_requested_presence(req) == OTHER
    req = SimpleNamespace(query_params={}, headers={"x-books-presence": OTHER})
    assert parse_requested_presence(req) == OTHER
    req = SimpleNamespace(query_params={}, headers={})
    assert parse_requested_presence(req, {"presence": OTHER}) == OTHER
    assert parse_requested_presence(req, {"presence": "nope"}) == ""


def test_books_routes_and_page_wire_grant():
    routes = (ROOT / "src/dashboard/routes/bookkeeping.py").read_text()
    assert "/api/books/access" in routes
    assert "/api/books/grants" in routes
    assert "No manage grant for those books" in routes
    page = (ROOT / "src/dashboard/static/books.html").read_text()
    assert "presenceSel" in page
    assert "X-Books-Presence" in page
    assert "Share books" in page


def test_agent_deny_still_covers_bookkeeping():
    from src.tools import nextcloud_tools as nc

    err = nc.check_nc_path_access("Bookkeeping/Organize/x.mapped.json", write=False)
    assert err and "Access denied" in err
