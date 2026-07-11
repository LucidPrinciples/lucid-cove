"""git_push pre-check — new-branch first push must not be refused.

Root cause (07-11, Stuart's #D41 push): _run_git wraps failures as
"Error (exit N):\nfatal: ..." but the pre-check sniffed for a bare "fatal:"
prefix. A MISSING origin/<branch> therefore looked like an EXISTING upstream,
the code ran `rev-list origin/<new>..<new>` (exit 128, "unknown revision"),
and the tool returned REFUSED — for every first push of every new branch,
regardless of approval. The fix accepts only a real 40-hex sha as proof the
upstream exists.
"""
import pytest

import src.tools.dev_tools as dt

SHA = "a" * 40
GIT_128 = (
    "Error (exit 128):\nfatal: ambiguous argument "
    "'origin/stuart/d41-ground-agents-in-real-repos': unknown revision or "
    "path not in the working tree."
)


def _mock_git(responses):
    """Return an async _run_git double keyed by command substring (first match wins)."""
    calls = []

    async def _run(cmd, repo, timeout=30):
        calls.append(cmd)
        for key, value in responses:
            if key in cmd:
                return value
        return ""

    _run.calls = calls
    return _run


@pytest.mark.asyncio
async def test_new_branch_without_upstream_is_pushable(monkeypatch):
    """The 07-11 failure case: rev-parse fails with the Error(exit 128) wrapper."""
    run = _mock_git([
        ("rev-parse --verify", GIT_128),          # no upstream — wrapped error, NOT bare "fatal:"
        ("rev-list --count", "3"),                # branch has commits
        ("push origin", "To github.com:o/r.git\n * [new branch]"),
    ])
    monkeypatch.setattr(dt, "_run_git", run)
    monkeypatch.setattr(dt, "_resolve_repo", lambda p: "/tmp/repo")

    result = await dt.git_push.coroutine("proj", "stuart/d41-ground-agents-in-real-repos")

    assert "REFUSED" not in result
    assert any(c.startswith("push origin") for c in run.calls)
    # and it must never have compared against the nonexistent upstream
    assert not any("origin/stuart" in c and "rev-list" in c for c in run.calls)


@pytest.mark.asyncio
async def test_new_branch_with_zero_commits_refused(monkeypatch):
    run = _mock_git([
        ("rev-parse --verify", GIT_128),
        ("rev-list --count", "0"),
    ])
    monkeypatch.setattr(dt, "_run_git", run)
    monkeypatch.setattr(dt, "_resolve_repo", lambda p: "/tmp/repo")

    result = await dt.git_push.coroutine("proj", "newbranch")

    assert "REFUSED" in result
    assert not any(c.startswith("push origin") for c in run.calls)


@pytest.mark.asyncio
async def test_existing_upstream_no_unpushed_commits_refused(monkeypatch):
    run = _mock_git([
        ("rev-parse --verify", SHA),              # upstream exists (real sha)
        ("rev-list --count", "0"),                # nothing ahead
    ])
    monkeypatch.setattr(dt, "_run_git", run)
    monkeypatch.setattr(dt, "_resolve_repo", lambda p: "/tmp/repo")

    result = await dt.git_push.coroutine("proj", "main")

    assert "REFUSED" in result
    assert not any(c.startswith("push origin") for c in run.calls)


@pytest.mark.asyncio
async def test_existing_upstream_with_unpushed_commits_pushes(monkeypatch):
    run = _mock_git([
        ("rev-parse --verify", SHA),
        ("rev-list --count", "2"),
        ("push origin", "ok"),
    ])
    monkeypatch.setattr(dt, "_run_git", run)
    monkeypatch.setattr(dt, "_resolve_repo", lambda p: "/tmp/repo")

    result = await dt.git_push.coroutine("proj", "feature")

    assert "REFUSED" not in result
    assert any(c.startswith("push origin") for c in run.calls)


@pytest.mark.asyncio
async def test_garbage_rev_parse_output_not_treated_as_upstream(monkeypatch):
    """Anything that isn't a sha (timeouts, partial output) = no upstream, fall through
    to the new-branch path instead of the doomed origin/<branch> comparison."""
    run = _mock_git([
        ("rev-parse --verify", "Error: git command timed out after 30s."),
        ("rev-list --count", "1"),
        ("push origin", "ok"),
    ])
    monkeypatch.setattr(dt, "_run_git", run)
    monkeypatch.setattr(dt, "_resolve_repo", lambda p: "/tmp/repo")

    result = await dt.git_push.coroutine("proj", "b")

    assert "REFUSED" not in result
