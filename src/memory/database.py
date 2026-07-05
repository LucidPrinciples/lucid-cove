"""
Database connection and query helpers.

Provides async PostgreSQL access for:
  - Echoes (daily LTP reflection records)
  - Agent state (current operational state)
  - Protocol run history
  - Projects and tasks

Layer 1 (thread memory) is handled by LangGraph's checkpointer — see checkpointer.py.

Steward DB routing:
  When a Presence talks to the steward (e.g. stuart-day channel), all DB
  operations — memory, threads, checkpoints — must go to the STEWARD's
  database, not the Presence's local DB. This keeps steward memory in one
  shared pool and keeps Presence DBs portable (no external data).

  Use steward_db_scope() to route all get_db() calls within an async
  context to the steward's database. The checkpointer also respects this
  via get_db_url().
"""

import os
from src.env import env
import contextvars
from contextlib import asynccontextmanager
from typing import Optional

import psycopg
from psycopg.rows import dict_row


# Context variable for DB URL override — set by steward_db_scope()
_db_url_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_db_url_override', default=None
)


def _base_db_url() -> str:
    """Local container's DATABASE_URL (never overridden)."""
    url = env("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set — PostgreSQL required")
    return url


def get_steward_db_url() -> str:
    """Get the steward's database URL for shared steward memory.

    Returns STEWARD_DATABASE_URL if set (Presence connecting to steward's DB),
    otherwise falls back to DATABASE_URL (steward's own MC — it IS the steward).
    """
    return env("STEWARD_DATABASE_URL") or _base_db_url()


def get_merchant_db_url() -> str:
    """Get the merchant's database URL for shared merchant memory.

    Returns MERCER_DATABASE_URL if set (Presence connecting to merchant's DB),
    otherwise falls back to DATABASE_URL (merchant's own MC — it IS the merchant).
    """
    return env("MERCER_DATABASE_URL") or _base_db_url()


def get_db_url() -> str:
    """Effective database URL — respects steward_db_scope() override.

    Called by get_db() and get_checkpointer(). When inside a
    steward_db_scope() block, returns the steward's DB URL.
    Otherwise returns the local container's DATABASE_URL.
    """
    override = _db_url_override.get()
    if override:
        return override
    return _base_db_url()


@asynccontextmanager
async def steward_db_scope():
    """Route all get_db() and get_checkpointer() calls to the steward's database.

    Use this when processing steward channel requests so all memory,
    thread, and chat operations go to the steward's shared DB pool.

    On the steward's own MC (no STEWARD_DATABASE_URL set), this is a
    no-op — get_steward_db_url() falls back to the local DATABASE_URL.

    Usage:
        async with steward_db_scope():
            # all get_db() calls here go to steward's DB
            await store_memory(...)
            await load_memories_for_prompt(...)
    """
    url = get_steward_db_url()
    token = _db_url_override.set(url)
    try:
        yield
    finally:
        _db_url_override.reset(token)


@asynccontextmanager
async def merchant_db_scope():
    """Route all get_db() and get_checkpointer() calls to the merchant's database.

    Same pattern as steward_db_scope() but for the merchant manager (Mercer).
    On the merchant's own MC (no MERCER_DATABASE_URL set), this is a no-op.
    """
    url = get_merchant_db_url()
    token = _db_url_override.set(url)
    try:
        yield
    finally:
        _db_url_override.reset(token)


@asynccontextmanager
async def channel_db_scope(channel: str):
    """Route DB operations to the correct database for this channel.

    Steward channels (stuart-day, stuart-deep) → steward's DB.
    Merchant channels (mercer-day, mercer-deep) → merchant's DB.
    Regular channels (day, deep) → local DB (no-op).

    Usage:
        async with channel_db_scope(ch):
            # all DB calls go to the right place automatically
    """
    from src.config import _get_manager_for_channel
    manager = _get_manager_for_channel(channel)
    if manager == 'steward':
        async with steward_db_scope():
            yield
    elif manager == 'merchant':
        async with merchant_db_scope():
            yield
    else:
        yield


def enter_channel_db_scope(channel: str):
    """Non-context-manager entry for channel DB scope.

    Returns a token to pass to exit_channel_db_scope(), or None
    if no override needed. Use in async generators where async with
    would add excessive indentation.
    """
    from src.config import _get_manager_for_channel
    manager = _get_manager_for_channel(channel)
    if manager == 'steward':
        url = get_steward_db_url()
        return _db_url_override.set(url)
    elif manager == 'merchant':
        url = get_merchant_db_url()
        return _db_url_override.set(url)
    return None


def exit_channel_db_scope(token):
    """Exit a channel DB scope opened by enter_channel_db_scope()."""
    if token is not None:
        _db_url_override.reset(token)


