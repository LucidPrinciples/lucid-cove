"""#VP-SESS-MAP1 — moments.json summary in session groups."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AB = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")


def test_session_map_route_and_summarizer():
    assert "/api/action-board/session-map" in AB
    assert "def _summarize_moments_map" in AB
    assert "transcripts/{stem}-moments.json" in AB or 'transcripts/{stem}-moments.json' in AB


def test_summarizer_counts():
    start = AB.index("def _summarize_moments_map")
    end = AB.index("@router.get(\"/api/action-board/session-map\")")
    ns = {}
    exec(AB[start:end], ns)
    data = {
        "moments": [
            {
                "id": 1,
                "theme_tag": "Deep Work",
                "clips": [
                    {"type": "quote", "label": "A", "processed": True, "start_seconds": 0, "end_seconds": 8},
                    {"type": "thought", "label": "B", "start_seconds": 10, "end_seconds": 20},
                    {"type": "story", "label": "C", "processed": True, "skipped": True, "start_seconds": 30, "end_seconds": 40},
                ],
            }
        ]
    }
    s = ns["_summarize_moments_map"](data, "IMG_7168")
    assert s["has_map"] is True
    assert s["clip_count"] == 3
    assert s["processed_count"] == 1
    assert s["skipped_count"] == 1
    assert s["left_count"] == 1
    assert s["moments"][0]["clips"][0]["state"] == "processed"
    assert s["moments"][0]["clips"][1]["state"] == "left"
    assert s["moments"][0]["clips"][2]["state"] == "skipped"


def test_js_map_ui():
    assert "ensureSessionMaps" in JS
    assert "_sessionMapPanelHtml" in JS
    assert "ab-session-map" in JS
    assert "ab-map-moment" in JS
    assert "/api/action-board/session-map" in JS


def test_css_map():
    assert ".ab-session-map" in CSS
    assert ".ab-map-clip-left" in CSS
