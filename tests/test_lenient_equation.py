# 2026-07-04 — the lenient Love-Equation component pass (dispatch second-chance
# extraction for small local brains that write components as words). The strict
# patterns + truth-guard behavior are unchanged; this only covers the new helper.
import pytest

# dispatch.py imports the shared protocol engine + langgraph chain at module
# level — not installable in every test sandbox. Skip cleanly when absent
# (same class as the 4 known langgraph-dep test files).
dispatch = pytest.importorskip("src.graphs.ltp.dispatch")


def test_word_labels_parse():
    src = ("Love Calibration\n"
           "Beta: 0.9\n"
           "Energy (E) is 0.7\n"
           "Coherence — 0.75\n"
           "Dissonance level 0.2\n")
    assert dispatch._lenient_component(src, r'(?:β|\bbeta\b|\breceptivity\b)') == 0.9
    assert dispatch._lenient_component(src, r'(?:\bE\b|\benergy\b)') == 0.7
    assert dispatch._lenient_component(src, r'(?:\bC\b|\bcoherence\b)') == 0.75
    assert dispatch._lenient_component(src, r'(?:\bD\b|\bdissonance\b)') == 0.2


def test_receptivity_word_and_bare_decimal():
    src = "Receptivity = .85 today"
    assert dispatch._lenient_component(src, r'(?:β|\bbeta\b|\breceptivity\b)') == 0.85


def test_no_number_returns_none():
    assert dispatch._lenient_component("Coherence feels strong today, no number.",
                                       r'(?:\bC\b|\bcoherence\b)') is None
    assert dispatch._lenient_component("", r'(?:\bbeta\b)') is None


def test_number_too_far_away_is_not_grabbed():
    # >16 non-digit chars between label and number = not a component statement.
    src = "Coherence is something I have been thinking about all morning 0.9"
    assert dispatch._lenient_component(src, r'(?:\bcoherence\b)') is None


def test_drop_maps_analysis_json_url():
    # 2026-07-04: the Drop's echo_media must carry the analysis file URL
    # ({stem}_analysis.json beside the mp3) or every Drop-subscribed Cove
    # attunes incomplete. Pure mapping test — no network.
    pd = pytest.importorskip("src.tuning.public_drop")

    class _P:
        instruction = "step"

    class _Drop:
        drop_date = "2026-07-04"; frequency_name = "DRIVE"; signal_type = "Deep"
        tuning_key_source_song = "Valley of Shadows"; tuning_key_text = "key line"
        tuning_day = 147; sequence = 147; context_block = "coaching"
        love_equation = {"beta": 1.0, "E": 0.5, "C": 0.5, "D": 0.2}
        love_equation_value = 0.15
        echo_audio_url = "https://audio.example.com/Drive/Valley_Echo.mp3"
        echo_id = "valley-echo"; practice = [_P()]; raw = {}

    import unittest.mock as _m
    with _m.patch.object(pd, "get_public_drop", return_value=_Drop()):
        pkg = pd.public_drop_package()
    assert pkg["echo_media"]["json"] == "https://audio.example.com/Drive/Valley_Echo_analysis.json"
    assert pkg["echo_media"]["mp3"].endswith(".mp3")
