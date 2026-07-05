# batch-9 #11 (B13): collapse consecutive duplicate transcript segments (the live STT path
# re-transcribed the growing buffer and stacked one sentence ~18×).
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from src.dashboard.routes.jules import collapse_consecutive_duplicates as cc  # noqa: E402


def test_collapses_run3_18x_repeat():
    seg = "I need to call the pharmacy about the refill."
    text = "\n\n".join([seg] * 18)
    assert cc(text) == seg


def test_keeps_distinct_consecutive():
    text = "First thought.\n\nSecond thought.\n\nThird thought."
    assert cc(text) == text


def test_non_consecutive_repeat_is_preserved():
    # A real transcript can legitimately repeat later — only CONSECUTIVE dups collapse.
    text = "Remember the milk.\n\nAlso eggs.\n\nRemember the milk."
    assert cc(text) == text


def test_normalization_case_and_space_insensitive():
    text = "Hello there.\n\nhello   THERE.\n\nDifferent."
    assert cc(text) == "Hello there.\n\nDifferent."


def test_growing_segment_keeps_longest():
    # Cumulative live chunks: prev is a prefix of the next → keep the longer.
    text = "I went to the\n\nI went to the store today."
    assert cc(text) == "I went to the store today."


def test_empty_passthrough():
    assert cc("") == ""
    assert cc("Just one line.") == "Just one line."
