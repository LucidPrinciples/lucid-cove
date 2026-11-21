"""Chat: Text/Voice switch, Listen on replies, Voice listening hint."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
MESSAGING_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "src/dashboard/static/js/panels.js").read_text(encoding="utf-8")
CHAT_CSS = (ROOT / "src/dashboard/static/css/chat.css").read_text(encoding="utf-8")
CHANNELS_PY = (ROOT / "src/graphs/channels.py").read_text(encoding="utf-8")


def test_chat_modes_are_text_and_voice_only():
    chat_panel = PANELS_JS.split("case 'chat':")[1].split("case 'tuning':")[0]
    assert 'id="mode-type">Text</button>' in chat_panel
    assert 'id="mode-voice">Voice</button>' in chat_panel
    assert "mode-dictate" not in chat_panel
    assert ">Dictate<" not in chat_panel
    assert "voice-listen-hint" in chat_panel
    assert "Say Over or tap Send. Tap the mic to stop and edit." in chat_panel
    assert "function normalizeChatMode" in VOICE_JS
    assert "mode === 'voice' ? 'voice' : 'type'" in VOICE_JS
    assert "function micStart(" not in VOICE_JS
    assert "function micConnect(" not in VOICE_JS
    assert "mode-dictate" not in VOICE_JS
    assert "Input Mode: Dictate" not in CHANNELS_PY


def test_listen_button_on_agent_text_replies():
    add = MESSAGING_JS.split("function addMessage(")[1].split("function formatMessage(")[0]
    assert "msg-listen" in add
    assert "Listen" in add
    assert "toggleListenReply" in add
    assert "role !== 'user'" in add
    assert "function toggleListenReply" in VOICE_JS
    listen = VOICE_JS.split("function toggleListenReply")[1]
    assert "ensureTTSAudioCtx()" in listen
    assert "playTTS(text, { force: true })" in listen
    assert "ttsStopPlayback()" in listen
    assert "function playTTS(text, opts)" in VOICE_JS
    play = VOICE_JS.split("function playTTS(text, opts)")[1].split("function syncVoiceBox")[0]
    assert "opts.force" in play
    assert ".msg-listen" in CHAT_CSS


def test_voice_listening_hint_lists_three_ways():
    assert "Say Over or tap Send. Tap the mic to stop and edit." in PANELS_JS
    assert "function setVoiceListenHint" in VOICE_JS
    listening = VOICE_JS.split("case 'listening':")[1].split("case 'transcribing':")[0]
    assert "setVoiceListenHint(true)" in listening
    assert "Listening..." in listening
    assert "say 'Over'" not in listening
    idle = VOICE_JS.split("case 'idle':")[1].split("case 'listening':")[0]
    assert "setVoiceListenHint(false)" in idle
    assert ".voice-listen-hint" in CHAT_CSS
