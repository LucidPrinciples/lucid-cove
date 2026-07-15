"""Jules: never discard a live on-screen transcript when re-transcribe returns empty.

The client prefers re-transcribing the full webm (cutoff fix), but if Whisper
returns empty/no-speech while live WS chunks already landed on screen, the save
must fall back to the live transcript. This module documents the decision table
so a future rewrite of jules.html keeps the same contract.
"""


def choose_save_path(*, live_words: int, blob_kb: float, retranscribe_ok: bool,
                     retranscribe_error: str = "") -> str:
    """Return which path the client should take.

    - retranscribe: use /api/jules/transcribe-and-save result
    - live: save getFullTranscript() via /api/jules/save
    - empty: show "Didn't catch any speech"
    - error: surface retranscribe_error
    """
    has_live = live_words > 0
    use_file = blob_kb > 20
    if use_file:
        if retranscribe_ok:
            return "retranscribe"
        emptyish = (not retranscribe_error) or bool(
            __import__("re").search(r"empty|no speech|no audio|silence", retranscribe_error, __import__("re").I)
        )
        if has_live:
            return "live"
        if emptyish:
            return "empty"
        return "error"
    if has_live:
        return "live"
    return "empty"


def test_live_chunks_survive_empty_retranscribe():
    # The failure JAG hit: chunks on screen, re-transcribe says no speech.
    assert choose_save_path(live_words=120, blob_kb=400, retranscribe_ok=False,
                            retranscribe_error="Empty transcription") == "live"


def test_true_silence_stays_empty():
    assert choose_save_path(live_words=0, blob_kb=400, retranscribe_ok=False,
                            retranscribe_error="No speech detected") == "empty"


def test_happy_retranscribe_wins():
    assert choose_save_path(live_words=80, blob_kb=400, retranscribe_ok=True) == "retranscribe"


def test_no_blob_uses_live():
    assert choose_save_path(live_words=40, blob_kb=5, retranscribe_ok=False) == "live"


def test_no_blob_no_live_is_empty():
    assert choose_save_path(live_words=0, blob_kb=0, retranscribe_ok=False) == "empty"
