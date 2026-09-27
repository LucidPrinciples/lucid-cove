"""Mint a fresh sign-in link for the Cove owner (or a named handle).

Host-side recovery: whoever has shell on the machine running this Cove
can get back in without a stored bearer file. Prints the URL to stdout
only — never logs the token, never writes it to disk.

Run inside the app container:

    python -m src.cli.door
    python -m src.cli.door --handle somehandle
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

import secrets


def build_door_url(
    raw_token: str,
    *,
    domain: str = "",
    port: str = "8200",
) -> str:
    """Build a /p/{token} sign-in URL. Claimed domain wins over localhost."""
    token = (raw_token or "").strip()
    if not token:
        raise ValueError("empty token")
    domain = (domain or "").strip().rstrip("/")
    if domain:
        return f"https://{domain}/p/{token}"
    port = str(port or "8200").strip() or "8200"
    host = "localhost" if port in ("80", "443") else f"localhost:{port}"
    return f"http://{host}/p/{token}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli.door",
        description="Print a fresh sign-in link for this Cove (terminal only).",
    )
    parser.add_argument(
        "--handle",
        default="",
        help="Presence handle to mint for (default: founding owner/admin)",
    )
    return parser.parse_args(argv)


async def _resolve_account(conn, handle: str):
    handle = (handle or "").strip().lstrip("@")
    if handle:
        row = await (
            await conn.execute(
                """SELECT id, username FROM accounts
                   WHERE active = TRUE AND lower(username) = lower(%s)
                   LIMIT 1""",
                (handle,),
            )
        ).fetchone()
        return row
    row = await (
        await conn.execute(
            """SELECT id, username FROM accounts
               WHERE active = TRUE AND cove_role = 'admin'
               ORDER BY created_at ASC
               LIMIT 1""",
        )
    ).fetchone()
    return row


async def mint_host_recovery_link(handle: str = "") -> str:
    """Create a new auth session labeled host-recovery and return the sign-in URL.

    Existing sessions are left alone. The session cap still prunes the oldest
    when this account is already at the limit.
    """
    from src.config import load_cove_config
    from src.dashboard.routes.presence import _create_session, _hash_token
    from src.memory.database import get_db

    raw_token = secrets.token_urlsafe(32)
    hashed_token = _hash_token(raw_token)

    async with get_db() as conn:
        row = await _resolve_account(conn, handle)
        if not row:
            if handle:
                raise LookupError(f"No active account with handle @{handle.lstrip('@')}")
            raise LookupError("No owner/admin account in this Cove")
        account_id = row["id"]
        await conn.execute(
            "UPDATE accounts SET auth_token = %s, updated_at = NOW() WHERE id = %s",
            (hashed_token, uuid.UUID(str(account_id))),
        )
        await _create_session(conn, uuid.UUID(str(account_id)), hashed_token, "host-recovery")

    cove = load_cove_config() or {}
    domain = (cove.get("domain") or "").strip()
    port = os.environ.get("PORT") or "8200"
    return build_door_url(raw_token, domain=domain, port=port)


async def _run(handle: str) -> int:
    try:
        url = await mint_host_recovery_link(handle)
    except LookupError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception:
        print("Could not mint a sign-in link.", file=sys.stderr)
        return 1
    # stdout only — callers must not redirect this to a file.
    print(url)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args.handle or ""))


if __name__ == "__main__":
    sys.exit(main())
