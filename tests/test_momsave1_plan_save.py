"""MOMSAVE1 — durable moments plan Save + crop batch rules."""

from pathlib import Path

from src.video_session import apply_moments_plan_updates, moments_plan_progress


def _plan():
    return {
        "moments": [
            {
                "id": 1,
                "clips": [
                    {"type": "quote", "start_seconds": 1.0, "end_seconds": 5.0},
                    {"type": "thought", "start_seconds": 6.0, "end_seconds": 12.0},
                    {"type": "story", "start_seconds": 13.0, "end_seconds": 40.0},
                ],
            }
        ]
    }


def test_save_approve_skip_and_trim():
    data = _plan()
    counts = apply_moments_plan_updates(
        data,
        [
            {"moment_id": 1, "clip_type": "quote", "approved": True,
             "start_seconds": 1.5, "end_seconds": 4.5},
            {"moment_id": 1, "clip_type": "thought", "skipped": True},
            {"moment_id": 1, "clip_type": "story", "approved": False},
        ],
    )
    assert counts["changed"] == 3
    assert counts["approved"] == 1
    assert counts["skipped"] == 1
    q, t, s = data["moments"][0]["clips"]
    assert q["approved"] is True and q["start_seconds"] == 1.5 and q["end_seconds"] == 4.5
    assert t["skipped"] is True and t["processed"] is True and t.get("approved") is False
    assert s.get("approved") is False
    prog = moments_plan_progress(data)
    # skipped does not count as left; unapproved story still left
    assert prog["clips_left"] == 2  # quote + story still not processed
    assert prog["clips_skipped"] == 1


def test_save_does_not_resurrect_processed_cut():
    data = _plan()
    data["moments"][0]["clips"][0]["processed"] = True
    counts = apply_moments_plan_updates(
        data,
        [{"moment_id": 1, "clip_type": "quote", "approved": True}],
    )
    assert counts["changed"] == 0
    assert data["moments"][0]["clips"][0].get("approved") is not True


def test_crop_prefers_approved_batch_logic():
    """Mirror crop page rule: if any approved remain, batch is approved-only."""
    data = _plan()
    apply_moments_plan_updates(
        data,
        [
            {"moment_id": 1, "clip_type": "quote", "approved": True},
            {"moment_id": 1, "clip_type": "thought", "skipped": True},
        ],
    )
    remaining = []
    for m in data["moments"]:
        for c in m["clips"]:
            if c.get("processed") or c.get("skipped"):
                continue
            remaining.append(c)
    approved = [c for c in remaining if c.get("approved")]
    batch = approved if approved else remaining
    assert len(batch) == 1
    assert batch[0]["type"] == "quote"


def test_moments_ui_has_save_and_process_doors():
    html = Path("src/dashboard/static/action-board/video-moments-review.html").read_text()
    assert "savePlan" in html
    assert "/api/video/moments/save-plan" in html
    assert "Process approved" in html
    assert "Save plan" in html


def test_crop_prefers_approved_from_plan():
    html = Path("src/dashboard/static/action-board/video-crop-position.html").read_text()
    assert "approvedOnly" in html
    assert "VPMULTI1" in html or "never apply another master" in html


def test_save_plan_route_registered():
    src = Path("src/dashboard/routes/video_pipeline.py").read_text()
    assert '@router.post("/moments/save-plan")' in src
