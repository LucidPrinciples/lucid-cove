"""#VP-HIST1 — Action board published History surface (lazy, newest-first)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")


def test_history_route_exists():
    assert '@router.get("/api/action-board/history")' in AB
    assert "async def get_history" in AB


def test_history_scopes_presence_and_public_gate():
    # Same CF-1 pattern as scheduled
    assert "_acting_presence_id" in AB
    assert "_is_public_app" in AB
    # History body mentions both queues
    assert "status = 'published'" in AB
    assert "youtube_queue" in AB
    assert "platform = 'x'" in AB


def test_history_exposes_watch_and_studio_fields():
    assert '"watch_url"' in AB or "'watch_url'" in AB
    assert "studio.youtube.com/video/" in AB
    assert "www.youtube.com/watch?v=" in AB
    assert "x.com/i/web/status/" in AB


def test_history_pagination_bounds():
    assert "max(1, min(int(limit" in AB
    assert "has_more" in AB
    assert "offset" in AB


def test_js_lazy_history_subtab():
    assert "loadHistorySubtab" in JS
    assert "_renderHistoryCards" in JS
    assert "id: 'history'" in JS or 'id: "history"' in JS
    assert "/api/action-board/history" in JS
    # Must not fetch history on cold actions load (PERF-MC1)
    load_fn = JS.split("async function loadABActions")[1].split("async function")[0]
    assert "/api/action-board/history" not in load_fn
    assert "/api/action-board/actions" in load_fn
    assert "/api/action-board/scheduled" in load_fn


def test_js_history_loads_on_subtab_switch():
    assert "subId === 'history'" in JS
    assert "loadHistorySubtab" in JS
    assert "Watch ↗" in JS
