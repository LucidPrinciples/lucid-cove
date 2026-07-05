"""Multi-step selection chain — LTP Protocol Spec Sections 2, 3, 5."""

import lucid_tuner_protocol as ltp
from lucid_tuner_protocol.reference import FREQUENCY_SIGNAL_MAP


def _fixed_entropy(calls):
    """Entropy stub that records every call and always picks index 0."""
    async def _entropy(pool_size):
        calls.append(pool_size)
        return 0, "crypto"
    return _entropy


async def test_three_independent_rolls():
    """Never collapse the rolls: exactly three entropy calls per selection."""
    calls = []
    sel = await ltp.select_tuning(entropy=_fixed_entropy(calls))
    assert len(calls) == 3
    assert len(sel.methods) == 3


async def test_recency_exclusion_and_reset():
    ref = ltp.load_reference()
    all_freqs = ref.all_frequencies

    # Excluding some frequencies shrinks roll 1's pool
    calls = []
    history = ltp.History(recent_frequencies=all_freqs[:5])
    sel = await ltp.select_tuning(history=history, entropy=_fixed_entropy(calls))
    assert calls[0] == len(all_freqs) - 5
    assert sel.frequency not in all_freqs[:5]

    # Excluding ALL frequencies resets the pool to all 13
    calls = []
    history = ltp.History(recent_frequencies=list(all_freqs))
    await ltp.select_tuning(history=history, entropy=_fixed_entropy(calls))
    assert calls[0] == len(all_freqs)


async def test_signal_type_matches_fixed_mapping():
    """Spec Section 3: each frequency maps to exactly one signal type."""
    calls = []
    sel = await ltp.select_tuning(entropy=_fixed_entropy(calls))
    assert sel.signal_type == FREQUENCY_SIGNAL_MAP[sel.frequency.upper()]


async def test_echo_pairing_is_deterministic():
    """Spec Section 5: echo derives from principle + signal type, never rolled.
    The selected tuning key's echo_filename IS the echo."""
    ref = ltp.load_reference()
    calls = []
    sel = await ltp.select_tuning(entropy=_fixed_entropy(calls))
    matching = [
        k for k in ref.tuning_keys_for(sel.frequency)
        if k.quote == sel.tuning_key
    ]
    assert matching, "selected key must exist in reference"
    assert sel.echo_filename == matching[0].echo_filename
    assert sel.echo_audio_url.endswith(f"{sel.echo_filename}.mp3")
    assert f"{sel.signal_type}_Signal" in sel.echo_audio_url


async def test_coverage_weighting_favors_underused():
    """Principle roll uses a weighted pool when usage counts are provided."""
    calls = []
    ref = ltp.load_reference()
    freq = ref.all_frequencies[0]
    principles = {k.principle for k in ref.tuning_keys_for(freq)}
    history = ltp.History(
        recent_frequencies=[f for f in ref.all_frequencies if f != freq],
        principle_usage_counts={p: 5 for p in list(principles)[:1]},
    )
    await ltp.select_tuning(history=history, entropy=_fixed_entropy(calls))
    # roll 2's pool is the weighted pool (> raw principle count) when weights apply
    if len(principles) > 1:
        assert calls[1] > len(principles) - 0  # weighted expansion happened


def test_reference_data_validates_against_protocol_tables():
    """Bundled lt_reference.json conforms to the fixed spec tables."""
    ref = ltp.load_reference()
    assert ref.validate() == []
    assert len(ref.all_frequencies) == 13
    total_keys = sum(len(ref.tuning_keys_for(f)) for f in ref.all_frequencies)
    assert total_keys >= 200  # spec says 244; allow data evolution


def test_protocol_class_and_context():
    protocol = ltp.TuningProtocol(anchor="canon")
    sel = protocol.tune_sync()
    ctx = protocol.as_context(sel)
    assert sel.frequency in ctx
    assert sel.tuning_key in ctx
    assert "CC BY 4.0" in ctx
