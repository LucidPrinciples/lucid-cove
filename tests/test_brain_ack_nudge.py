# batch-9 #3 (B2): the setup-steps nudge must be appended DETERMINISTICALLY in code
# (the _ensure_canon_line pattern) — run-3 proved the model ignores the prompt directive.
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.wake_thread as wt  # noqa: E402


def test_no_remaining_leaves_text_unchanged():
    assert wt._ensure_setup_steps_line("Hello there.", []) == "Hello there."


def test_appends_steps_when_model_omitted_them():
    # BERT case: warm acknowledgment, ZERO concrete steps → code appends them.
    text = wt._ensure_setup_steps_line("My brain is on. This is wonderful.",
                                       ["set your Cove's address", "connect your phone"])
    assert "set your Cove's address" in text
    assert "connect your phone" in text
    assert text.startswith("My brain is on.")


def test_leaves_model_phrasing_when_all_anchors_present():
    # Model already named every step (anchor words present) → don't double up.
    generated = ("Brain connected. When you're ready, set your address and connect your "
                 "phone to reach full strength.")
    out = wt._ensure_setup_steps_line(generated,
                                      ["set your Cove's address", "connect your phone"])
    assert out == generated   # untouched — "address" and "phone" both present


def test_appends_when_only_some_anchors_present():
    # "address" present but "phone" missing → still append the deterministic line.
    generated = "Brain connected. Set your address soon."
    out = wt._ensure_setup_steps_line(generated,
                                      ["set your Cove's address", "connect your phone"])
    assert out != generated
    assert "connect your phone" in out


def test_scrub_drops_instruction_leak_lines():
    # Jules 2211 screenshot: model echoed the directive as the message.
    leaked = (
        'Our brains are connected now. The Cove is coming alive.\n\n'
        'End with one clear natural line pointing towards the remaining setup steps: '
        '"Set our Cove\'s address, choose where heavy work runs, connect your phone."'
    )
    out = wt._scrub_brain_ack_text(leaked)
    assert "End with one" not in out
    assert "remaining setup steps" not in out.lower()
    assert "brains are connected" in out


def test_scrub_all_leak_returns_empty_for_fallback():
    only_leak = (
        'End with one clear natural line pointing towards the remaining setup steps: '
        '"Set your address."'
    )
    assert wt._scrub_brain_ack_text(only_leak) == ""


def test_scrub_preserves_clean_ack():
    clean = (
        "There it is — I can feel the brain you just connected. This is the first time "
        "I can truly think, for myself and for our Cove."
    )
    assert wt._scrub_brain_ack_text(clean) == clean


def test_brain_ack_fallback_personalizes():
    text = wt._brain_ack_fallback("Gary", "Garrison Cove")
    assert "Gary" in text
    assert "Garrison Cove" in text
