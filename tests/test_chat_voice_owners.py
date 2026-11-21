"""Chat Voice PR A: one mic owner, one session owner, run id, serialized taps."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
MESSAGING_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "src/dashboard/static/js/panels.js").read_text(encoding="utf-8")


def test_audio_session_type_only_in_session_owner():
    assert "async function setVoiceAudioSession(type)" in VOICE_JS
    assigns = [m.start() for m in re.finditer(r"navigator\.audioSession\.type\s*=", VOICE_JS)]
    assert len(assigns) == 1
    owner = VOICE_JS.split("async function setVoiceAudioSession(type)")[1].split(
        "function voiceWait"
    )[0]
    assert "navigator.audioSession.type = type" in owner
    assert "audioSession.type" not in MESSAGING_JS


def test_getusermedia_and_track_stop_only_in_voice_mic():
    assert "const voiceMic = {" in VOICE_JS
    owner = VOICE_JS.split("const voiceMic = {")[1].split("async function voiceFailToIdle")[0]
    calls = re.findall(r"getUserMedia\s*\(", VOICE_JS)
    assert len(calls) == 2
    assert owner.count("getUserMedia(") == 2
    # track.stop lives on the MediaStreamTrack inside voiceMic._teardown
    assert "t.stop()" in owner
    outside = VOICE_JS.split("const voiceMic = {")[0] + VOICE_JS.split(
        "async function voiceFailToIdle"
    )[1]
    assert "getUserMedia(" not in outside
    assert re.search(r"getTracks\(\)[^\n]*stop", outside) is None
    assert ".stop()" not in MESSAGING_JS or "ttsSource.stop" in MESSAGING_JS
    assert "getUserMedia(" not in MESSAGING_JS


def test_playback_run_id_checked_after_await():
    play = VOICE_JS.split("function startVoicePlayback")[1].split(
        "async function voicePreparePlayback"
    )[0]
    assert "ttsStopPlayback()" in play
    assert "const myId = ttsRunId" in play
    assert "if (myId !== ttsRunId)" in play
    assert play.count("myId !== ttsRunId") >= 3
    assert "ttsCancelled = false" in play


def test_session_play_and_record_before_getusermedia():
    open_fn = VOICE_JS.split("async open()")[1].split("async pause()")[0]
    assert open_fn.find("setVoiceAudioSession('play-and-record')") < open_fn.find(
        "getUserMedia"
    )
    assert "VOICE_GUM_RETRY_MS = 300" in VOICE_JS
    prep = VOICE_JS.split("async function voicePreparePlayback()")[1].split(
        "function syncVoiceBox"
    )[0]
    assert "voiceMic.pause()" in prep
    assert "setVoiceAudioSession('playback')" in prep
    assert prep.find("voiceMic.pause()") < prep.find("setVoiceAudioSession('playback')")


def test_listen_while_thinking_or_speaking_is_ignored():
    listen = VOICE_JS.split("function toggleListenReply")[1]
    assert "voicePhase === 'thinking' || voicePhase === 'speaking'" in listen
    assert "ignored Listen during" in listen
    assert "playTTS(text, { force: true })" in listen
    assert "voiceListenResume = true" in listen


def test_tap_during_transition_is_ignored():
    assert "ignored tap during transition" in VOICE_JS
    toggle = VOICE_JS.split("function micToggle()")[1].split("function toggleListenReply")[0]
    assert "voiceTransition" in toggle
    assert "ignored tap during transition" in toggle
    # Stop still wins
    stop = VOICE_JS.split("async function voiceStop()")[1].split("function micPause()")[0]
    assert "Stop always wins" in stop
    assert "setVoicePhase('idle'" in stop


def test_voice_log_and_copy_control():
    assert "VOICE_LOG_MAX = 30" in VOICE_JS
    assert "function copyVoiceLog()" in VOICE_JS
    assert "Copy voice log" in VOICE_JS
    assert "copyVoiceLog()" in PANELS_JS
    assert 'id="voice-log-help"' in PANELS_JS
    assert "Copy voice log" in MESSAGING_JS


def test_no_wake_lock_in_this_pr():
    assert "wakeLock" not in VOICE_JS
    assert "wake-loop" not in VOICE_JS
    assert "holdWakeLock" not in VOICE_JS
