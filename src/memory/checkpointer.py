"""
Checkpoint persistence — PostgreSQL for production, SQLite for dev.

LangGraph checkpointing stores the full state of every graph execution,
enabling: conversation memory across restarts, time-travel debugging,
and fault-tolerant resumption.

Respects steward_db_scope() — when inside that context, the checkpointer
connects to the steward's database so conversation state is shared.
"""

import os
from src.env import env
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.memory.database import get_db_url


@asynccontextmanager
async def get_checkpointer():
    """Get the appropriate checkpointer based on environment.

    Uses PostgreSQL if DATABASE_URL is set, otherwise falls back to SQLite.
    Both support async operations for non-blocking persistence.

    Respects steward_db_scope() — inside that context, connects to the
    steward's database instead of the local one.

    Usage:
        async with get_checkpointer() as checkpointer:
            graph = workflow.compile(checkpointer=checkpointer)
    """
    url = get_db_url()  # respects steward_db_scope() override

    if url:
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            yield saver
    else:
        db_path = env("SQLITE_PATH", "./data/checkpoints.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
            yield saver
