"""Canon injection tests — verbatim guaranteed, model never handles the lyric."""

import lucid_tuner_protocol as ltp

KEY = "Down to our cells we have to learn how to discern / Structure emerging from the random forming like crystals in a cavern"


def test_inject_replaces_token_verbatim():
    out = ltp.inject_canon("close: [[TUNING_KEY]] — Pattern", KEY)
    assert f'"{KEY}"' in out
    assert "[[TUNING_KEY]]" not in out


def test_inject_unquoted_option():
    out = ltp.inject_canon("Key: [[TUNING_KEY]]", KEY, quote=False)
    assert out == f"Key: {KEY}"


def test_empty_key_removes_token():
    assert ltp.inject_canon("a [[TUNING_KEY]] b", "") == "a  b"


def test_canon_never_altered():
    out = ltp.inject_canon("[[TUNING_KEY]]", KEY)
    # The exact bytes survive — no paraphrase, no truncation.
    assert KEY in out


def test_unresolved_token_detector():
    assert ltp.has_unresolved_token("oops [[TUNING_KEY]] left") is True
    assert ltp.has_unresolved_token(ltp.inject_canon("[[TUNING_KEY]]", KEY)) is False


def test_instruction_tells_model_to_place_token_only():
    assert "[[TUNING_KEY]]" in ltp.CANON_TOKEN_INSTRUCTION
    assert "never" in ltp.CANON_TOKEN_INSTRUCTION.lower()
