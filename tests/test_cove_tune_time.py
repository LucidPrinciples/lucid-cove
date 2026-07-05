"""
batch-10 #7 — the Cove morning self-tune runs at 06:30 America/New_York (locked
2026-07-04). The Drop publishes ~05:30 ET, so 06:30 leaves an hour of slack. Text scan
(no import of the scheduler, which pulls the whole app) locks the registered time.
"""

import re
from pathlib import Path

SCHED = Path(__file__).resolve().parents[1] / "src" / "utils" / "scheduler.py"


def test_cove_self_tune_registered_at_0630():
    src = SCHED.read_text()
    # The host self-tune registration must schedule _run_tuning_sweep at 06:30.
    m = re.search(r'schedule\.every\(\)\.day\.at\("06:30",\s*tz\)\.do\(\s*'
                  r'self\._schedule_async\(self\._run_tuning_sweep\)', src)
    assert m, "Cove self-tune is not registered at 06:30"


def test_no_0700_self_tune_registration():
    src = SCHED.read_text()
    # No lingering .at("07:00") schedule registration (docstrings updated too).
    assert '.at("07:00"' not in src, "a 07:00 schedule registration still remains"
