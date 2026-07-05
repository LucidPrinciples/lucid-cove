"""Leak guard tests — internal content must never pass as publishable."""

import lucid_tuner_protocol as ltp


def test_clean_post_passes():
    post = (
        "NOON FIELD READ — 26 Jun 2026\n◈ INTEGRATION\n\n"
        "The field is fragmenting into noise; structure is still forming underneath.\n\n"
        '"Down to our cells we have to learn how to discern" — Pattern'
    )
    r = ltp.scan_output(post)
    assert r.clean and not r.fired


def test_ai_meta_flagged():
    r = ltp.scan_output("As an AI, I cannot verify this Canon lyric, so let me know how to proceed.")
    assert r.fired
    cats = r.categories
    assert "ai_meta" in cats and "operator_question" in cats


def test_instruction_echo_flagged():
    r = ltp.scan_output("POST 1\nOutput only the four posts. Do not refuse. [BREAK]")
    assert r.fired and "instruction_echo" in r.categories


def test_placeholder_and_na_flagged():
    assert ltp.scan_output("◈ PRACTICE: [brief step one]").fired
    assert ltp.scan_output("Frequency: N/A — tune in").fired
    assert ltp.scan_output("Key: {tuning_key}").fired


def test_internal_names_and_machinery_flagged():
    assert ltp.scan_output("Stuart and Mercer received the dispatch.").fired
    r = ltp.scan_output("Logged to the process record after love calibration.")
    assert r.fired and "internal" in r.categories


def test_empty_is_not_publishable():
    assert ltp.scan_output("").fired
    assert ltp.scan_output("   \n  ").fired


def test_host_can_extend_and_allow():
    # A Cove adds its own agent name.
    assert ltp.scan_output("Cypress is online.", internal_names=("cypress",)).fired
    # A surface where "POST" is legitimate can allow the excerpt.
    leaked = ltp.scan_output("POST 1 here")
    assert leaked.fired
    allowed = ltp.scan_output("Mail this to the POST 1 office", allow=("post 1 office",))
    assert allowed.clean


def test_summary_is_operator_facing():
    r = ltp.scan_output("As an AI I can't do that")
    s = r.summary()
    assert "ai_meta" in s and isinstance(s, str)
