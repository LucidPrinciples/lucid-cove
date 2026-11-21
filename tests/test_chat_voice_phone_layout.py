"""Chat Voice at phone width: full-width message box, mic + Send on the row below."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
CHAT_CSS = (ROOT / "src/dashboard/static/css/chat.css").read_text(encoding="utf-8")


def test_voice_mode_marks_composer_for_phone_stack():
    switch = VOICE_JS.split("function switchChatMode(mode)")[1].split(
        "// Voice conversation mode"
    )[0]
    assert "chat-voice-composer" in switch
    assert "mode === 'voice'" in switch


def test_phone_voice_css_stacks_box_above_mic_and_send():
    mobile = CHAT_CSS.split("@media (max-width: 640px)")[1]
    assert ".chat-input-area.chat-voice-composer" in mobile
    assert "flex-wrap: wrap" in mobile
    assert "textarea#chat-input" in mobile
    assert "flex: 1 1 100%" in mobile
    assert "#mic-label" in mobile
    assert "overflow-wrap: anywhere" in mobile
    before_mobile = CHAT_CSS.split("@media (max-width: 640px)")[0]
    assert ".chat-input-area.chat-voice-composer" not in before_mobile
