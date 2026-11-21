"""Chat Voice mode: keep heard words, Over only at the end, park on interrupt."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
MESSAGING_JS = (ROOT / "src/dashboard/static/js/messaging.js").read_text(encoding="utf-8")

FILLERS = {"um", "uh", "er", "ah", "please", "yeah", "okay", "ok"}


def _heard_words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def strip_voice_end_trigger(text: str) -> str | None:
    """Python mirror of the Voice hands-free send rule.

    "Over" / "send it" fire only at the end of what was heard. Mid-sentence
    must not fire ("come over here", "it's over there"). Trailing punctuation
    and one filler after the trigger are allowed. Misses use visible Send.
    """
    words = _heard_words(text)
    if not words:
        return None

    def match_at(end_idx: int) -> int | None:
        if end_idx < 0:
            return None
        w = words[end_idx]
        if w in ("over", "sendit"):
            return end_idx
        if w == "it" and end_idx > 0 and words[end_idx - 1] == "send":
            return end_idx - 1
        return None

    start = match_at(len(words) - 1)
    if start is None and len(words) >= 2 and words[-1] in FILLERS:
        start = match_at(len(words) - 2)
    if start is None:
        return None
    return " ".join(words[:start]).strip()


def test_over_only_at_end_of_heard_text():
    assert strip_voice_end_trigger("come over here") is None
    assert strip_voice_end_trigger("it's over there") is None
    assert strip_voice_end_trigger("the oven is over there") is None
    assert strip_voice_end_trigger("please come over later") is None

    assert strip_voice_end_trigger("what should we have for dinner over") == (
        "what should we have for dinner"
    )
    assert strip_voice_end_trigger("what should we have for dinner over.") == (
        "what should we have for dinner"
    )
    assert strip_voice_end_trigger("what should we have for dinner, over!") == (
        "what should we have for dinner"
    )
    assert strip_voice_end_trigger("what should we have for dinner over um") == (
        "what should we have for dinner"
    )
    assert strip_voice_end_trigger("Over") == ""
    assert strip_voice_end_trigger("send it") == ""
    assert strip_voice_end_trigger("tell them send it please") == "tell them"


def test_voice_js_keeps_over_at_end_and_parks_draft():
    assert "function stripVoiceEndTrigger" in VOICE_JS
    assert "function parkVoiceDraft" in VOICE_JS
    assert "function voiceInterruptSession" in VOICE_JS
    assert "come over here" in VOICE_JS
    assert "it's over there" in VOICE_JS
    assert "visibilitychange" in VOICE_JS
    assert "pagehide" in VOICE_JS
    assert "addEventListener('ended'" in VOICE_JS
    assert "interrupted" in VOICE_JS
    assert "Do not auto-resume" in VOICE_JS
    assert "voiceIgnoringMicLoss" in VOICE_JS
    assert "mode === 'voice'" in VOICE_JS
    # Stop copies heard words into the box instead of wiping them.
    stop = VOICE_JS.split("async function voiceStop()")[1].split("function micPause()")[0]
    assert "parkVoiceDraft()" in stop
    assert "voiceFlushOpenChunk" in stop
    assert "voicePartial = '';" not in stop.split("parkVoiceDraft()")[0]
    # Intentional mic teardown must not look like a phone call.
    pause = VOICE_JS.split("function micPause()")[1].split("function updateMicState")[0]
    assert "unbindVoiceInterruptWatchers()" in pause
    assert "voiceIgnoringMicLoss = true" in pause


def test_messaging_keeps_send_visible_in_voice_mode():
    assert "chatMode === 'voice'" in MESSAGING_JS
    restore = MESSAGING_JS.split("function _restoreChatChromeAfterTurn()")[1].split(
        "async function checkChatProcessing()"
    )[0]
    assert "sendBtn.style.display = ''" in restore
    send = MESSAGING_JS.split("async function sendMessage()")[1].split(
        "const sendBtn = document.getElementById('chat-send');"
    )[0]
    assert "voicePartial" in send
    assert "micPause" in send
