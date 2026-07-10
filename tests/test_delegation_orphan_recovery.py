# #D30 — restart-orphaned delegations. A delegated turn killed by a container restart
# left its task 'in_progress' forever. Two layers: a watcher check (stale in_progress
# agent tasks) and a boot-time sweep (mark them blocked + report-back). No DB harness for
# these paths in-repo, so guard the SQL scoping (the safety-critical part: never touch a
# non-agent or non-in_progress task) and the check registration.
import inspect
import pathlib
import re

from src.utils.watcher import _CHECKS, CATEGORIES, _check_delegation_stale
from src.tools import delegation_tools

DELEG = pathlib.Path(__file__).resolve().parents[1] / "src" / "tools" / "delegation_tools.py"


def test_delegation_stale_check_registered():
    assert _check_delegation_stale in _CHECKS
    # category derived from the function name must be in CATEGORIES for auto-resolve
    assert "delegation-stale" in CATEGORIES


def test_stale_check_scoped_to_agent_in_progress():
    src = inspect.getsource(_check_delegation_stale)
    assert "source = 'agent'" in src
    assert "status = 'in_progress'" in src
    assert "30 minutes" in src


def test_boot_sweep_only_touches_agent_in_progress():
    # the UPDATE must be scoped so it can NEVER clobber a real (human/internal) task
    src = inspect.getsource(delegation_tools.sweep_orphaned_delegations)
    m = re.search(r"UPDATE tasks SET .*?WHERE (.*?)RETURNING", src, re.S)
    assert m, "expected an UPDATE ... WHERE ... RETURNING in the sweep"
    where = m.group(1)
    assert "source = 'agent'" in where
    assert "status = 'in_progress'" in where
    assert "blocked" in src


def test_set_task_status_scoped_to_agent():
    # the lifecycle helper must only ever transition delegated (source='agent') rows
    src = inspect.getsource(delegation_tools._set_task_status)
    assert "source = 'agent'" in src


def test_turn_marks_lifecycle_statuses():
    # in_progress at start; done on success; blocked on timeout/failure
    src = DELEG.read_text()
    assert '_set_task_status(task_id, "in_progress")' in src
    assert '_set_task_status(task_id, "done")' in src
    assert src.count('_set_task_status(task_id, "blocked")') >= 2
