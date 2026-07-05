"""Shared fixtures for the cove-core test suite (#94).

DB tests run against a THROWAWAY Postgres with the cove-core schema loaded.
They never mutate it — each test runs inside a transaction that is rolled back.

Setup (one time):

    createdb cove_test
    psql cove_test -f docker/init-base.sql
    export TEST_DATABASE_URL="postgresql://localhost/cove_test"

Run:

    pip install -e ".[dev]"
    pytest

If TEST_DATABASE_URL (or DATABASE_URL) is unset, the DB tests skip cleanly
so the pure-logic tests still run anywhere.
"""
import os

import pytest
import pytest_asyncio

TEST_DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

# Mark DB-dependent tests so they skip when no throwaway DB is configured.
requires_db = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="Set TEST_DATABASE_URL (or DATABASE_URL) to a throwaway Postgres to run DB tests.",
)


@pytest_asyncio.fixture
async def db():
    """A connection wrapped in a transaction that is rolled back after the test.

    The CRUD helpers in src.memory.database take a connection and leave the
    commit to the caller, so a test can insert, read it back within the same
    connection, and the rollback in the finally block discards everything.
    """
    import psycopg
    from psycopg.rows import dict_row

    conn = await psycopg.AsyncConnection.connect(
        TEST_DB_URL, row_factory=dict_row, autocommit=False
    )
    try:
        yield conn
    finally:
        await conn.rollback()
        await conn.close()
