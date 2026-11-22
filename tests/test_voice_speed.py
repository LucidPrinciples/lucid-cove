"""Speaking speed: one setting per Presence, applied by Piper (pitch unchanged).

The voice server and the dashboard proxy are checked without importing fastapi or piper:
the small functions are pulled out of the source with ast and run directly.
"""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TTS_ROUTE = (ROOT / "voice/src/routes/tts.py").read_text(encoding="utf-8")
PIPER = (ROOT / "voice/src/transports/piper_tts.py").read_text(encoding="utf-8")
PROXY = (ROOT / "src/dashboard/routes/voice_proxy.py").read_text(encoding="utf-8")
VOICE_JS = (ROOT / "src/dashboard/static/js/voice.js").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "src/dashboard/static/js/settings-voice.js").read_text(encoding="utf-8")


def _func_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found")


def test_clamp_speed_bounds_and_nonsense():
    ns = {"SPEED_MIN": 0.8, "SPEED_MAX": 1.3}
    exec(_func_source(TTS_ROUTE, "clamp_speed"), ns)
    clamp = ns["clamp_speed"]
    assert clamp(1.2) == 1.2
    assert clamp("1.1") == 1.1
    assert clamp(5) == 1.3
    assert clamp(0.2) == 0.8
    assert clamp(None) == 1.0
    assert clamp("fast") == 1.0
    assert clamp(0) == 1.0
    assert clamp(float("nan")) == 1.0


def test_normal_speed_takes_the_old_path_in_the_route():
    assert "if speed == 1.0:" in TTS_ROUTE
    assert "await loop.run_in_executor(None, tts.synthesize, text)" in TTS_ROUTE
    assert "tts.synthesize(text, speed=speed)" in TTS_ROUTE


def test_speed_config_uses_length_scale_and_skips_normal_speed(monkeypatch):
    import textwrap

    class FakeConfig:
        def __init__(self, **kw):
            self.kw = kw

    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(SynthesisConfig=FakeConfig))
    ns = {"logger": types.SimpleNamespace(warning=lambda *a, **k: None)}
    exec(textwrap.dedent(_func_source(PIPER, "_speed_config")), ns)
    tts = types.SimpleNamespace(length_scale=1.0, noise_scale=0.667, noise_w=0.8, speaker_id=None)
    speed_config = ns["_speed_config"]
    assert speed_config(tts, 1.0) is None
    assert speed_config(tts, "abc") is None
    assert speed_config(tts, 0) is None
    cfg = speed_config(tts, 1.25)
    assert abs(cfg.kw["length_scale"] - 0.8) < 1e-9  # faster speech = shorter phonemes
    assert cfg.kw["noise_scale"] == 0.667 and cfg.kw["noise_w_scale"] == 0.8
    assert "speaker_id" not in cfg.kw
    tts.speaker_id = 3
    assert speed_config(tts, 0.8).kw["speaker_id"] == 3


def test_synthesize_falls_back_to_normal_speed_when_piper_rejects_the_config():
    body = _func_source(PIPER, "synthesize")
    assert "speed: float = 1.0" in body
    assert "self.model.synthesize(text, syn_config=syn_config)" in body
    assert "except TypeError:" in body
    assert body.count("self.model.synthesize(text)") == 2  # normal path + fallback


def test_proxy_forwards_only_a_sane_speed():
    assert 'payload["speed"] = speed' in PROXY
    assert "0.5 <= speed <= 2.0" in PROXY


def test_page_sends_the_saved_speed_and_settings_has_one_slider():
    assert "function voiceSpeedSetting()" in VOICE_JS
    assert "if (speed !== 1.0) payload.speed = speed;" in VOICE_JS
    assert 'id="voice-speed"' in SETTINGS_JS
    assert SETTINGS_JS.count('id="voice-speed"') == 1
    assert "JSON.stringify({ voice_speed: text })" in SETTINGS_JS
    assert 'min="0.8" max="1.3" step="0.1"' in SETTINGS_JS
