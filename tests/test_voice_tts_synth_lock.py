"""Piper synthesize must serialize — concurrent calls dropped middle TTS chunks.

voice/src is a separate src root from cove-core; load by file path (same pattern
as test_video_lifecycle.py).
"""
import importlib.util
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MOD_PATH = _ROOT / "voice" / "src" / "transports" / "piper_tts.py"
_spec = importlib.util.spec_from_file_location("voice_piper_tts", _MOD_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_piper_synth_lock_serializes_calls(monkeypatch):
    """Two overlapping synthesize calls must not interleave model.synthesize."""
    lock = threading.Lock()
    monkeypatch.setattr(mod, "_PIPER_SYNTH_LOCK", lock)

    transport = mod.PiperTTSTransport.__new__(mod.PiperTTSTransport)
    transport.is_initialized = True
    transport.sample_rate = 22050
    transport.speaker_id = None
    transport.length_scale = 1.0
    transport.noise_scale = 0.667
    transport.noise_w = 0.8

    active = 0
    max_active = 0
    gate = threading.Lock()

    class _Chunk:
        audio_int16_bytes = b"\x00\x01" * 8

    class _Model:
        def synthesize(self, text):
            nonlocal active, max_active
            with gate:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with gate:
                active -= 1
            yield _Chunk()

    transport.model = _Model()

    results = [None, None]
    errors = []

    def run(i, text):
        try:
            results[i] = transport.synthesize(text, return_wav=True)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=run, args=(0, "Hello one."))
    t2 = threading.Thread(target=run, args=(1, "Hello two."))
    t1.start()
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert not errors
    assert results[0] and results[1]
    # Without the lock max_active would be 2; with lock it must stay 1
    assert max_active == 1


def test_voice_js_documents_bounded_prefetch():
    """Guardrail: voice.js must not fire all-sentence parallel TTS again."""
    js = (_ROOT / "src/dashboard/static/js/voice.js").read_text()
    assert "TTS_PREFETCH" in js
    assert "prepareSpeakText" in js
    assert "sentences.map(s => fetchTTSChunk(s))" not in js
    assert "_PIPER_SYNTH_LOCK" in (_ROOT / "voice/src/transports/piper_tts.py").read_text()
