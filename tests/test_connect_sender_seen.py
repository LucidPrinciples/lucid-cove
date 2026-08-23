"""Connect Family-room sender label + send/seen chrome.

Timeline used to print the raw Matrix localpart (steward) and never
listened for typing or receipts, so a mention sat silent until a reply
appeared. Product contract: bubble says Stuart, typing/receipt events
re-paint the open room, last own message can show Seen by.
"""
from pathlib import Path

CX = Path("src/dashboard/static/js/connect.js").read_text()


def test_timeline_uses_pretty_sender_not_raw_localpart():
    assert "function prettySenderLabel" in CX
    rt = CX.index("function renderTimeline")
    chunk = CX[rt : rt + 1800]
    assert "prettySenderLabel" in chunk
    assert "split(':')[0].replace('@', '')" not in chunk
    assert "local === 'steward') return 'Stuart'" in CX


def test_invite_label_not_reused_as_your_cove_on_bubbles():
    fn = CX.index("function prettySenderLabel")
    chunk = CX[fn : fn + 700]
    assert "Your Cove" in chunk
    assert "return 'Stuart'" in chunk


def test_typing_and_receipt_listeners_repaint_open_room():
    assert "RoomEvent.Typing" in CX
    assert "RoomEvent.Receipt" in CX
    assert "client.on(RoomEvent.Typing" in CX
    assert "client.on(RoomEvent.Receipt" in CX


def test_seen_and_typing_chrome_present():
    assert "cx-seen" in CX
    assert "cx-typing" in CX
    assert "Seen by " in CX
    assert " is typing…" in CX
    assert "function typingNames" in CX
    assert "function receiptNamesForEvent" in CX
