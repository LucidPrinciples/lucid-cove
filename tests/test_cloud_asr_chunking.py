"""Cloud ASR long-audio chunking (no network)."""
from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "voice" / "src"))

from transports.cloud_asr import CloudASRTransport, _MAX_UPLOAD_BYTES  # noqa: E402


def _write_silence(path: str, seconds: int, fr: int = 16000) -> None:
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(b"\x00\x00" * fr * seconds)


def test_split_long_wav_into_chunks():
    t = CloudASRTransport(provider="groq", api_key="test-key")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "long.wav")
        _write_silence(wav, 25)
        pieces = t._split_wav(wav, td, 10)
        assert len(pieces) >= 2
        offs = [o for _, o in pieces]
        assert offs == sorted(offs)
        assert offs[0] == 0.0
        for p, _ in pieces:
            assert os.path.getsize(p) <= _MAX_UPLOAD_BYTES


def test_short_wav_no_split():
    t = CloudASRTransport(provider="groq", api_key="test-key")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "short.wav")
        _write_silence(wav, 5)
        pieces = t._split_wav(wav, td, 600)
        assert len(pieces) == 1
        assert pieces[0][0] == wav
        assert pieces[0][1] == 0.0


def test_missing_key_message(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    t = CloudASRTransport(provider="groq", api_key="")
    try:
        t.transcribe("/nope.wav")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "API key" in str(e)
