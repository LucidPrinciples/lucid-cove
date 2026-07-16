"""Founder spark must land in Chat before Continue navigates (install-pass 2026-07-15).

Regression: fire-and-forget /api/presence/wake-thread + immediate location.href
let the browser cancel the POST. Brain-ack later wrote alone → Chat only showed
the intelligence-connect message, not the spark exchange.
"""
from pathlib import Path

HTML = Path("src/dashboard/static/action-board/new-agent-setup.html").read_text()


def test_wake_persist_promise_tracked():
    assert "let _wakePersistPromise" in HTML
    assert "_wakePersistPromise = _persistWakeThread()" in HTML


def test_finish_wake_awaits_persist_before_navigate():
    # finishWake must await the in-flight (or kick a) persist before redirect
    assert "await Promise.race" in HTML
    assert "_wakePersistPromise" in HTML
    # Soft cap so a hung write can't freeze the door forever
    assert "8000" in HTML
    # Founder path still navigates after await
    assert "window.location.href = _wakeRedirect" in HTML


def test_persist_wake_uses_keepalive_and_credentials():
    # Survive navigation + multi-mode session cookie
    assert "keepalive: true" in HTML
    assert "credentials: 'same-origin'" in HTML
    assert "/api/presence/wake-thread" in HTML


def test_firstrun_persist_starts_before_handoff_animation():
    # Persist must not be buried only inside the 700ms setTimeout (Continue race)
    # — start write first, animate handoff after.
    assert "_wakePersistPromise = _persistWakeThread();" in HTML
    # Old bug: only call was inside setTimeout after handoff bubble
    # Guard: finishWake re-kicks if somehow never started
    assert "if (!_wakePersistPromise)" in HTML
