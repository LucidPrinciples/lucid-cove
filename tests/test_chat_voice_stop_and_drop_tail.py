"""Chat Voice: one Stop for the turn; drop leftover transcripts after send."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
MESSAGING_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text(encoding="utf-8")


def test_voice_keeps_stop_through_speaking_hides_send():
    send_fn = MESSAGING_JS.split("async function sendMessage()")[1].split(
        "async function cancelSend()"
    )[0]
    assert "const voiceSpeak" in send_fn
    speak = send_fn.split("if (voiceSpeak)")[1].split("} else if")[0]
    assert "stopBtn.style.display = ''" in speak
    assert "sendBtn.style.display = 'none'" in speak
    assert "micDuringSpeak.style.display = 'none'" in speak
    assert "await playTTS" in speak
    assert "voiceResumeListening" in speak
    # Must not hide Stop before speaking in Voice.
    before_speak = send_fn.split("const voiceSpeak")[0]
    assert "stopBtn.style.display = 'none'" not in before_speak


def test_stop_during_speech_silences_and_listens():
    cancel = MESSAGING_JS.split("async function cancelSend()")[1].split(
        "async function resetThread()"
    )[0]
    assert "ttsPlaying" in cancel
    assert "ttsStopPlayback" in cancel
    assert "voiceResumeListening" in cancel
    assert cancel.find("ttsPlaying") < cancel.find("api/chat/cancel")


def test_post_send_transcripts_are_dropped():
    assert "function voiceBeginSentTurn()" in VOICE_JS
    assert "function voiceDropIncomingTranscript()" in VOICE_JS
    assert "voiceHoldTranscripts" in VOICE_JS
    begin = VOICE_JS.split("function voiceBeginSentTurn()")[1].split(
        "function voiceResumeListening()"
    )[0]
    assert "voiceHoldTranscripts = true" in begin
    assert "voicePartial = ''" in begin
    assert "syncVoiceBox('')" in begin
    handle = VOICE_JS.split("function handleVoiceTranscript(text)")[1].split(
        "function getMicBtn"
    )[0]
    assert "voiceDropIncomingTranscript()" in handle
    ws = VOICE_JS.split("micWs.onmessage")[1].split("micWs.onclose")[0]
    assert "voiceDropIncomingTranscript()" in ws
    pause_block = MESSAGING_JS.split("typeof micPause === 'function')")[1].split(
        "const sendBtn = document.getElementById('chat-send');"
    )[0]
    assert "voiceBeginSentTurn" in pause_block
