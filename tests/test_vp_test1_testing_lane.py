"""#VP-TEST1 — Testing lane isolation + clear/mark APIs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
VP = (ROOT / "src/dashboard/routes/video_processing.py").read_text(encoding="utf-8")


def test_testing_helpers_and_routes():
    assert "def _is_testing_row" in AB
    assert "/api/action-board/testing/mark" in AB
    assert "/api/action-board/testing/clear" in AB
    assert '"category": "testing"' in AB or "category\": \"testing\"" in AB


def test_js_testing_subtab_and_clear():
    assert "testingItems" in JS
    assert "🧪 Testing" in JS
    assert "clearTestingLane" in JS
    assert "/api/action-board/testing/clear" in JS


def test_css_testing_bar():
    assert ".ab-testing-bar" in CSS


def test_pipeline_honors_testing_flag():
    assert "_testing_batch" in VP
    assert "test-moments-" in VP
    assert '"testing": True' in VP or "'testing': True" in VP


def test_helper_logic():
    # smoke exec helpers
    start = AB.index("def _parse_platform_data")
    end = AB.index("async def _promote_youtube_post")
    ns = {}
    exec(AB[start:end], ns)
    assert ns["_is_testing_row"]("test-moments-IMG_1", None) is True
    assert ns["_is_testing_row"]("moments-IMG_1", {"testing": True}) is True
    assert ns["_is_testing_row"]("moments-IMG_1", {}) is False
