"""Metadata for shorts must use the approved clip window on the source timeline.

Bug: process-moments returned processed[] without start_seconds; queue code then
did `start = 0` and took the first duration_seconds of the full transcript — so a
mid-talk clip about agent memory got titles/descriptions about the opening
(book) section. Video file was cut correctly; only the LLM prompt was wrong.
"""
from src.dashboard.routes.video_processing import (
    _clip_window_seconds,
    _transcript_text_for_window,
)


SEGMENTS = [
    {"start": 0.0, "end": 10.0, "text": "writing the book chapter one"},
    {"start": 10.0, "end": 20.0, "text": "more book outline talk"},
    {"start": 100.0, "end": 110.0, "text": "agent memory stores what matters"},
    {"start": 110.0, "end": 120.0, "text": "recall across sessions"},
    {"start": 200.0, "end": 210.0, "text": "closing thoughts"},
]


def test_window_from_processed_payload():
    clip = {
        "moment_id": 3,
        "clip_type": "thought",
        "start_seconds": 100.0,
        "end_seconds": 120.0,
        "duration_seconds": 20.0,
    }
    assert _clip_window_seconds(clip, {}) == (100.0, 120.0)


def test_window_falls_back_to_approved_moments_when_processed_lacks_start():
    """Older voice images omitted start_seconds — use the request moments list."""
    clip = {
        "moment_id": 3,
        "clip_type": "thought",
        "duration_seconds": 20.0,
        # no start_seconds / end_seconds
    }
    moments = {
        (3, "thought"): {"moment_id": 3, "clip_type": "thought",
                         "start_seconds": 100.0, "end_seconds": 120.0},
    }
    assert _clip_window_seconds(clip, moments) == (100.0, 120.0)


def test_legacy_zero_start_plus_duration_is_last_resort():
    clip = {"duration_seconds": 15.0}
    assert _clip_window_seconds(clip, {}) == (0.0, 15.0)


def test_transcript_text_is_clip_window_not_opening():
    text = _transcript_text_for_window(SEGMENTS, 100.0, 120.0)
    assert "agent memory" in text
    assert "recall across sessions" in text
    assert "writing the book" not in text
    assert "closing thoughts" not in text


def test_transcript_overlap_includes_straddle_segments():
    # Segment 95–105 straddles start=100 — must still contribute
    segs = [
        {"start": 95.0, "end": 105.0, "text": "bridge into memory"},
        {"start": 105.0, "end": 115.0, "text": "agent memory core"},
    ]
    text = _transcript_text_for_window(segs, 100.0, 120.0)
    assert "bridge into memory" in text
    assert "agent memory core" in text


def test_strict_containment_would_miss_but_overlap_does_not():
    """Regression: old code required start>=clip_start AND end<=clip_end."""
    segs = [{"start": 99.0, "end": 101.0, "text": "edge word"}]
    # Old strict containment: 99 >= 100? no → empty. Overlap keeps it.
    assert "edge word" in _transcript_text_for_window(segs, 100.0, 120.0)


def test_voice_processed_payload_includes_start_seconds():
    """Lock the voice process-moments stamp so metadata never guesses t=0."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "voice/src/routes/video.py").read_text()
    # processed.append block must carry source window
    assert '"start_seconds"' in src
    assert '"end_seconds"' in src
    assert "start_seconds" in src and "round(float(start" in src
