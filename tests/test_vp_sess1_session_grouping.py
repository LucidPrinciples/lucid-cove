"""#VP-SESS1 — session identity (source_stem + role) and UI grouping."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
MIG = (ROOT / "docker/migrations/040_youtube_queue_source_stem.sql").read_text(encoding="utf-8")
INIT = (ROOT / "docker/init-base.sql").read_text(encoding="utf-8")


def test_migration_adds_youtube_source_stem():
    assert "ALTER TABLE youtube_queue ADD COLUMN IF NOT EXISTS source_stem" in MIG
    assert "idx_ytq_source_stem" in MIG
    assert "source_stem" in INIT


def test_promote_copies_source_stem():
    assert "def _session_stem_from_row" in AB
    assert "def _session_role" in AB
    assert "source_stem, clip_type" in AB or "source_stem" in AB
    assert "source_stem=COALESCE" in AB or "source_stem" in AB
    # INSERT lists source_stem
    assert "presence_id, source_stem)" in AB or "source_stem)" in AB


def test_board_payloads_include_session_fields():
    assert '"source_stem": stem' in AB or '"source_stem": stem' in AB.replace(" ", "")
    assert '"session_role"' in AB
    assert "_session_stem_from_row" in AB
    assert "_session_role" in AB


def test_js_groups_by_session():
    assert "function _groupItemsBySession" in JS
    assert "function _renderGrouped" in JS
    assert "ab-session-group" in JS
    assert "Session " in JS
    assert "session_role" in JS
    # drafts, scheduled, history all go through grouped render
    assert "_renderGrouped(items" in JS
    assert JS.count("return _renderGrouped") >= 3


def test_css_session_group():
    assert ".ab-session-group" in CSS
    assert ".ab-session-header" in CSS
    assert ".ab-meta-full" in CSS
