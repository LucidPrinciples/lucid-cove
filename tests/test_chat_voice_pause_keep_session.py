"""Chat Voice: a speech pause must not end the hands-free session."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")


def test_silence_closed_chunk_leaves_session_listening():
    """Pause-chunking may transcribe, but voiceActive stays true and we return to listening."""
    proc = VOICE_JS.split("micProcessor.onaudioprocess")[1].split("src.connect(micProcessor)")[0]
    assert "end_audio" in proc
    assert "voiceStop()" not in proc
    assert "voiceInterruptSession" not in proc
    handle = VOICE_JS.split("function handleVoiceTranscript(text)")[1].split(
        "function getMicBtn"
    )[0]
    assert "updateMicState('listening')" in handle
    assert "voiceActive = false" not in handle
    ws = VOICE_JS.split("function micConnectVoice()")[1].split(
        "function isHallucinatedTranscript"
    )[0]
    assert "updateMicState('listening')" in ws
    assert "voiceStop()" not in ws
    assert "voiceActive && !voiceFlushInFlight" in ws


def test_suspended_audio_context_is_not_an_immediate_hangup():
    bind = VOICE_JS.split("function bindVoiceInterruptWatchers")[1].split(
        "function voiceStart()"
    )[0]
    assert "ctx.state === 'interrupted'" in bind
    assert "ctx.resume()" in bind
    assert "mic ended" in bind
    assert "audio interrupted" in bind
    assert "app in background" in bind
    # Immediate hangup on suspended was the iPhone pause bug.
    assert "ctx.state === 'interrupted' || ctx.state === 'suspended'" not in bind
    interrupt = VOICE_JS.split("async function voiceInterruptSession")[1].split(
        "function unbindVoiceInterruptWatchers"
    )[0]
    assert "audio interrupted" in interrupt or "reason" in interrupt
    assert "Voice paused" in interrupt
