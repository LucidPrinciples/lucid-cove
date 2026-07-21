"""#VP-CAL — Video Pipeline secondary calendar + one evolving lifecycle event.

Guarantees:
1. Events write to video-pipeline, not personal/
2. Calendar is ensure-on-first-write (MKCOL)
3. Upload path UPDATES the event (does not delete)
4. Mark Published deletes the event (calendar clears with Actions)
5. create still returns bool for board failure surfacing
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAL = (ROOT / "src" / "dashboard" / "routes" / "youtube_calendar.py").read_text()
SCHED = (ROOT / "src" / "utils" / "scheduler.py").read_text()
BOARD = (ROOT / "src" / "dashboard" / "routes" / "action_board.py").read_text()


def test_writes_to_video_pipeline_not_personal():
    assert 'VIDEO_PIPELINE_CALENDAR = "video-pipeline"' in CAL
    # Primary event URL must use video-pipeline
    assert "video-pipeline" in CAL
    # Must not hardcode personal as the only write target
    assert re.search(
        r'/personal/\{uid\}\.ics', CAL
    ) is None or "legacy" in CAL.lower(), \
        "personal/ still looks like the primary write path"


def test_ensure_calendar_on_write():
    assert "_ensure_video_pipeline_calendar" in CAL
    assert "MKCOL" in CAL
    assert "Video Pipeline" in CAL


def test_create_returns_bool_and_accepts_status():
    assert re.search(
        r"async def create_youtube_calendar_event\([\s\S]*?\)\s*->\s*bool",
        CAL,
    )
    assert "status:" in CAL or "status =" in CAL
    assert "youtube_video_id" in CAL


def test_upload_updates_calendar_not_deletes():
    # Post-upload path must update lifecycle event
    assert "update_youtube_calendar_event" in SCHED
    assert 'status="uploaded"' in SCHED or "status='uploaded'" in SCHED
    # Must NOT delete calendar immediately after successful upload
    # (the old pattern deleted right after upload)
    upload_fn = SCHED.split("async def _upload_youtube_post")[1].split(
        "async def _create_youtube_followups"
    )[0]
    assert "delete_youtube_calendar_event" not in upload_fn, \
        "upload still deletes the calendar event — VP-CAL wants it kept until Mark Published"


def test_mark_published_deletes_calendar():
    assert "delete_youtube_calendar_event" in BOARD
    # mark_published handler should call delete
    chunk = BOARD.split("async def mark_published")[1].split("@router.")[0]
    assert "delete_youtube_calendar_event" in chunk


def test_build_ical_phases():
    assert "Upload · YT" in CAL
    assert "Private · YT" in CAL
    assert "_build_ical" in CAL


def test_legacy_personal_delete_still_attempted():
    # Delete should still try personal so pre-VP-CAL ghosts get cleaned
    del_fn = CAL.split("async def delete_youtube_calendar_event")[1]
    assert "personal" in del_fn
