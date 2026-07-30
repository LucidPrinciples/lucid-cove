"""ASRVOCAB1 — proper-noun replace map for video transcripts."""

from src.asr_vocab import (
    apply_text,
    apply_to_transcript,
    load_default_pairs,
    merge_pairs,
    pairs_from_map_data,
)


def test_stewart_to_stuart_default():
    pairs = load_default_pairs()
    out, hits = apply_text("Hey Stewart, call Stuart later", pairs)
    assert hits >= 1
    assert "Stewart" not in out
    assert "Stuart" in out


def test_open_claw_variants():
    pairs = load_default_pairs()
    out, hits = apply_text("We tried Open Claw and open claw today", pairs)
    assert hits >= 2
    assert "Open Claw" not in out
    assert "open claw" not in out
    assert "OpenClaw" in out


def test_presence_overrides_default():
    defaults = [("Stewart", "Stuart")]
    presence = [("Stewart", "Stu")]  # presence wins
    pairs = merge_pairs(defaults, presence)
    out, _ = apply_text("Stewart here", pairs)
    assert out == "Stu here"


def test_longer_phrase_wins_order():
    pairs = merge_pairs([("Open", "X"), ("Open Claw", "OpenClaw")], [])
    out, _ = apply_text("Open Claw rocks", pairs)
    assert out == "OpenClaw rocks"


def test_word_boundary_no_partial():
    pairs = [("art", "ART")]
    out, hits = apply_text("Stuart is smart", pairs)
    assert hits == 0
    assert out == "Stuart is smart"


def test_apply_to_transcript_segments_and_flag():
    pairs = [("Stewart", "Stuart")]
    raw = {
        "text": "Stewart speaks",
        "segments": [
            {"text": "Stewart", "start": 0.0, "end": 0.4},
            {"text": "speaks", "start": 0.4, "end": 0.9},
        ],
    }
    out, stats = apply_to_transcript(raw, pairs)
    assert stats["applied"] is True
    assert stats["hits"] >= 1
    assert out["vocab_applied"] is True
    assert out["segments"][0]["text"] == "Stuart"
    assert "Stewart" not in out["text"]


def test_apply_skips_when_already_flagged():
    pairs = [("Stewart", "Stuart")]
    raw = {
        "text": "Stewart",
        "segments": [{"text": "Stewart", "start": 0, "end": 1}],
        "vocab_applied": True,
        "vocab_hits": 0,
    }
    out, stats = apply_to_transcript(raw, pairs)
    assert stats["skipped"] is True
    assert out["segments"][0]["text"] == "Stewart"


def test_force_reapply():
    pairs = [("Stewart", "Stuart")]
    raw = {
        "text": "Stewart",
        "segments": [{"text": "Stewart", "start": 0, "end": 1}],
        "vocab_applied": True,
    }
    out, stats = apply_to_transcript(raw, pairs, force=True)
    assert stats["applied"] is True
    assert out["segments"][0]["text"] == "Stuart"


def test_pairs_from_map_shapes():
    assert pairs_from_map_data({"Stewart": "Stuart"}) == [("Stewart", "Stuart")]
    assert pairs_from_map_data(
        {"replacements": [{"from": "A", "to": "B"}]}
    ) == [("A", "B")]
    assert pairs_from_map_data([["X", "Y"]]) == [("X", "Y")]
