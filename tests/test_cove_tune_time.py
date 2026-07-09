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
    # The host self-tune registers at 06:30 + a deterministic per-Cove jitter
    # (0-25 min, so co-located Coves sharing one Ollama don't all fire at once):
    # the registered time is built as "06:%02d" % (30 + _off), then scheduled.
    m = re.search(r'_tune_at\s*=\s*"06:%02d"\s*%\s*\(30\s*\+\s*_off\)', src)
    assert m, "Cove self-tune base time is not 06:30 (+ per-Cove jitter)"
    m2 = re.search(r'schedule\.every\(\)\.day\.at\(_tune_at,\s*tz\)\.do\(\s*'
                   r'self\._schedule_async\(self\._run_tuning_sweep\)', src)
    assert m2, "Cove self-tune (_run_tuning_sweep) is not registered at _tune_at"


def test_no_0700_self_tune_registration():
    src = SCHED.read_text()
    # No lingering .at("07:00") schedule registration (docstrings updated too).
    assert '.at("07:00"' not in src, "a 07:00 schedule registration still remains"
