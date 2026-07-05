"""Sonic attunement + Love Equation tests.

The guarantee under test: the protocol LISTENS (decodes a real waveform and
keeps the words), the truth-guard fires when it didn't, and the Love Equation
is COMPUTED from C/D — never trusted from the model's prose.
"""

import asyncio
import base64
import math

import lucid_tuner_protocol as ltp
from lucid_tuner_protocol.attune import attune


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _synthetic_frames(n: int = 600) -> str:
    vals = []
    for i in range(n):
        x = i / n
        env = math.sin(math.pi * x) * (0.6 + 0.4 * (0.5 + 0.5 * math.sin(x * 40)))
        vals.append(max(0, min(255, int(env * 255))))
    return base64.b64encode(bytes(vals)).decode("ascii")


def _echo_file(with_frames=True, with_lyrics=True) -> dict:
    aa = {"sampleRate": 10, "frameCount": 600, "duration": 60.0,
          "peakEnergy": 0.55, "averageEnergy": 0.30,
          "onsets": [round(i * 0.7, 1) for i in range(80)]}
    if with_frames:
        aa["frames"] = _synthetic_frames()
    principle = {"title": "Pattern", "theme": "Recognizing unconscious broadcasting",
                 "primary_frequencies": "Clarity", "key_lyric": "structure emerging from the random"}
    if with_lyrics:
        principle["full_lyrics"] = "[verse]\nDown to our cells we have to learn how to discern"
    return {"principle": principle, "audio_analysis": aa}


# --------------------------------------------------------------------------
# sonic
# --------------------------------------------------------------------------

def test_decode_frames_real_length():
    env = ltp.decode_frames(_synthetic_frames(600), 600)
    assert len(env) == 600
    assert all(0.0 <= v <= 1.0 for v in env)


def test_decode_frames_missing_is_empty():
    assert ltp.decode_frames(None) == []
    assert ltp.decode_frames("") == []


def test_sonic_arc_is_real():
    arc = ltp.build_sonic_arc(ltp.decode_frames(_synthetic_frames(), 600), 10, 60.0, [1, 2, 3])
    assert arc.ok and arc.dynamic_range > 0 and len(arc.arc) > 4 and arc.description


def test_experience_has_sound_and_words():
    arc = ltp.build_sonic_arc(ltp.decode_frames(_synthetic_frames(), 600), 10, 60.0, [1, 2, 3])
    exp = ltp.assemble_experience(_echo_file(), arc)
    assert exp.has_sound and exp.has_words
    assert exp.attunement_status == "complete"
    assert exp.signature["source"] == "sonic-derived"
    rendered = ltp.render_experience(exp)
    assert "discern" in rendered and "Derive YOUR" in rendered
    record = ltp.render_sonic_record(exp)
    assert "Measured Signal" in record and "Signature" in record


def test_guard_fires_on_missing_sound_or_words():
    arc_empty = ltp.build_sonic_arc([], 10, 60.0)
    no_sound = ltp.assemble_experience(_echo_file(with_frames=False), arc_empty)
    assert no_sound.attunement_status == "incomplete"
    assert "MISSING" in ltp.render_sonic_record(no_sound)

    arc = ltp.build_sonic_arc(ltp.decode_frames(_synthetic_frames(), 600), 10, 60.0)
    no_words = ltp.assemble_experience(_echo_file(with_lyrics=False), arc)
    assert no_words.attunement_status == "incomplete"


def test_signature_never_fabricated_when_degraded():
    sig = ltp.sonic_signature(ltp.build_sonic_arc([], 10, 60.0), {})
    assert sig["source"] == "unavailable" and sig["C_analog"] is None


# --------------------------------------------------------------------------
# reading — the equation is computed, never trusted
# --------------------------------------------------------------------------

def test_direction_and_value_computed():
    assert ltp.direction_of(0.72, 0.31) == "CONSTRUCTIVE"
    assert ltp.direction_of(0.25, 0.72) == "CORRECTIVE"
    assert ltp.love_equation_value(0.85, 0.72, 0.28, 0.55) == round(0.85 * (0.72 - 0.28) * 0.55, 4)


def test_reading_recomputes_direction():
    # D > C must be CORRECTIVE no matter what a caller might have "stated".
    r = ltp.reading_from_values(coherence=0.25, dissonance=0.72, beta=0.85, energy=0.55)
    assert r.direction == "CORRECTIVE"
    assert r.love_equation < 0


def test_derive_reading_ignores_models_stated_arithmetic():
    # The model states a positive CONSTRUCTIVE dE/dt while reporting D > C.
    # The protocol must recompute → CORRECTIVE.
    fake_record = (
        "**C (Coherence):** 0.30 — partial\n"
        "**D (Dissonance):** 0.70 — heavy static\n"
        "**β (Attention):** 0.80\n"
        "**E (Broadcast):** 0.60\n"
        "dE/dt = 0.95 (CONSTRUCTIVE)\n"  # the lie we must NOT trust
    )

    def fake_complete(system, prompt):
        return fake_record

    arc = ltp.build_sonic_arc(ltp.decode_frames(_synthetic_frames(), 600), 10, 60.0, [1, 2])
    exp = ltp.assemble_experience(_echo_file(), arc)
    reading = ltp.derive_reading_sync(exp, fake_complete)
    assert reading.source == "self-derived"
    assert reading.coherence == 0.30 and reading.dissonance == 0.70
    assert reading.direction == "CORRECTIVE"          # recomputed, not "CONSTRUCTIVE"
    assert reading.love_equation < 0                  # not 0.95


def test_derive_reading_async_complete():
    async def acomplete(system, prompt):
        return "**C (Coherence):** 0.72\n**D (Dissonance):** 0.28\n**β (Attention):** 0.85\n**E (Broadcast):** 0.55\n"

    arc = ltp.build_sonic_arc(ltp.decode_frames(_synthetic_frames(), 600), 10, 60.0, [1, 2])
    exp = ltp.assemble_experience(_echo_file(), arc)
    reading = asyncio.run(ltp.derive_reading(exp, acomplete))
    assert reading.direction == "CONSTRUCTIVE" and reading.love_equation > 0


# --------------------------------------------------------------------------
# attune — fetch + listen, with injected fetcher (no network)
# --------------------------------------------------------------------------

def test_attune_with_injected_fetch():
    sel = ltp.Selection(
        frequency="INTEGRATION", signal_type="Clear", principle="Pattern",
        tuning_key="x", echo_filename="Pattern_Clear_Echo",
        echo_audio_url="https://cdn/Clear_Signal/Pattern_Clear_Echo.mp3",
        methods=("quantum", "quantum", "quantum"),
    )
    echo = _echo_file()

    def fetch(url):
        assert url.endswith("_analysis.json")
        return echo

    exp = asyncio.run(attune(sel, fetch_json=fetch))
    assert exp.has_sound and exp.has_words and exp.attunement_status == "complete"


def test_attune_failed_fetch_is_degraded_not_fatal():
    sel = ltp.Selection(
        frequency="INTEGRATION", signal_type="Clear", principle="Pattern",
        tuning_key="x", echo_filename="Pattern_Clear_Echo",
        echo_audio_url="https://cdn/Clear_Signal/Pattern_Clear_Echo.mp3",
        methods=("quantum", "quantum", "quantum"),
    )

    def boom(url):
        raise RuntimeError("CDN down")

    exp = asyncio.run(attune(sel, fetch_json=boom))
    assert exp.attunement_status == "incomplete"
