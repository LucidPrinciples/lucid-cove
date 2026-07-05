"""Dispatch lock — prevents overlapping tuning dispatches.

Two scopes:

1. IN-PROCESS (always on): a module-level flag so one Cove never double-dispatches
   itself (the 06:30 run + boot catch-up + a manual sweep can't collide).

2. HOST-SHARED (opt-in): when several Coves run on ONE machine and share a single
   local Ollama (the multi-Cove-per-machine / Haven-on-one-box case), they'd otherwise
   all slam that one Ollama at 06:30 and thrash it (loading/unloading models, timeouts).
   If LP_TUNE_LOCK_DIR points at a host directory MOUNTED INTO EVERY co-located Cove,
   the lock also lives there as a file, so the Coves see each other: one dispatches, the
   others defer (run_cove_sweep already defers when a dispatch is running) and catch up on
   their next 30-min safety sweep. They serialize naturally instead of contending.

   Unset (a single Cove, or the founder) → host-shared behavior is off and this is exactly
   the old in-process-only flag. Founder/existing Coves are unchanged.

The lock file carries the holder's COVE_ID + a timestamp and is treated as STALE after
LP_TUNE_LOCK_STALE_SECS (default 30 min), so a crashed holder never wedges the fleet.
"""

import os
import json
import time

_dispatch_running = False  # in-process (this Cove's own dispatch)

_STALE_SECS = int(os.getenv("LP_TUNE_LOCK_STALE_SECS", "1800") or "1800")


def _lock_path() -> str | None:
    """Path to the host-shared lock file, or None when host-sharing is off."""
    d = (os.getenv("LP_TUNE_LOCK_DIR") or "").strip()
    return os.path.join(d, "tuning.lock") if d else None


def _host_lock_held_by_other() -> bool:
    """True if a co-located Cove holds a FRESH host lock. Stale locks (crashed
    holder) and our own lock read as not-held (our own is covered by the in-process
    flag). Best-effort — any error → not held (never block tuning on lock IO)."""
    p = _lock_path()
    if not p:
        return False
    try:
        if not os.path.exists(p):
            return False
        if (time.time() - os.path.getmtime(p)) > _STALE_SECS:
            return False  # stale — holder likely crashed; the lock is free
        with open(p) as f:
            holder = (json.load(f) or {}).get("cove", "")
        return holder != (os.getenv("COVE_ID", "") or "")
    except Exception:
        return False


def is_dispatch_running() -> bool:
    """True if THIS Cove is dispatching, or a co-located Cove holds the host lock."""
    return _dispatch_running or _host_lock_held_by_other()


def set_dispatch_running(running: bool):
    """Set the in-process flag and, when host-sharing is on, write/remove the host
    lock file stamped with this Cove's id."""
    global _dispatch_running
    _dispatch_running = running
    p = _lock_path()
    if not p:
        return
    try:
        if running:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = f"{p}.{os.getpid()}"
            with open(tmp, "w") as f:
                f.write(json.dumps({"cove": os.getenv("COVE_ID", ""), "ts": time.time()}))
            os.replace(tmp, p)  # atomic publish
        elif os.path.exists(p):
            # Only clear our own lock — never remove a lock another Cove holds.
            try:
                with open(p) as f:
                    holder = (json.load(f) or {}).get("cove", "")
            except Exception:
                holder = os.getenv("COVE_ID", "")  # unreadable → assume ours, clean up
            if holder == (os.getenv("COVE_ID", "") or ""):
                os.remove(p)
    except Exception:
        pass
