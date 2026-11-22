"""Chat Voice: one playback at a time, and Listen inside a live Voice session.

Runs tests/voice_listen_harness.js, which loads voice.js with stubbed browser APIs and
drives it (playback takeover, Listen mid-sentence, Listen A then B, Stop, ignored taps).
Skipped when node is not installed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "voice_listen_harness.js"
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_voice_listen_harness_passes():
    run = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1000:]
    assert "HARNESS_OK" in run.stdout


def test_listen_never_sets_the_ios_audio_session():
    # #521 broke Voice by switching navigator.audioSession.type around capture.
    # Listen matches a spoken reply by closing the mic first; it must not touch the session type.
    assert "audioSession" not in VOICE_JS


def test_playback_has_a_run_id():
    play = VOICE_JS.split("function playTTS(")[1].split("// ── Voice conversation lifecycle")[0]
    assert "const myRun = ++ttsRunId;" in play
    assert "if (!live())" in play
    assert "const queue = new Array(sentences.length);" in play
