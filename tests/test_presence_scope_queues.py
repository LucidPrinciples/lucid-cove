"""CF-1 — presence isolation for the social/youtube queues (strict self-scope).

Covers the three legs of CF-1:
  (a) migration 025 exists and is idempotent (IF NOT EXISTS on columns and
      indexes, accounts-guarded backfill),
  (b) the _acting_presence_id helper's three-way contract:
        None -> single mode, no scoping (behave as today)
        ''   -> multi mode but no resolvable presence: match NOTHING
        id   -> scope to this presence,
  (c) the scoped sites in action_board.py are marked with CF-1 comments.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "docker" / "migrations" / "025_presence_scope_queues.sql"
ACTION_BOARD = REPO / "src" / "dashboard" / "routes" / "action_board.py"


class _FakeRequest:
    """The helper only passes the request through to get_current_presence."""


# ── (a) migration ─────────────────────────────────────────────────────


def test_migration_file_exists():
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"


def test_migration_alter_tables_are_idempotent():
    sql = MIGRATION.read_text()
    assert "ALTER TABLE youtube_queue ADD COLUMN IF NOT EXISTS presence_id" in sql
    assert "ALTER TABLE social_queue" in sql
    # Both ALTERs carry IF NOT EXISTS (idempotent — safe to re-run).
    assert sql.count("ADD COLUMN IF NOT EXISTS presence_id") == 2


def test_migration_indexes_are_idempotent():
    sql = MIGRATION.read_text()
    assert sql.count("CREATE INDEX IF NOT EXISTS") == 2
    assert "youtube_queue (presence_id)" in sql
    assert "social_queue (presence_id)" in sql


def test_migration_backfill_is_accounts_guarded():
    sql = MIGRATION.read_text()
    # Backfill only runs when the accounts table exists.
    assert "to_regclass('public.accounts')" in sql
    assert "cove_role = 'admin'" in sql
    # Only rows that predate scoping are touched.
    assert sql.count("WHERE presence_id IS NULL") == 2


# ── (b) _acting_presence_id contract ─────────────────────────────────


async def test_helper_returns_none_in_single_mode(monkeypatch):
    from src.dashboard.routes import action_board

    monkeypatch.setattr(
        action_board, "env",
        lambda key, default=None: "single" if key == "COVE_MODE" else default,
    )
    assert await action_board._acting_presence_id(_FakeRequest()) is None


async def test_helper_returns_empty_string_when_no_presence(monkeypatch):
    from src.dashboard.routes import action_board, presence

    monkeypatch.setattr(
        action_board, "env",
        lambda key, default=None: "multi" if key == "COVE_MODE" else default,
    )

    async def _no_presence(request):
        return None

    # get_current_presence is imported INSIDE the helper — patch the source module.
    monkeypatch.setattr(presence, "get_current_presence", _no_presence)
    assert await action_board._acting_presence_id(_FakeRequest()) == ""


async def test_helper_returns_id_string_when_presence_exists(monkeypatch):
    from src.dashboard.routes import action_board, presence

    monkeypatch.setattr(
        action_board, "env",
        lambda key, default=None: "multi" if key == "COVE_MODE" else default,
    )

    async def _presence(request):
        return {"id": 42, "name": "Steward"}

    monkeypatch.setattr(presence, "get_current_presence", _presence)
    assert await action_board._acting_presence_id(_FakeRequest()) == "42"


def test_scope_clause_shapes():
    from src.dashboard.routes.action_board import _scope_clause

    assert _scope_clause(None) == ("", ())            # single mode: no-op
    sql, args = _scope_clause("7")
    assert sql == " AND presence_id = %s"
    assert args == ("7",)


# ── (c) scoped sites are marked ──────────────────────────────────────


def test_action_board_has_cf1_comments():
    src = ACTION_BOARD.read_text()
    assert src.count("CF-1") >= 5, "every scoped site must carry a CF-1 comment"
