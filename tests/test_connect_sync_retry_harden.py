"""Connect sync retry harden (post-#154 residue).

After the Quietgrove hang fix, ERROR/STOPPED still left the matrix-js-sdk client
alive and doubled backoff on every event — so one dead /sync became a client-
stacking retry loop. Contract:

  * one-shot failConnect lock per attempt (ERROR + STOPPED = one retry)
  * teardown (listeners off, then stopClient) before scheduling retry
  * cap auto-retries and surface a manual Retry control
  * PREPARED clears the storm counters
"""
from pathlib import Path

CX = Path("src/dashboard/static/js/connect.js").read_text()


def test_fail_connect_is_one_shot():
    assert "function failConnect" in CX
    assert "connectFailLocked" in CX
    assert "if (connectFailLocked)" in CX
    # ERROR path must go through failConnect, not a raw backoff block.
    assert "failConnect('Chat sync hit ' + state)" in CX
    assert "failConnect('Chat sync timed out" in CX
    assert "failConnect('Chat sync failed to start')" in CX


def test_teardown_before_retry_and_listener_order():
    assert "function teardownConnectClient" in CX
    # Listeners first so stopClient's STOPPED cannot re-enter after unlock.
    i_rm = CX.index("removeAllListeners")
    i_stop = CX.index("c.stopClient")
    assert i_rm < i_stop
    assert "teardownConnectClient(reason || 'fail')" in CX
    assert "teardownConnectClient('ensureChats-start')" in CX


def test_auto_retry_caps_with_manual_button():
    assert "CONNECT_MAX_AUTO" in CX
    assert "cx-retry-now" in CX
    assert "auto-retry paused" in CX


def test_prepared_resets_storm_counters():
    # PREPARED block must clear attempt + lock + backoff.
    assert "connectAttempt = 0" in CX
    # tokenBackoff reset on success (not on mere token mint — only PREPARED).
    prepared_idx = CX.index("state === 'PREPARED'")
    chunk = CX[prepared_idx : prepared_idx + 500]
    assert "connectAttempt = 0" in chunk
    assert "tokenBackoff = 2000" in chunk
    assert "connectFailLocked = false" in chunk
