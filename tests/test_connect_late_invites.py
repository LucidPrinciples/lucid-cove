"""Connect late steward invites (post set-domain / first Connect).

Race: /api/matrix/token returns immediately and kicks ensure_cove_space in the
background. First client PREPARED often paints an empty room list before the
steward Space + Family invites exist. Timeline-only listeners never re-render
when membership flips without a message.

Contract:
  * live SYNCING/CATCHUP after started re-paints the tree
  * Room.myMembership is wired when the SDK exposes it
  * same-Cove invites auto-join without opening a row
  * empty state is waiting guidance (Devices & Access pattern), not a dead end
"""
from pathlib import Path

CX = Path("src/dashboard/static/js/connect.js").read_text()


def test_sync_repaints_after_started():
    assert "started && mode === 'chats'" in CX
    assert "state === 'SYNCING'" in CX
    assert "state === 'CATCHUP'" in CX
    # After first PREPARED, later rounds must call renderTree (not only first paint).
    prepared_blocks = CX.split("state === 'PREPARED'")
    assert len(prepared_blocks) >= 2
    # The SYNCING branch sits near the PREPARED handlers and includes renderTree.
    idx = CX.index("state === 'SYNCING'")
    chunk = CX[idx : idx + 400]
    assert "renderTree()" in chunk
    assert "autoJoinOwnServerInvites" in chunk


def test_my_membership_listener_wired():
    assert "RoomEvent.MyMembership" in CX
    assert "client.on(RoomEvent.MyMembership" in CX


def test_auto_join_own_server_invites_helper():
    assert "function autoJoinOwnServerInvites" in CX
    assert "_autoJoinInFlight" in CX
    # Must run from renderTree so an empty list still joins without a row click.
    rt = CX.index("function renderTree")
    chunk = CX[rt : rt + 500]
    assert "autoJoinOwnServerInvites" in chunk
    # And from first PREPARED + later sync.
    assert CX.count("autoJoinOwnServerInvites") >= 3


def test_empty_state_is_waiting_guidance_not_dead_end():
    assert "cx-empty-waiting" in CX
    assert "Setting up your Cove chats" in CX
    # Old dead-end copy must not be the only empty path.
    assert "when they arrive, this note goes away" in CX
    # Permanent-looking "Use + Add" alone is not enough for first Connect.
    assert "Stay on this tab" in CX
