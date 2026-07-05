# A13 round 3 (batch7 #2) — resilient moments-JSON extraction. The local brain's
# generation cap can truncate the JSON mid-object; a strict parse throws away the
# whole analysis. These cover: clean parse, <think> stripping, trailing-comma
# tolerance, and truncated-tail salvage (keep complete moments, drop the cut one).
import json

from src.moments_json import extract_moments_json, tail


def test_clean_json():
    blob = '{"moments": [{"id": 1, "topic": "a"}, {"id": 2, "topic": "b"}]}'
    r = extract_moments_json(blob)
    assert r is not None
    assert len(r["moments"]) == 2


def test_strips_think_tags_and_prose():
    blob = 'sure!\n<think>let me plan</think>\n{"moments": [{"id": 1}]}\nhope that helps'
    r = extract_moments_json(blob)
    assert r["moments"] == [{"id": 1}]


def test_trailing_comma_tolerated():
    blob = '{"moments": [{"id": 1}, {"id": 2},]}'
    r = extract_moments_json(blob)
    assert len(r["moments"]) == 2


def test_truncated_tail_salvages_complete_moments():
    # Two complete moments, then a third cut off mid-object (the generation cap).
    blob = ('{"moments": [\n'
            '  {"id": 1, "topic": "first", "clips": [{"type": "quote"}]},\n'
            '  {"id": 2, "topic": "second", "clips": [{"type": "thought"}]},\n'
            '  {"id": 3, "topic": "third", "cli')  # truncated
    r = extract_moments_json(blob)
    assert r is not None
    ids = [m["id"] for m in r["moments"]]
    assert ids == [1, 2]  # the truncated third is dropped, first two survive


def test_truncated_with_nested_braces_and_strings():
    # A brace inside a string must not confuse the salvage scanner.
    blob = ('{"moments": [\n'
            '  {"id": 1, "hook": "he said {maybe}", "clips": [{"type": "quote", "why": "a}b"}]},\n'
            '  {"id": 2, "hook": "next", "clips": [{"typ')  # truncated
    r = extract_moments_json(blob)
    assert [m["id"] for m in r["moments"]] == [1]
    assert r["moments"][0]["hook"] == "he said {maybe}"


def test_empty_or_garbage_returns_none():
    assert extract_moments_json("") is None
    assert extract_moments_json("no json here at all") is None


def test_no_complete_moment_returns_none():
    # First moment already truncated → nothing whole to salvage.
    blob = '{"moments": [ {"id": 1, "topic": "cut'
    assert extract_moments_json(blob) is None


def test_tail_returns_last_chars():
    assert tail("abcdefingklmnop", 4) == "mnop"
    assert tail("", 10) == ""
