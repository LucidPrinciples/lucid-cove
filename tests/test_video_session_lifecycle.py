"""#VP-SESS-LIFE1 — session open-work model + Actions surface."""
from pathlib import Path

from src.video_session import (
    build_session_snapshot,
    classify_queue_row,
    has_processed_outputs,
    is_session_clear,
    may_graduate_to_raw,
    moments_plan_progress,
    shorts_belong_to_stem,
    summarize_open_work,
)

ROOT = Path(__file__).resolve().parents[1]
AB = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")
JS = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
CSS = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
VP = (ROOT / "src/dashboard/routes/video_pipeline.py").read_text(encoding="utf-8")


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
    p = moments_plan_progress(data)
    assert p["clip_count"] == 3
    assert p["clips_left"] == 1
    assert p["clips_done"] == 1
    assert p["clips_skipped"] == 1
    assert p["moments_complete"] is False

    data["moments"][0]["clips"][2]["processed"] = True
    p2 = moments_plan_progress(data)
    assert p2["clips_left"] == 0
    assert p2["moments_complete"] is True


def test_moments_plan_progress_empty():
    p = moments_plan_progress(None)
    assert p["moments_complete"] is False
    assert p["clips_left"] is None


def test_shorts_belong_to_stem_rejects_clean_dual():
    assert shorts_belong_to_stem("IMG_7171", "IMG_7171-m1-quote.mp4")
    assert not shorts_belong_to_stem("IMG_7171", "IMG_7171-clean-m11.mp4")
    assert has_processed_outputs(
        "IMG_7171",
        {"IMG_7171-m1.mp4", "IMG_7171-clean-m11.mp4"},
    )
    assert not has_processed_outputs(
        "IMG_7171",
        {"IMG_7171-clean-m11.mp4", "IMG_7171-preview.mp4"},
    )


def test_session_stays_open_when_clips_left_even_if_raw():
    snap = build_session_snapshot(
        stem="IMG_7923",
        in_raw=True,
        has_transcript=True,
        has_moments=True,
        moments_data={
            "moments": [{"clips": [{"processed": True}, {"label": "left"}]}]
        },
        has_processed=True,
    )
    assert snap["on_open_work"] is True
    assert snap["is_clear"] is False
    assert snap["phase"] == "clips_remaining"
    assert snap["clips_left"] == 1
    assert snap["may_graduate_to_raw"] is False


def test_session_clear_requires_plan_and_settled_queue():
    plan_done = {
        "moments": [{"clips": [{"processed": True}, {"skipped": True}]}]
    }
    assert (
        is_session_clear(
            has_transcript=True,
            has_moments=True,
            skip_moments=False,
            moments_complete=True,
            queue_open=0,
            queue_uploaded=0,
            queue_scheduled=0,
        )
        is True
    )
    snap = build_session_snapshot(
        stem="IMG_7159",
        in_processing=True,
        has_transcript=True,
        has_moments=True,
        moments_data=plan_done,
        has_processed=True,
        queue_rows=[{"status": "uploaded", "title": "x"}],
    )
    assert snap["is_clear"] is False
    assert snap["phase"] == "uploaded_awaiting_publish"
    assert snap["may_graduate_to_raw"] is False

    snap2 = build_session_snapshot(
        stem="IMG_7159",
        in_processing=True,
        has_transcript=True,
        has_moments=True,
        moments_data=plan_done,
        has_processed=True,
        queue_rows=[{"status": "published"}],
    )
    assert snap2["is_clear"] is True
    assert snap2["phase"] == "clear"
    assert snap2["may_graduate_to_raw"] is True


def test_graduate_gated_until_clear():
    assert (
        may_graduate_to_raw(
            has_transcript=True,
            has_moments=True,
            skip_moments=False,
            moments_complete=True,
            queue_open=0,
            queue_uploaded=0,
            queue_scheduled=0,
            in_processing=True,
        )
        is True
    )
    assert (
        may_graduate_to_raw(
            has_transcript=True,
            has_moments=True,
            skip_moments=False,
            moments_complete=False,
            queue_open=0,
            queue_uploaded=0,
            queue_scheduled=0,
            in_processing=True,
        )
        is False
    )


def test_skip_moments_complete_when_processed():
    snap = build_session_snapshot(
        stem="SHORT1",
        in_processing=True,
        has_transcript=True,
        has_moments=False,
        skip_moments=True,
        has_processed=True,
        queue_rows=[],
    )
    assert snap["moments_complete"] is True
    assert snap["is_clear"] is True
    assert snap["may_graduate_to_raw"] is True


