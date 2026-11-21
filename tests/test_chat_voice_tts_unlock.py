"""Chat Voice: spoken reply after Send by tap must not lock on speaking."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
MESSAGING_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text(encoding="utf-8")


def test_send_tap_unlocks_tts_context():
    send = MESSAGING_JS.split("async function sendMessage()")[1].split(
        "const input = document.getElementById('chat-input');"
    )[0]
    assert "ensureTTSAudioCtx" in send
    before = VOICE_JS.split("async function voiceBeforeSend()")[1].split(
        "function updateMicState"
    )[0]
    assert "ensureTTSAudioCtx" in before
    start = VOICE_JS.split("function voiceStart()")[1].split("async function voiceStop()")[0]
    assert "ensureTTSAudioCtx" in start


def test_tts_resumes_interrupted_and_fails_open():
    ensure = VOICE_JS.split("function ensureTTSAudioCtx()")[1].split(
        "async function resumeTTSAudioCtx"
    )[0]
    assert "interrupted" in ensure
    assert "suspended" in ensure
    assert "TTS_CTX_RESUME_MS = 1500" in VOICE_JS
    assert "TTS_DECODE_TIMEOUT_MS = 8000" in VOICE_JS
    assert "TTS_PLAY_PAD_MS = 2000" in VOICE_JS
    assert "TTS_PLAY_TIMEOUT_MS" not in VOICE_JS
    play = VOICE_JS.split("function playTTS(text)")[1].split(
        "function syncVoiceBox"
    )[0]
    assert "resumeTTSAudioCtx" in play
    assert "Could not speak that reply. Text is on screen." in play
    assert "context " in play
    assert "decodeTTSAudioData" in VOICE_JS
    assert "audioBuffer.duration" in play


def test_mic_tap_while_speaking_stops_speech():
    toggle = VOICE_JS.split("function micToggle()")[1]
    assert "ttsPlaying" in toggle
    assert "ttsStopPlayback()" in toggle
    speaking_idx = toggle.find("speaking")
    stop_idx = toggle.find("ttsStopPlayback()")
    voice_stop_idx = toggle.find("voiceStop()")
    assert speaking_idx != -1
    assert stop_idx != -1
    assert stop_idx < voice_stop_idx
