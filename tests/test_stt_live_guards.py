"""Live Dictate/Voice STT guards — no Whisper load required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_MOD_PATH = ROOT / "voice" / "src" / "stt_guards.py"
_spec = importlib.util.spec_from_file_location("voice_stt_guards", _MOD_PATH)
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)


def test_live_whisper_kwargs_disable_conditioning():
    kw = guards.LIVE_WHISPER_TRANSCRIBE_KWARGS
    assert kw["condition_on_previous_text"] is False
    assert kw["temperature"] == 0.0
    assert kw["vad_filter"] is True


def test_drop_high_no_speech_segments():
    keep = SimpleNamespace(text="hello there", no_speech_prob=0.1)
    drop = SimpleNamespace(text="thank you", no_speech_prob=0.95)
    empty = SimpleNamespace(text="  ", no_speech_prob=0.0)
    assert guards.keep_whisper_segment(keep) is True
    assert guards.keep_whisper_segment(drop) is False
    assert guards.keep_whisper_segment(empty) is False


def test_repeat_loop_is_hallucination():
    loop = " ".join(["thank you"] * 12)
    assert guards.is_hallucinated_transcript(loop) is True
    run = "yes " * 8
    assert guards.is_hallucinated_transcript(run) is True
    assert guards.is_hallucinated_transcript("") is True
    assert guards.is_hallucinated_transcript("...") is True
    assert guards.is_hallucinated_transcript("Need to recode the Merrick payoff") is False


def test_finalize_returns_none_for_junk():
    assert guards.finalize_live_transcript(["thank you"] * 10) is None
    assert guards.finalize_live_transcript(["Need to recode the payoff"]) == (
        "Need to recode the payoff"
    )


def test_whisper_stt_uses_live_guards():
    src = (ROOT / "voice/src/transports/whisper_stt.py").read_text(encoding="utf-8")
    assert "LIVE_WHISPER_TRANSCRIBE_KWARGS" in src
    assert "finalize_live_transcript" in src
    assert "falling back to CPU small" in src


def test_ws_clears_buffer_on_start_recording():
    src = (ROOT / "voice/src/ws.py").read_text(encoding="utf-8")
    assert "start_recording" in src
    assert "buffer.clear()" in src


def test_voice_js_refuses_junk_autosend_and_reuses_socket():
    js = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
    assert "function isHallucinatedTranscript" in js
    assert "Didn't catch that" in js
    assert "Always create a fresh WebSocket per recording session" not in js
    assert "start_recording clears the server buffer" in js
