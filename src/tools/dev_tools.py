"""
Development Tools — git, testing, builds, database for the steward agent.

Tier assignments:
  AUTO    — git_status, git_diff, git_log, git_current_branch, run_tests,
            check_syntax, db_query
  NOTIFY  — git_add, git_commit, git_create_branch, git_revert_file
  APPROVE — git_push, git_force_push, git_delete_branch, db_execute,
            create_github_pr, ship_branch
"""

import asyncio
import os
import re
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

    Checks: /sites/{name}, PROJECTS_DIR/{name}.
    Site repos (one folder per domain) live in /sites/.
    Code repos: lucid-cove, ltp-core, ltp-drop at PROJECTS_DIR (/app/data/projects/).

    HARD BOUNDARY: Absolute paths outside the canonical roots are rejected.
    Path traversal (..) is rejected. Missing workspace = report, don't improvise.
    """
    from pathlib import Path
    p = Path(project)

    # Reject path traversal attempts
    if ".." in str(p):
        return f"Error: Path traversal ('..') not allowed in repo name: {project}"

    # If absolute path, verify it's under allowed roots
    CANONICAL_ROOTS = [PROJECTS_DIR, SITES_DIR]
    if p.is_absolute():
        if not any(str(p).startswith(str(root)) for root in CANONICAL_ROOTS):
            allowed = [str(r) for r in CANONICAL_ROOTS]
            return (f"Error: Repo path '{project}' is outside allowed directories. "
                    f"Allowed roots: {', '.join(allowed)}")
        if not p.exists():
            return f"Error: Repo path '{project}' does not exist."
        return str(p)

    # Check sites directory first (website repos)
    site_candidate = SITES_DIR / project
    if site_candidate.exists():
        return str(site_candidate)
    # Then project data
    candidate = PROJECTS_DIR / project
    if candidate.exists():
        return str(candidate)

    # Miss: return error with actual repo list and allowed roots
    found = []
    if PROJECTS_DIR.exists():
        found = [d.name for d in PROJECTS_DIR.iterdir() if d.is_dir() and (d / ".git").exists()]
    allowed_roots = f"{PROJECTS_DIR}, {SITES_DIR}"
    return (f"Error: Repo '{project}' not found. Available repos: {', '.join(found) if found else 'none found'}. "
            f"Allowed directories: {allowed_roots}. "
            f"If a repo is missing, REPORT it — do not improvise a clone.")


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

    Git workflow order: git_add → git_commit → git_push → create_github_pr

    Args:
        project: Project name or path
        message: Commit message
    """
    repo = _resolve_repo(project)
    branch = await _run_git("branch --show-current", repo)

    # PRE-CHECK: Something must be staged before committing
    # Use diff --cached to check if there's staged content (read-only check)
    staged_diff = await _run_git("diff --cached", repo)
    if not staged_diff or staged_diff == "(no output)":
        return "REFUSED: Nothing staged. Run git_add first."

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

    Git workflow order: git_add → git_commit → ship_branch (preferred)
    or git_push → create_github_pr for edge cases.

    Args:
        project: Project name or path
        branch: Branch to push (default: current branch)
    """
    repo = _resolve_repo(project)
    if not branch:
        branch = await _run_git("branch --show-current", repo)

    # PRE-CHECK: Must have unpushed commits before requesting push approval
    # Explicitly handle: (1) existing branch with upstream, (2) new branch without upstream.
    # _run_git reports failures as "Error (exit N): fatal: ..." — the old "fatal:" prefix
    # sniff missed that wrapper, so a MISSING upstream looked like an existing one and
    # every first push of a new branch was refused (exit-128 on rev-list). Only an actual
    # 40-hex commit sha counts as proof the upstream exists.
    upstream_sha = await _run_git(
        f"rev-parse --verify --quiet origin/{shlex.quote(branch)}", repo
    )
    if re.fullmatch(r"[0-9a-f]{40}", upstream_sha.strip()):
        # Upstream exists — check if there are unpushed commits
        ahead_count = await _run_git(f"rev-list --count origin/{branch}..{branch}", repo)
        try:
            if int(ahead_count.strip()) == 0:
                return "REFUSED: No unpushed commits. Stage and commit first."
        except (ValueError, TypeError):
            return f"REFUSED: Could not verify commit count. Git output: {ahead_count}"
    else:
        # No upstream — new branch. Verify it has commits.
        commit_count = await _run_git(f"rev-list --count {branch}", repo)
        try:
            if int(commit_count.strip()) == 0:
                return "REFUSED: Branch has no commits. Stage and commit first."
            # New branch with commits → allow push approval
        except (ValueError, TypeError):
            return f"REFUSED: Could not verify branch state. Git output: {commit_count}"

    # If we reach here: either upstream exists with unpushed commits, OR new branch with commits
    result = await _run_git(f"push origin {shlex.quote(branch)}", repo)
    
    # POST-CHECK: Verify the remote ref actually exists after push
    # This catches credential/auth failures that report success but don't push
    remote_ref_check = await _run_git(f"ls-remote --heads origin {shlex.quote(branch)}", repo)
    if not remote_ref_check or remote_ref_check == "(no output)":
        return f"FAILED: Push reported success but branch '{branch}' not found on origin.\n" \
               f"Likely cause: no push credentials configured.\n" \
               f"Git output: {result}"
    
    return result


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


def _github_token() -> str:
    """The GitHub PAT create_github_pr authenticates with — GH_TOKEN/GITHUB_TOKEN
    env, else the ~/.git-credentials token the clone pushes with. The ONE token
    source (Companion A): the PR-review diff route resolves the PAT through here
    too, so the card can never render 'PAT not configured' while pushes work."""
    import re
    tok = env("GH_TOKEN") or env("GITHUB_TOKEN") or ""
    if tok:
        return tok
    try:
        from pathlib import Path as _P
        cred = _P(env("HOME", "/root")) / ".git-credentials"
        if cred.exists():
            for line in cred.read_text().splitlines():
                m = re.search(r"https://(?:[^:@/]+:)?(?P<tok>[^@/]+)@github\.com", line)
                if m:
                    return m.group("tok")
    except Exception:
        pass
    return ""


def _github_repo_and_token(repo: str) -> tuple[str, str] | None:
    """(owner/name, token) for this clone's origin, or None.

    Token sources, in order: embedded in the remote URL, then the shared
    _github_token() chain (GH_TOKEN/GITHUB_TOKEN env, ~/.git-credentials). No
    gh CLI, no shell parsing of user text — this feeds the REST call below."""
    import re
    import subprocess
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None
    m = re.search(r"github\.com[:/](?P<own>[^/]+)/(?P<name>[^/\s]+?)(?:\.git)?$", url)
    if not m:
        return None
    slug = f"{m.group('own')}/{m.group('name')}"

    tok = ""
    m2 = re.search(r"https://(?:[^:@/]+:)?(?P<tok>[^@/]+)@github\.com", url)
    if m2 and not m2.group("tok").startswith("github.com"):
        tok = m2.group("tok")
    if not tok:
        tok = _github_token()
    return (slug, tok) if tok else None


@approve
@tool
async def create_github_pr(project: str, title: str, body: str = "",
                           base: str = "main") -> str:
    """Create a GitHub pull request. Requires approval.

    Talks to the GitHub REST API directly with the clone's own credentials —
    NO shell, NO gh CLI. The old gh-CLI shell-out failed on EVERY approved
    execution for days (unescaped title/body shell-split; and gh isn't in the
    container image) while the approval card showed green — the LOOP-1 ghost.

    Git workflow order: prefer ship_branch (push+PR, one approval).
    Use this alone only when the branch is already on origin.

    Args:
        project: Project name or path
        title: PR title
        body: PR description/body
        base: Base branch (default: main)
    """
    repo = _resolve_repo(project)
    branch = (await _run_git("branch --show-current", repo)).strip()
    if not branch or branch.startswith("Error"):
        return f"Error: could not determine current branch in {repo}: {branch}"
    if branch in ("main", "master"):
        return "REFUSED: you are on main — create the PR from your feature branch."

    # PRE-CHECK: Branch must exist on origin before creating PR
    # This prevents 422 head-invalid errors after operator approval
    remote_check = await _run_git(f"ls-remote --heads origin {shlex.quote(branch)}", repo)
    if not remote_check or remote_check == "(no output)":
        return "REFUSED: Branch not pushed to origin. Run git_push first."

    rt = _github_repo_and_token(repo)
    if not rt:
        return ("Error: no GitHub credentials found for this clone (remote URL, "
                "GH_TOKEN/GITHUB_TOKEN, or ~/.git-credentials). Ask the operator "
                "to wire the push PAT.")
    slug, token = rt

    import httpx
    import json
    # AUDIT-F2: the verify + compare GETs below MUST run inside this `async with`
    # block. Previously the context manager wrapped only the POST, so `client` was
    # already closed by the time verification ran — every real 201 fell into the
    # "FAILED: PR created but verification error" branch (closed-client RuntimeError),
    # reporting FAILED on a genuine success and inviting a duplicate-PR retry.
    _hdr = {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{slug}/pulls",
                headers=_hdr,
                json={"title": title, "body": body, "head": branch, "base": base},
            )

            if resp.status_code == 201:
                data = resp.json()
                pr_number = data.get('number')
                pr_url = data.get('html_url')

                # POST-CHECK: Verify PR was actually created and is reachable
                if not pr_number or not pr_url:
                    return (f"FAILED: GitHub API returned 201 but PR data incomplete.\n"
                            f"Response: {data}")

                # Verify PR exists by fetching it back (same live client)
                try:
                    verify_resp = await client.get(
                        f"https://api.github.com/repos/{slug}/pulls/{pr_number}",
                        headers=_hdr,
                    )
                    if verify_resp.status_code != 200:
                        return (f"FAILED: PR creation reported success but verification failed.\n"
                                f"PR URL: {pr_url}\n"
                                f"Verify response: {verify_resp.status_code}")
                except Exception as e:
                    return (f"FAILED: PR created but verification error: {e}\n"
                            f"PR URL: {pr_url}")

                # Get comparison stats for the PR card
                additions = 0
                deletions = 0
                try:
                    compare_resp = await client.get(
                        f"https://api.github.com/repos/{slug}/compare/{base}...{branch}",
                        headers=_hdr,
                    )
                    if compare_resp.status_code == 200:
                        compare_data = compare_resp.json()
                        additions = sum(f.get('additions', 0) for f in compare_data.get('files', []))
                        deletions = sum(f.get('deletions', 0) for f in compare_data.get('files', []))
                except Exception:
                    pass  # Stats are nice-to-have, don't fail the PR creation

                # Return structured JSON for PR review card
                return json.dumps({
                    "status": "created",
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "title": title,
                    "branch": branch,
                    "base": base,
                    "repo": slug,
                    "additions": additions,
                    "deletions": deletions,
                    "message": f"PR CREATED: #{pr_number} {pr_url}\n'{title}' ({branch} -> {base})."
                }, indent=2)

            if resp.status_code == 422 and "already exists" in resp.text:
                return f"A PR for {branch} -> {base} already exists: {resp.text[:200]}"
            return (f"Error: GitHub API returned {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        return f"Error: PR request failed to send: {e}"


@approve
@tool
async def ship_branch(project: str, title: str, body: str = "",
                      base: str = "main", branch: str = "") -> str:
    """Push the feature branch and open a GitHub PR in one approval.

    #SHIP1 preferred ship path: one operator Approve → branch on origin + PR
    review card (structured JSON with pr_url). Does not merge or deploy —
    operator merges on GitHub, then deploys founders/Clearfield as usual.

    Prefer this over separate git_push + create_github_pr during active builds
    so the operator is not the message bus between two gates.

    Args:
        project: Project name or path
        title: PR title
        body: PR description/body
        base: Base branch (default: main)
        branch: Branch to ship (default: current checked-out branch)
    """
    import json

    repo = _resolve_repo(project)
    current = (await _run_git("branch --show-current", repo)).strip()
    if not current or current.startswith("Error"):
        return f"Error: could not determine current branch in {repo}: {current}"
    if branch and branch.strip() and branch.strip() != current:
        return (
            f"REFUSED: ship_branch runs on the checked-out branch ({current}). "
            f"Checkout {branch.strip()} first, or omit branch."
        )
    if current in ("main", "master"):
        return "REFUSED: you are on main — ship from your feature branch."

    push_result = await git_push.coroutine(project, "")
    push_ok_already = isinstance(push_result, str) and "No unpushed commits" in push_result
    push_failed = (
        isinstance(push_result, str)
        and not push_ok_already
        and (
            push_result.startswith("REFUSED:")
            or push_result.startswith("FAILED:")
            or push_result.startswith("Error")
            or push_result.startswith("Error:")
        )
    )
    if push_failed:
        return push_result

    pr_result = await create_github_pr.coroutine(project, title, body, base)

    # Enrich successful JSON for the #D18 card + operator clarity
    if isinstance(pr_result, str) and pr_result.lstrip().startswith("{"):
        try:
            data = json.loads(pr_result)
            if data.get("status") == "created":
                data["pushed"] = not push_ok_already
                data["already_on_origin"] = bool(push_ok_already)
                data["message"] = (
                    f"SHIPPED: PR #{data.get('pr_number')} {data.get('pr_url')}\n"
                    f"'{data.get('title')}' ({data.get('branch')} -> {data.get('base')}). "
                    f"One approval — merge on GitHub, then deploy."
                )
                return json.dumps(data, indent=2)
        except json.JSONDecodeError:
            pass

    # Already-open PR: resolve URL so the card still gets a link (no hunt)
    if isinstance(pr_result, str) and "already exists" in pr_result.lower():
        rt = _github_repo_and_token(repo)
        if rt:
            slug, token = rt
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"https://api.github.com/repos/{slug}/pulls",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                        },
                        params={"state": "open", "head": f"{slug.split('/')[0]}:{current}", "base": base},
                    )
                    if resp.status_code == 200:
                        prs = resp.json() or []
                        if prs:
                            pr = prs[0]
                            return json.dumps({
                                "status": "created",
                                "pr_number": pr.get("number"),
                                "pr_url": pr.get("html_url"),
                                "title": pr.get("title") or title,
                                "branch": current,
                                "base": base,
                                "repo": slug,
                                "additions": 0,
                                "deletions": 0,
                                "already_existed": True,
                                "pushed": not push_ok_already,
                                "message": (
                                    f"SHIPPED: existing PR #{pr.get('number')} {pr.get('html_url')}\n"
                                    f"(branch already had an open PR; push step ok)."
                                ),
                            }, indent=2)
            except Exception:
                pass
        return pr_result

    return pr_result


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
    git_push, git_delete_branch, create_github_pr, ship_branch,
    # Testing
    run_tests, check_syntax,
    # Database
    db_query, db_execute,
]
TOOLS = ALL_DEV_TOOLS  # alias for cove-core channels.py loader
