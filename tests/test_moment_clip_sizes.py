"""Hard platform lines for nested quote / thought / story."""
from __future__ import annotations

from src.moment_clip_sizes import (
    QUOTE_MAX,
    STORY_MAX,
    STORY_MIN,
    THOUGHT_MAX,
    normalize_moment_clips,
    normalize_moments_result,
)


def test_full_nest_expands_short_story_and_clamps_thought():
    m = {
        "id": 1,
        "start_seconds": 100.0,
        "end_seconds": 280.0,
        "clips": [
            {"type": "quote", "start_seconds": 200.0, "end_seconds": 220.0, "duration_seconds": 20},
            {"type": "thought", "start_seconds": 180.0, "end_seconds": 250.0, "duration_seconds": 70},
            {"type": "story", "start_seconds": 160.0, "end_seconds": 230.0, "duration_seconds": 70},
        ],
    }
    normalize_moment_clips(m)
    by = {c["type"]: c for c in m["clips"]}
    assert set(by) == {"quote", "thought", "story"}
    assert by["thought"]["duration_seconds"] <= THOUGHT_MAX + 0.01
    assert STORY_MIN - 0.01 <= by["story"]["duration_seconds"] <= STORY_MAX + 0.01
    assert by["quote"]["duration_seconds"] <= QUOTE_MAX + 0.01
    # Nested: quote ⊆ thought ⊆ story
    assert by["quote"]["start_seconds"] >= by["thought"]["start_seconds"] - 0.05
    assert by["quote"]["end_seconds"] <= by["thought"]["end_seconds"] + 0.05
    assert by["thought"]["start_seconds"] >= by["story"]["start_seconds"] - 0.05
    assert by["thought"]["end_seconds"] <= by["story"]["end_seconds"] + 0.05


def test_long_moment_missing_story_gets_all_three():
    m = {
        "id": 2,
        "start_seconds": 0.0,
        "end_seconds": 200.0,
        "clips": [
            {"type": "quote", "start_seconds": 10.0, "end_seconds": 25.0},
            {"type": "thought", "start_seconds": 5.0, "end_seconds": 80.0},
        ],
    }
    normalize_moment_clips(m)
    types = {c["type"] for c in m["clips"]}
    assert types == {"quote", "thought", "story"}
    story = next(c for c in m["clips"] if c["type"] == "story")
    assert story["duration_seconds"] >= STORY_MIN - 0.01
    thought = next(c for c in m["clips"] if c["type"] == "thought")
    assert thought["duration_seconds"] <= THOUGHT_MAX + 0.01


def test_short_moment_no_forced_story():
    m = {
        "id": 3,
        "start_seconds": 0.0,
        "end_seconds": 50.0,
        "clips": [
            {"type": "quote", "start_seconds": 5.0, "end_seconds": 20.0},
            {"type": "thought", "start_seconds": 0.0, "end_seconds": 50.0},
        ],
    }
    normalize_moment_clips(m)
    types = {c["type"] for c in m["clips"]}
    assert "story" not in types
    thought = next(c for c in m["clips"] if c["type"] == "thought")
    assert thought["duration_seconds"] <= THOUGHT_MAX + 0.01


def test_normalize_moments_result_batch():
    result = {
        "moments": [
            {
                "id": 1,
                "start_seconds": 0.0,
                "end_seconds": 180.0,
                "clips": [
                    {"type": "story", "start_seconds": 0.0, "end_seconds": 60.0, "duration_seconds": 60},
                ],
            }
        ]
    }
    normalize_moments_result(result, video_duration=600.0)
    m = result["moments"][0]
    types = {c["type"] for c in m["clips"]}
    assert types == {"quote", "thought", "story"}


def test_pipeline_prompt_has_hard_lines():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/dashboard/routes/video_pipeline.py").read_text()
    assert "MUST stay under 60" in src
    assert "120-180" in src
    assert "moment_clip_sizes" in src
    assert "normalize_moments_result" in src
    assert "45-90 seconds" not in src
    assert "2-5 minutes" not in src