def test_classify_queue_and_summary():
    assert classify_queue_row("draft") == "open"
    assert classify_queue_row("queued") == "scheduled"
    assert classify_queue_row("uploaded") == "uploaded"
    assert classify_queue_row("published") == "settled"
    sessions = [
        build_session_snapshot(
            stem="A",
            has_transcript=True,
            has_moments=True,
            moments_data={"moments": [{"clips": [{"label": "x"}]}]},
        ),
        build_session_snapshot(
            stem="B",
            has_transcript=True,
            has_moments=True,
            moments_data={"moments": [{"clips": [{"processed": True}]}]},
        ),
    ]
    s = summarize_open_work(sessions)
    assert s["open_count"] == 1
    assert s["clear_count"] == 1


def test_pipeline_delegates_progress_helpers():
    assert "from src.video_session import moments_plan_progress" in VP
    assert "from src.video_session import shorts_belong_to_stem" in VP


def test_open_work_route_exists():
    assert '@router.get("/api/action-board/open-work")' in AB
    assert "async def get_open_work" in AB
    assert "build_session_snapshot" in AB
    assert "on_open_work" in AB
    assert "summarize_open_work" in AB


def test_js_open_work_tab():
    assert "id: 'open-work'" in JS or 'id: "open-work"' in JS
    assert "/api/action-board/open-work" in JS
    assert "function _renderOpenWorkPanel" in JS
    assert "function _renderOpenWorkCard" in JS
    assert "Open Work" in JS
    # first paint fetches open-work with actions/scheduled
    load_fn = JS.split("async function loadABActions")[1].split("async function")[0]
    assert "/api/action-board/open-work" in load_fn
    # History still lazy
    assert "/api/action-board/history" not in load_fn


def test_css_open_work():
    assert ".ab-open-work-card" in CSS
    assert ".ab-open-work-list" in CSS
    assert ".ab-ow-phase" in CSS


def test_try_graduate_session_wired():
    assert "async def try_graduate_session" in VP
    assert "may_graduate_to_raw" in VP
    assert "try_graduate_session" in (
        ROOT / "src/dashboard/routes/video_processing.py"
    ).read_text(encoding="utf-8")
    ab = (ROOT / "src/dashboard/routes/action_board.py").read_text(encoding="utf-8")
    assert "try_graduate_session" in ab
    assert "graduation" in ab


def test_voice_no_eager_graduate_on_render():
    voice = (ROOT / "voice/src/routes/video.py").read_text(encoding="utf-8")
    # process-moments / caption-full success paths must not call graduate inline
    pm = voice.split("async def process_moments")[1].split("async def graduate_stem_api")[0]
    assert "graduate_processing_to_raw" not in pm
    # caption-full still has the helper available via graduate-stem route only
    assert '@router.post("/api/video/graduate-stem")' in voice


def test_open_work_panel_full_width_css():
    css = (ROOT / "src/dashboard/static/css/action-board.css").read_text(encoding="utf-8")
    assert "#ab-act-panel-open-work" in css
    assert "flex-direction: column" in css
    js = (ROOT / "src/dashboard/static/js/action-board.js").read_text(encoding="utf-8")
    assert "ab-open-work-list" in js
    assert "Crop (" in js or "cropLabel" in js


def test_crop_loads_remaining_from_plan():
    crop = (ROOT / "src/dashboard/static/action-board/video-crop-position.html").read_text(
        encoding="utf-8"
    )
    assert "loadRemainingClipsFromPlan" in crop
    assert "/api/video/moments/" in crop
    assert "preferPlan" in crop
    assert "Clips to cut" in crop
    # Must not auto-bounce to Moments when plan load is empty
    assert "window.location.href = momentsUrl" not in crop
    assert "Open Moments" in crop
    assert "Loading remaining clips" in crop


def test_open_work_crop_links_use_plan_flag():
    src = (ROOT / "src/video_session.py").read_text(encoding="utf-8")
    assert "video-crop-position.html?{param}&plan=1" in src


def test_moments_handoff_is_stem_scoped():
    moments = (
        ROOT / "src/dashboard/static/action-board/video-moments-review.html"
    ).read_text(encoding="utf-8")
    assert "approved_moments_${stem}" in moments or "approved_moments_${" in moments
    # MOMSAVE1: handoff may use saved.approved || approved after durable Save
    assert (
        "clips: approved" in moments
        or "clips: saved.approved" in moments
        or "saved.approved || approved" in moments
    )
