"""Human manage grants for another Presence's Bookkeeping.

Owner grants a Cove member manage on /books. Agent Nextcloud tools stay
denied on the Bookkeeping tree. Havens are out of this slice.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.dashboard import books as bk


def parse_books_presence_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return ""


def requested_books_owner(actor_id: str, requested_owner_id: Any) -> str:
    """Which Presence's Bookkeeping tree to open. Empty request = actor."""
    actor = parse_books_presence_id(actor_id)
    want = parse_books_presence_id(requested_owner_id)
    if not actor:
        return want
    if not want or want == actor:
        return actor
    return want


def books_grant_allowed(actor_id: str, owner_id: str, has_grant: bool) -> bool:
    actor = parse_books_presence_id(actor_id)
    owner = parse_books_presence_id(owner_id)
    if not actor or not owner:
        return False
    if actor == owner:
        return True
    return bool(has_grant)


def grantable_cove_role(role: str) -> bool:
    return str(role or "").strip().lower() in ("admin", "member")


def presence_books_label(row: dict | None) -> str:
    if not isinstance(row, dict):
        return "Books"
    name = str(row.get("display_name") or row.get("username") or "").strip()
    return name or "Books"


def parse_requested_presence(request, body: dict | None = None) -> str:
    raw = ""
    if isinstance(body, dict):
        raw = str(body.get("presence") or body.get("presence_id") or "").strip()
    if not raw and request is not None:
        raw = (
            request.query_params.get("presence")
            or request.query_params.get("presence_id")
            or request.headers.get("x-books-presence")
            or ""
        ).strip()
    return parse_books_presence_id(raw)


# Re-export so bookkeeping can keep one import surface for path helpers.
clean_books_path = bk.clean_books_path
