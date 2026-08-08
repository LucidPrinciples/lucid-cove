"""Active list + moments plan progress (list/moments flow)."""

from src.dashboard.routes import video_pipeline as vp


def test_moments_plan_progress_left_and_complete():
    data = {
        "moments": [
            {
                "clips": [
                    {"processed": True},
                    {"skipped": True},
                    {"label": "left one"},
                ]
            }
        ]
    }
    p = vp._moments_plan_progress(data)
    assert p["clip_count"] == 3
    assert p["clips_left"] == 1
    assert p["moments_complete"] is False

    data["moments"][0]["clips"][2]["processed"] = True
    p2 = vp._moments_plan_progress(data)
    assert p2["clips_left"] == 0
    assert p2["moments_complete"] is True


def test_moments_plan_progress_empty():
    p = vp._moments_plan_progress(None)
    assert p["moments_complete"] is False
    assert p["clips_left"] is None


def test_shorts_belong_to_stem_clean_dual():
    assert vp._shorts_belong_to_stem("IMG_7171", "IMG_7171-m1-quote.mp4")
    assert not vp._shorts_belong_to_stem("IMG_7171", "IMG_7171-clean-m11.mp4")


def test_pipeline_html_keeps_incomplete():
    from pathlib import Path
    html = (
        Path(__file__).resolve().parents[1]
        / "src/dashboard/static/action-board/full-video-pipeline.html"
    ).read_text()
    assert "if (t.has_processed) continue" not in html
    assert "moments_complete" in html
    assert "clips_left" in html
