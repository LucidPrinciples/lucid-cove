"""Guards for live Whisper STT — drop silence and compression-loop junk.

Live Dictate/Voice must not auto-send hallucinated repeats. These helpers are
numpy-free so cove-core tests can load them without the voice image.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional

# faster-whisper live decode: never condition on previous text (that is the
# loop that turns a near-silent clip into "thank you thank you thank you").
LIVE_WHISPER_TRANSCRIBE_KWARGS = {
    "language": "en",
    "beam_size": 5,
    "vad_filter": True,
    "vad_parameters": {"min_silence_duration_ms": 500},
    "condition_on_previous_text": False,
    "temperature": 0.0,
}

NO_SPEECH_PROB_MAX = 0.6
REPEAT_TOKEN_MIN = 8
REPEAT_SHARE_MIN = 0.6
CONSECUTIVE_RUN_MIN = 6

_WORD_RE = re.compile(r"[A-Za-z']+")


def keep_whisper_segment(segment) -> bool:
    """Keep a Whisper segment only if it looks like real speech."""
    text = (getattr(segment, "text", None) or "").strip()
    if not text:
        return False
    nsp = getattr(segment, "no_speech_prob", None)
    if nsp is not None:
        try:
            if float(nsp) > NO_SPEECH_PROB_MAX:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _is_periodic_loop(words: list[str]) -> bool:
    """True when the whole clip is the same 1–4 word phrase repeated."""
    n = len(words)
    if n < 8:
        return False
    unique = set(words)
    if len(unique) <= 3 and (len(unique) / n) <= 0.25:
        return True
    max_period = min(4, n // 4)
    for period in range(1, max_period + 1):
        unit = words[:period]
        repeats = n // period
        if repeats >= 4 and words[: repeats * period] == unit * repeats:
            return True
    return False


def is_hallucinated_transcript(text: Optional[str]) -> bool:
    """True when the transcript is empty, non-lexical, or a compression loop."""
    if text is None:
        return True
    raw = str(text).strip()
    if not raw:
        return True
    words = _WORD_RE.findall(raw.lower())
    if not words:
        return True
    if _is_periodic_loop(words):
        return True
    _, count = Counter(words).most_common(1)[0]
    if count >= REPEAT_TOKEN_MIN and (count / len(words)) >= REPEAT_SHARE_MIN:
        return True
    run = 1
    max_run = 1
    for i in range(1, len(words)):
        if words[i] == words[i - 1]:
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 1
    if max_run >= CONSECUTIVE_RUN_MIN:
        return True
    return False


def finalize_live_transcript(parts: Iterable[str]) -> Optional[str]:
    """Join kept segments; return None if the result is junk (caller sends silence)."""
    text = " ".join(p.strip() for p in parts if p and str(p).strip()).strip()
    if not text or is_hallucinated_transcript(text):
        return None
    return text
