"""#JL-WAKE — Jules Screen Wake Lock must cover record AND save.

Mobile lock-screen during re-transcribe/save was dropping in-flight notes because
stopRecording() released the wake lock immediately. Contract is enforced as
source-shape assertions on jules.html (same style as other Jules client tests).
"""
import re
from pathlib import Path

JULES = (Path(__file__).resolve().parents[1]
         / "src" / "dashboard" / "static" / "jules.html").read_text()


def _top_level_fn(name: str) -> str:
    """Extract a top-level `function name() { ... }` (async optional) by brace matching."""
    m = re.search(r"^(?:async\s+)?function " + re.escape(name) + r"\([^)]*\) \{", JULES, re.M)
    assert m, f"{name} not found"
    i = m.end() - 1  # at '{'
    depth = 0
    for j in range(i, len(JULES)):
        c = JULES[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return JULES[m.start(): j + 1]
    raise AssertionError(f"unclosed function {name}")


def test_wake_lock_helper_covers_save_phases():
    assert "function wakeLockStillNeeded()" in JULES
    body = _top_level_fn("wakeLockStillNeeded")
    for flag in ("recording", "saving", "pendingAutoSave", "needsSave"):
        assert flag in body, flag


def test_visibility_reacquires_during_save_not_only_record():
    idx = JULES.find('document.addEventListener("visibilitychange"')
    assert idx != -1
    window = JULES[idx: idx + 280]
    assert "wakeLockStillNeeded()" in window
    assert "recording && !wakeLock" not in window


def test_stop_recording_does_not_release_before_save():
    body = _top_level_fn("stopRecording")
    # Must not release unconditionally near the top (the original bug).
    # Allowed: one release in the nothing-to-save branch.
    assert body.count("releaseWakeLock") == 1
    assert "Nothing to persist" in body
    assert "#JL-WAKE" in body


def test_finish_ok_and_err_release_lock():
    body = _top_level_fn("doSave")
    assert "releaseWakeLock();  // #JL-WAKE — save finished successfully" in body
    assert "releaseWakeLock();  // #JL-WAKE — save path ended" in body


def test_dosave_reasserts_wake_lock():
    body = _top_level_fn("doSave")
    assert "Re-assert wake lock" in body
    assert "acquireWakeLock()" in body


def test_start_recording_still_acquires():
    body = _top_level_fn("startRecording")
    assert "acquireWakeLock()" in body
