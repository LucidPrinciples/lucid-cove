"""batch8 #6 — hyphen-safe stem derivation. Live case: `IMG_7168-Test1`."""
from src.video_stems import (
    stem_from_derived_name, stem_from_transcript_name, caption_glob_matches_sibling,
)

HY = "IMG_7168-Test1"   # a stem that itself contains a hyphen
PLAIN = "IMG_7129"


def test_transcript_name_preserves_hyphenated_stem():
    assert stem_from_transcript_name(f"{HY}-transcript.json") == HY
    assert stem_from_transcript_name(f"{HY}-transcript-edited.json") == HY
    assert stem_from_transcript_name(f"{PLAIN}-transcript.json") == PLAIN


def test_derived_name_all_suffixes_hyphen_safe():
    for suf in ("-transcript.json", "-transcript-edited.json", "-moments.json",
                "-moments-processed.json", "-captioned.mp4", "-preview.mp4"):
        assert stem_from_derived_name(f"{HY}{suf}") == HY
        assert stem_from_derived_name(f"{PLAIN}{suf}") == PLAIN


def test_derived_name_never_splits_on_first_hyphen():
    # The failure this guards: a first-hyphen split would return "IMG_7168".
    assert stem_from_derived_name(f"{HY}-moments.json") != "IMG_7168"


def test_unknown_name_unchanged():
    assert stem_from_derived_name("random.mov") == "random.mov"


def test_caption_glob_sibling_false_positive_detected():
    # Checking caption existence for the base stem must recognize that a glob hit
    # of the hyphenated sibling's plain caption is ambiguous, not a title rename.
    assert caption_glob_matches_sibling("IMG_7168", "IMG_7168-Test1-captioned.mp4") is True
    # A genuine title-rename for the exact stem is also "has a middle" — ambiguous
    # by name alone, so the caller confirms with the exact plain check first.
    assert caption_glob_matches_sibling("IMG_7168", "IMG_7168-captioned.mp4") is False