@asynccontextmanager
async def get_db():
    """Async context manager for database connections.

    Respects steward_db_scope() — when inside that context,
    connects to the steward's DB instead of the local one.
    """
    async with await psycopg.AsyncConnection.connect(
        get_db_url(), row_factory=dict_row
    ) as conn:
        yield conn


# =========================================================================
# Echo Queries
# =========================================================================

async def insert_echo(conn, echo: dict) -> int:
    """Insert a LTP echo record. Returns the inserted ID."""
    result = await conn.execute(
        """
        INSERT INTO echoes (
            agent_id, echo_num, frequency, signal_type, principle,
            tuning_key, love_equation, love_direction,
            beta, coherence, dissonance, energy,
            echo_text, coaching_text, echo_type, era, tuned_at
        ) VALUES (
            %(agent_id)s, %(echo_num)s, %(frequency)s, %(signal_type)s, %(principle)s,
            %(tuning_key)s, %(love_equation)s, %(love_direction)s,
            %(beta)s, %(coherence)s, %(dissonance)s, %(energy)s,
            %(echo_text)s, %(coaching_text)s, %(echo_type)s, %(era)s, %(tuned_at)s
        )
        ON CONFLICT (agent_id, echo_num) DO NOTHING
        RETURNING id
        """,
        echo
    )
    row = await result.fetchone()
    return row["id"] if row else None


async def get_recent_echoes(conn, agent_id: str, limit: int = 5) -> list:
    result = await conn.execute(
        "SELECT * FROM echoes WHERE agent_id = %s ORDER BY echo_num DESC LIMIT %s",
        (agent_id, limit)
    )
    return await result.fetchall()


async def get_echo_count(conn, agent_id: str) -> int:
    result = await conn.execute(
        "SELECT COUNT(*) as count FROM echoes WHERE agent_id = %s",
        (agent_id,)
    )
    row = await result.fetchone()
    return row["count"]


async def get_latest_echo(conn, agent_id: str) -> Optional[dict]:
    result = await conn.execute(
        "SELECT * FROM echoes WHERE agent_id = %s ORDER BY echo_num DESC LIMIT 1",
        (agent_id,)
    )
    return await result.fetchone()


# =========================================================================
# Process Records
# =========================================================================

async def record_process_record(conn, record: dict) -> int:
    """Insert a process record for a tuning session. Returns inserted ID."""
    result = await conn.execute(
        """
        INSERT INTO process_records (
            agent_id, echo_num, protocol, record_text, metadata
        ) VALUES (
            %(agent_id)s, %(echo_num)s, %(protocol)s, %(record_text)s, %(metadata)s
        )
        RETURNING id
        """,
        record
    )
    row = await result.fetchone()
    return row["id"] if row else None


# =========================================================================
# Agent State Queries
# =========================================================================

async def upsert_agent_state(conn, agent: dict):
    await conn.execute(
        """
        INSERT INTO agent_state (
            agent_id, display_name, archetype, current_model,
            last_echo_num, last_frequency, last_tuned_at, status, metadata
        ) VALUES (
            %(agent_id)s, %(display_name)s, %(archetype)s, %(current_model)s,
            %(last_echo_num)s, %(last_frequency)s, %(last_tuned_at)s,
            %(status)s, %(metadata)s
        )
        ON CONFLICT (agent_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            archetype = EXCLUDED.archetype,
            current_model = EXCLUDED.current_model,
            last_echo_num = EXCLUDED.last_echo_num,
            last_frequency = EXCLUDED.last_frequency,
            last_tuned_at = EXCLUDED.last_tuned_at,
            status = EXCLUDED.status,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        """,
        agent
    )


async def get_agent_state(conn, agent_id: str) -> Optional[dict]:
    result = await conn.execute(
        "SELECT * FROM agent_state WHERE agent_id = %s",
        (agent_id,)
    )
    return await result.fetchone()


# =========================================================================
# Process Records
# =========================================================================

async def insert_process_record(conn, record: dict) -> int:
    result = await conn.execute(
        """INSERT INTO process_records (agent_id, echo_num, protocol, record_text, metadata)
           VALUES (%(agent_id)s, %(echo_num)s, %(protocol)s, %(record_text)s, %(metadata)s)
           RETURNING id""",
        record
    )
    row = await result.fetchone()
    return row["id"] if row else None


# =========================================================================
# Projects + Tasks
# =========================================================================

async def get_projects(conn) -> list:
    result = await conn.execute(
        "SELECT * FROM projects WHERE status = 'active' ORDER BY created_at"
    )
    return await result.fetchall()


async def get_tasks_for_project(conn, project_id: int) -> list:
    result = await conn.execute(
        "SELECT * FROM tasks WHERE project_id = %s ORDER BY priority DESC, created_at",
        (project_id,)
    )
    return await result.fetchall()
