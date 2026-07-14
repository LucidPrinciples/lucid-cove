"""Install-pass: brain-acknowledge is idempotent so Open-chat re-clicks don't double-append."""
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.dashboard.routes.wake_thread import (  # noqa: E402
    _thread_already_has_brain_ack,
    _BRAIN_ACK_FALLBACK,
    _ensure_setup_steps_line,
)


class _Msg:
    def __init__(self, type_, content, kind=None):
        self.type = type_
        self.content = content
        self.additional_kwargs = {"kind": kind} if kind else {}


def test_empty_thread_not_acked():
    assert not _thread_already_has_brain_ack([])


def test_wake_exchange_not_mistaken_for_ack():
    # Wake handoff talks about "brain" but is NOT a brain-acknowledge.
    wake = (
        "Next, you'll give the Cove a brain of its own — your own model, or a local one "
        "— so the whole team comes online."
    )
    msgs = [
        _Msg("ai", "Hello — I'm Jude. What should I call you?"),
        _Msg("human", "JAG"),
        _Msg("ai", "Thank you. I'm keeping that.\n\n" + wake),
    ]
    assert not _thread_already_has_brain_ack(msgs)


def test_kind_tag_detects_ack():
    msgs = [_Msg("ai", "Something unique the model said.", kind="brain_ack")]
    assert _thread_already_has_brain_ack(msgs)


def test_fallback_content_detects_legacy_ack():
    msgs = [_Msg("ai", _BRAIN_ACK_FALLBACK)]
    assert _thread_already_has_brain_ack(msgs)


def test_setup_nudge_line_detects_legacy_ack():
    text = _ensure_setup_steps_line(
        "Warm short ack from the model.",
        ["set your Cove's address", "connect your phone"],
    )
    assert _thread_already_has_brain_ack([_Msg("ai", text)])


def test_human_message_never_counts():
    assert not _thread_already_has_brain_ack([_Msg("human", _BRAIN_ACK_FALLBACK, kind="brain_ack")])


def test_dict_messages_supported():
    assert _thread_already_has_brain_ack([
        {"type": "ai", "content": "x", "additional_kwargs": {"kind": "brain_ack"}},
    ])
