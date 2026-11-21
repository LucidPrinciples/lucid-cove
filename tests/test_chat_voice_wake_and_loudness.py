"""Chat Voice: keep the screen on during a session; match Listen and reply loudness."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "src/dashboard/static/index.html").read_text(encoding="utf-8")
WAKE_MP4 = ROOT / "src/dashboard/static/wake-loop.mp4"


def test_voice_wake_lock_held_for_session_only():
    assert "async function holdWakeLock()" in VOICE_JS
    assert "async function releaseWake()" in VOICE_JS
    assert "function startWakeVideo()" in VOICE_JS
    assert "function stopWakeVideo()" in VOICE_JS
    assert 'navigator.wakeLock.request("screen")' in VOICE_JS or "navigator.wakeLock.request('screen')" in VOICE_JS
    assert 'document.getElementById("voice-wake")' in VOICE_JS or "document.getElementById('voice-wake')" in VOICE_JS
    hold = VOICE_JS.split("async function holdWakeLock()")[1].split("function startWakeVideo()")[0]
    assert "chatMode !== 'voice'" in hold
    assert "startWakeVideo()" in hold
    start = VOICE_JS.split("function voiceStart()")[1].split("async function voiceStop()")[0]
    assert "holdWakeLock()" in start
    assert "setAudioSession('play-and-record')" in start
    stop = VOICE_JS.split("async function voiceStop()")[1].split("function micPause()")[0]
    assert "releaseWake()" in stop
    switch = VOICE_JS.split("function switchChatMode(mode)")[1].split("function restoreChatMode()")[0]
    assert "releaseWake()" in switch
    vis = VOICE_JS.split("document.addEventListener('visibilitychange'")[1].split("});")[0]
    assert "document.visibilityState === 'visible'" in vis
    assert "chatMode === 'voice'" in vis
    assert "voiceActive" in vis
    assert "holdWakeLock()" in vis
    listening = VOICE_JS.split("case 'listening':")[1].split("case 'transcribing':")[0]
    thinking = VOICE_JS.split("case 'thinking':")[1].split("case 'speaking':")[0]
    speaking = VOICE_JS.split("case 'speaking':")[1].split("case 'unavailable':")[0]
    assert "holdWakeLock()" in listening
    assert "holdWakeLock()" in thinking
    assert "holdWakeLock()" in speaking
    assert 'id="voice-wake"' in INDEX_HTML
    assert 'src="/static/wake-loop.mp4"' in INDEX_HTML
    assert WAKE_MP4.is_file() and WAKE_MP4.stat().st_size > 100


def test_listen_and_reply_share_playback_session():
    assert "function setAudioSession(type)" in VOICE_JS
    assert "navigator.audioSession" in VOICE_JS
    play = VOICE_JS.split("function playTTS(text, opts)")[1].split("function syncVoiceBox")[0]
    assert "setAudioSession('playback')" in play
    pause = VOICE_JS.split("function micPause()")[1].split("async function voiceBeforeSend()")[0]
    assert "setAudioSession('playback')" in pause
    listen = VOICE_JS.split("function toggleListenReply")[1]
    assert "micPause()" in listen
    assert "function voiceResumeAfterListen()" in VOICE_JS
    assert "voiceResumeAfterListen()" in listen
    resume = VOICE_JS.split("function voiceResumeAfterListen()")[1].split("function toggleListenReply")[0]
    assert "setAudioSession('play-and-record')" in resume
    assert "micStartContinuous()" in resume
    mic = VOICE_JS.split("async function micStartContinuous()")[1].split(
        "function _setupScriptProcessorContinuous"
    )[0]
    assert "setAudioSession('play-and-record')" in mic
    assert "getUserMedia" in mic


def test_listen_pause_flag_survives_consecutive_listens():
    resume = VOICE_JS.split("function voiceResumeAfterListen()")[1].split(
        "async function toggleListenReply"
    )[0]
    assert resume.find("ttsPlaying") < resume.find("_listenPausedMic = false")
    assert resume.find("if (!_listenPausedMic)") < resume.find("ttsPlaying")
    listen = VOICE_JS.split("async function toggleListenReply")[1]
    assert "micRecording || _listenPausedMic" in listen
    assert "_listenPausedMic = true" in listen
    assert "_listenPausedMic = !!(voiceActive && micRecording)" not in listen


def test_listen_flushes_open_chunk_before_mic_pause():
    listen = VOICE_JS.split("async function toggleListenReply")[1]
    assert "speechSeen || silenceStart || voiceChunkPending" in listen
    assert "voiceFlushOpenChunk" in listen
    assert "absorbVoiceTail" in listen
    assert listen.find("voiceFlushOpenChunk") < listen.find("micPause()")
    assert listen.find("absorbVoiceTail") < listen.find("micPause()")
