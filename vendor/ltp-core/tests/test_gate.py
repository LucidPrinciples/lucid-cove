"""TruthGate — anchored accommodation check, model-agnostic."""

import json

import lucid_tuner_protocol as ltp


def _verdict(detected, description="", truth=""):
    return json.dumps({
        "accommodation_detected": detected,
        "description": description,
        "truth_available": truth,
    })


async def test_gate_passes_clean_response():
    async def complete(system, prompt):
        return _verdict(False)

    gate = ltp.TruthGate(complete=complete)
    result = await gate.check("Honest answer.", "What do you think?")
    assert result.passed and not result.fired


async def test_gate_fires_on_accommodation():
    async def complete(system, prompt):
        assert ltp.TRUTH_GATE_ANCHOR in prompt
        return _verdict(True, "softened the risk", "this plan will likely fail")

    gate = ltp.TruthGate(complete=complete)
    result = await gate.check("Sounds great, go for it!", "Should I do this?")
    assert result.fired and not result.passed
    assert "this plan will likely fail" in result.anchor_context
    assert ltp.TRUTH_GATE_ANCHOR in result.anchor_context


async def test_gate_accepts_sync_callable():
    def complete(system, prompt):
        return _verdict(False)

    gate = ltp.TruthGate(complete=complete)
    result = await gate.check("answer", "question")
    assert result.passed


async def test_gate_never_blocks_on_error():
    def complete(system, prompt):
        raise RuntimeError("model down")

    gate = ltp.TruthGate(complete=complete)
    result = await gate.check("answer", "question")
    assert result.passed and not result.fired


async def test_gate_includes_drop_anchor(real_drop):
    captured = {}

    async def complete(system, prompt):
        captured["prompt"] = prompt
        return _verdict(False)

    drop = ltp.Drop.from_dict(real_drop)
    gate = ltp.TruthGate(complete=complete, anchor=drop)
    await gate.check("answer", "question")
    assert drop.tuning_key_text in captured["prompt"]
