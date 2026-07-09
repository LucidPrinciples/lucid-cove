"""
Development Tools — git, testing, builds, database for the steward agent.

Tier assignments:
  AUTO    — git_status, git_diff, git_log, git_current_branch, run_tests,
            check_syntax, db_query
  NOTIFY  — git_add, git_commit, git_create_branch, git_revert_file
  APPROVE — git_push, git_force_push, git_delete_branch, db_execute,
            create_github_pr
"""

import asyncio
import os
from src.env import env
from pathlib import Path
import shlex
from typing import Optional

from langchain_core.tools import tool

from src.tools.approval import auto, notify, approve
from src.utils.settings import get_setting_sync

PROJECTS_DIR = Path(env("STUART_DATA_DIR", "/app/data")) / "projects"
SITES_DIR = Path("/sites")  # website repos (GitHub → Cloudflare Pages)


# =============================================================================
# Helpers
# =============================================================================

async def _run_git(cmd: str, repo_dir: str, timeout: int = 30) -> str:
    """Run a git command in a specific repo directory."""
    proc = await asyncio.create_subprocess_shell(
        f"git {cmd}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_dir,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"Error: git command timed out after {timeout}s."

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        return f"Error (exit {proc.returncode}):\n{err}\n{out}".strip()
    return out or err or "(no output)"


async def _run_cmd(cmd: str, cwd: str = None, timeout: int = 60) -> str:
    """Run a command and return output."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        result = out
        if err:
            result += ("\n\nSTDERR:\n" + err) if out else err
        if proc.returncode != 0:
            result += f"\n[exit: {proc.returncode}]"
        return result or "(no output)"
    except asyncio.TimeoutError:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Error: {e}"


def _resolve_repo(project: str) -> str:
    """Resolve a project name to its repo directory.

    Checks: absolute paths, /sites/{name}, /data/projects/{name}.
    Site repos (one folder per domain) live in /sites/.
    """
    p = Path(project)
    if p.is_absolute() and p.exists():
        return str(p)
    # Check sites directory first (website repos)
    site_candidate = SITES_DIR / project
    if site_candidate.exists():
        return str(site_candidate)
    # Then project data
    candidate = PROJECTS_DIR / project
    if candidate.exists():
        return str(candidate)
    return project  # let git fail with a clear error


# =============================================================================
# Git — Read Operations (AUTO)
# =============================================================================

@auto
@tool
async def git_status(project: str) -> str:
    """Show git status for a project.

    Args:
        project: Project name or absolute path to repo
    """
    repo = _resolve_repo(project)
    branch = await _run_git("branch --show-current", repo)
    status = await _run_git("status --short", repo)
    return f"Branch: {branch}\n\n{status or '(clean working tree)'}"


@auto
@tool
async def git_diff(project: str, staged: bool = False, path: str = "") -> str:
    """Show uncommitted changes as a diff.

    Args:
        project: Project name or path
        staged: Show staged changes only
        path: Specific file to diff (optional)
    """
    repo = _resolve_repo(project)
    cmd = "diff --staged" if staged else "diff"
    if path:
        cmd += f" -- {path}"
    diff = await _run_git(cmd, repo)
    if not diff or diff == "(no output)":
        return "No changes." + (" Try staged=True?" if not staged else "")
    if len(diff) > 10000:
        diff = diff[:10000] + "\n\n... [truncated at 10000 chars]"
    return diff


@auto
@tool
async def git_log(project: str, n: int = 15) -> str:
    """Show recent commit history.

    Args:
        project: Project name or path
        n: Number of commits (default 15, max 50)
    """
    repo = _resolve_repo(project)
    n = min(max(n, 1), 50)
    return await _run_git(f"log --oneline --graph -{n}", repo)


@auto
@tool
async def git_current_branch(project: str) -> str:
    """Check what branch a project is on.

    Args:
        project: Project name or path
    """
    repo = _resolve_repo(project)
    return f"Current branch: {await _run_git('branch --show-current', repo)}"


@auto
@tool
async def git_diff_branch(project: str, base: str = "main") -> str:
    """Show full diff between current branch and base (usually main).

    Args:
        project: Project name or path
        base: Base branch to compare against (default: main)
    """
    repo = _resolve_repo(project)
    branch = await _run_git("branch --show-current", repo)
    diff = await _run_git(f"diff {base}...HEAD", repo)
    if not diff or diff == "(no output)":
        diff = await _run_git(f"diff {base}", repo)
    if not diff or diff == "(no output)":
        return f"No differences between {branch} and {base}."
    if len(diff) > 12000:
        diff = diff[:12000] + "\n\n... [truncated]"
    return f"DIFF: {branch} vs {base}\n\n{diff}"


# =============================================================================
# Git — Write Operations (NOTIFY)
# =============================================================================

@notify
@tool
async def git_create_branch(project: str, name: str, from_branch: str = "main") -> str:
    """Create and switch to a new branch.

    Args:
        project: Project name or path
        name: Branch name (will be prefixed with 'stuart/' for safety)
        from_branch: Branch to create from (default: main)
    """
    repo = _resolve_repo(project)
    safe_name = name.strip().lower().replace(" ", "-")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "-_/")
    if not safe_name.startswith("stuart/"):
        safe_name = f"stuart/{safe_name}"

    await _run_git(f"checkout {from_branch}", repo)
    await _run_git(f"pull origin {from_branch} 2>/dev/null", repo)
    result = await _run_git(f"checkout -b {safe_name}", repo)
    branch = await _run_git("branch --show-current", repo)
    return f"Created branch: {branch}\n{result}"


@notify
@tool
async def git_add(project: str, files: str = ".") -> str:
    """Stage files for commit.

    Args:
        project: Project name or path
        files: Space-separated file paths, or '.' for all changes
    """
    repo = _resolve_repo(project)
    result = await _run_git(f"add {files}", repo)
    return f"Staged: {files}\n{result}"


@notify
@tool
async def git_commit(project: str, message: str) -> str:
    """Commit staged changes. Refuses to commit on main.

    Args:
        project: Project name or path
        message: Commit message
    """
    repo = _resolve_repo(project)
    branch = await _run_git("branch --show-current", repo)
    # Sites repos deploy from main — no branch workflow needed
    is_site = str(repo).startswith(str(SITES_DIR))
    if branch.strip() in ("main", "master") and not is_site:
        return f"REFUSED: Cannot commit on {branch}. Create a feature branch first."

    # Set admin agent as the git author
    _admin_name = get_setting_sync("admin_agent_display_name", "Stuart")
    _family = get_setting_sync("family_name", "Cove")
    _git_name = f"{_admin_name} {_family}"
    _admin_id = get_setting_sync("admin_agent_id", "stuart")
    # shlex.quote EVERYTHING user-supplied: an apostrophe/quote/dash in a commit
    # message used to shell-split the command (found live 2026-07-09: the steward's
    # first #D10 commit failed twice and forced a raw-shell workaround).
    env_cmd = (
        f'GIT_AUTHOR_NAME={shlex.quote(_git_name)} '
        f'GIT_COMMITTER_NAME={shlex.quote(_git_name)} '
        f'GIT_AUTHOR_EMAIL={shlex.quote(_admin_id + "@lucidtuner.ai")} '
        f'GIT_COMMITTER_EMAIL={shlex.quote(_admin_id + "@lucidtuner.ai")} '
        f'git commit -m {shlex.quote(message)}'
    )
    proc = await asyncio.create_subprocess_shell(
        env_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    result = stdout.decode(errors="replace").strip()
    if stderr:
        result += "\n" + stderr.decode(errors="replace").strip()
    return f"Committed on {branch}:\n{result}"


@notify
@tool
async def git_revert_file(project: str, path: str, to_branch: str = "main") -> str:
    """Revert a file to its state on another branch. The undo button.

    Args:
        project: Project name or path
        path: File path relative to repo root
        to_branch: Branch to restore from (default: main)
    """
    repo = _resolve_repo(project)
    result = await _run_git(f"checkout {to_branch} -- {path}", repo)
    return f"Reverted {path} to {to_branch} version.\n{result}"


# =============================================================================
# Git — Dangerous Operations (APPROVE)
# =============================================================================

@approve
@tool
async def git_push(project: str, branch: str = "") -> str:
    """Push branch to remote. Requires approval.

    Args:
        project: Project name or path
        branch: Branch to push (default: current branch)
    """
    repo = _resolve_repo(project)
    if not branch:
        branch = await _run_git("branch --show-current", repo)
    return await _run_git(f"push origin {shlex.quote(branch)}", repo)


@approve
@tool
async def git_delete_branch(project: str, branch: str, remote: bool = False) -> str:
    """Delete a branch. Requires approval.

    Args:
        project: Project name or path
        branch: Branch to delete
        remote: Also delete from remote
    """
    repo = _resolve_repo(project)
    result = await _run_git(f"branch -d {branch}", repo)
    if remote:
        result += "\n" + await _run_git(f"push origin --delete {branch}", repo)
    return result


@approve
@tool
async def create_github_pr(project: str, title: str, body: str = "",
                           base: str = "main") -> str:
    """Create a GitHub pull request using gh CLI. Requires approval.

    Args:
        project: Project name or path
        title: PR title
        body: PR description/body
        base: Base branch (default: main)
    """
    repo = _resolve_repo(project)
    return await _run_cmd(
        f'gh pr create --title {shlex.quote(title)} --body {shlex.quote(body)} '
        f'--base {shlex.quote(base)}',
        cwd=repo, timeout=30
    )


# =============================================================================
# Testing
# =============================================================================

@auto
@tool
async def run_tests(project: str, path: str = "", verbose: bool = False) -> str:
    """Run pytest for a project.

    Args:
        project: Project name or path
        path: Specific test file or dir (default: auto-discover)
        verbose: Show full output
    """
    repo = _resolve_repo(project)
    v = "-v" if verbose else "-q"
    target = path or "."
    return await _run_cmd(f"python -m pytest {target} {v} --tb=short --no-header 2>&1", cwd=repo, timeout=120)


@auto
@tool
async def check_syntax(project: str, path: str) -> str:
    """Check Python syntax without running.

    Args:
        project: Project name or path
        path: File to check (relative to repo root)
    """
    repo = _resolve_repo(project)
    return await _run_cmd(f"python -m py_compile {path} 2>&1 && echo 'Syntax OK: {path}'", cwd=repo)


# =============================================================================
# Database
# =============================================================================

@auto
@tool
async def db_query(sql: str) -> str:
    """Run a read-only SQL query.

    Args:
        sql: SELECT query
    """
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            result = await conn.execute(sql)
            rows = await result.fetchall()
            if not rows:
                return "(no rows)"
            cols = list(rows[0].keys()) if hasattr(rows[0], 'keys') else [str(i) for i in range(len(rows[0]))]
            lines = [" | ".join(str(c) for c in cols)]
            lines.append("-" * len(lines[0]))
            for row in rows[:50]:
                lines.append(" | ".join(str(v) for v in row))
            if len(rows) > 50:
                lines.append(f"[...{len(rows) - 50} more rows]")
            return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@approve
@tool
async def db_execute(sql: str, params: Optional[list] = None, reason: str = "") -> str:
    """Run a write SQL statement (INSERT, UPDATE, DELETE). Requires approval.

    Args:
        sql: SQL statement
        params: Parameter values (optional)
        reason: Why this write is needed
    """
    try:
        from src.memory.database import get_db
        async with get_db() as conn:
            if params:
                await conn.execute(sql, params)
            else:
                await conn.execute(sql)
            await conn.commit()
            return "OK"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_DEV_TOOLS = [
    # Git read
    git_status, git_diff, git_log, git_current_branch, git_diff_branch,
    # Git write
    git_create_branch, git_add, git_commit, git_revert_file,
    # Git dangerous
    git_push, git_delete_branch, create_github_pr,
    # Testing
    run_tests, check_syntax,
    # Database
    db_query, db_execute,
]
TOOLS = ALL_DEV_TOOLS  # alias for cove-core channels.py loader
