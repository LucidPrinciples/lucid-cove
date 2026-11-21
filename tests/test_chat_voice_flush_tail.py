"""Chat Voice: flush the open audio chunk before park or send."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
MESSAGING_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text(encoding="utf-8")


def test_flush_sends_end_audio_and_waits():
    assert "VOICE_FLUSH_TIMEOUT_MS = 8000" in VOICE_JS
    assert "async function voiceFlushOpenChunk" in VOICE_JS
    assert "async function voiceBeforeSend" in VOICE_JS
    flush = VOICE_JS.split("async function voiceFlushOpenChunk")[1].split(
        "function absorbVoiceTail"
    )[0]
    assert "end_audio" in flush
    assert "transcribe" in flush
    assert "voiceWaitForFlushTranscript" in flush
    stop = VOICE_JS.split("async function voiceStop()")[1].split("function micPause()")[0]
    assert "voiceStopCaptureKeepSocket" in stop
    assert "voiceFlushOpenChunk" in stop
    assert "parkVoiceDraft()" in stop
    send = VOICE_JS.split("async function voiceBeforeSend()")[1].split(
        "function updateMicState"
    )[0]
    assert "voiceFlushOpenChunk" in send
    interrupt = VOICE_JS.split("async function voiceInterruptSession")[1].split(
        "function unbindVoiceInterruptWatchers"
    )[0]
    assert "await voiceStop()" in interrupt
    assert "nothing was captured" in interrupt
    assert "send or edit" in interrupt


def test_voice_hides_duplicate_partial_preview():
    fn = VOICE_JS.split("function updatePartialDisplay(text)")[1].split(
        "async function micStartContinuous"
    )[0]
    assert "chatMode === 'voice'" in fn
    assert "display = 'none'" in fn


def test_send_message_flushes_before_taking_box():
    send = MESSAGING_JS.split("async function sendMessage()")[1].split(
        "const sendBtn = document.getElementById('chat-send');"
    )[0]
    assert "voiceBeforeSend" in send
    assert send.find("voiceBeforeSend") < send.find("input.value.trim()")


def test_over_rule_unchanged_in_flush_branch():
    assert "come over here" in VOICE_JS
    assert "it's over there" in VOICE_JS
    assert "function stripVoiceEndTrigger" in VOICE_JS


def test_second_send_during_flush_is_ignored():
    send = MESSAGING_JS.split("async function sendMessage()")[1]
    head = send.split("const sendBtn = document.getElementById('chat-send');")[0]
    assert "voiceFlushInFlight" in head
    assert head.find("voiceFlushInFlight") < head.find("voiceBeforeSend")
    after = head.split("await voiceBeforeSend();")[1]
    assert "if (sending) return;" in after.split("const input")[0]
    before_send = VOICE_JS.split("async function voiceBeforeSend()")[1].split(
        "function updateMicState"
    )[0]
    assert "sendBtn.disabled = true" in before_send
