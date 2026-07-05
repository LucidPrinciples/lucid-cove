"""Dispatch lock — prevents sweep from running during active Socrates-orchestrated dispatch.

The ltp-dispatch endpoint sets this lock while running. Stuart's safety-net
sweep checks it and defers if a dispatch is in progress. This prevents GPU
contention when multiple Coves share hardware.

Simple module-level flag. No persistence needed — if the process restarts,
the lock resets and the sweep resumes normally.
"""

_dispatch_running = False


def is_dispatch_running() -> bool:
    return _dispatch_running


def set_dispatch_running(running: bool):
    global _dispatch_running
    _dispatch_running = running
