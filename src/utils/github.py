"""
GitHub Content API — file operations for site management.

All operations use a Personal Access Token (PAT) stored per-Presence
in feature flags. Centralized here so site tools, approval execution,
and the diff endpoint all share the same API layer.

GitHub API docs: https://docs.github.com/en/rest/repos/contents
"""

import asyncio
import base64
import logging
from typing import Optional

import httpx

log = logging.getLogger("github")

API = "https://api.github.com"
TIMEOUT = 20

# Transient GitHub 5xx (and network blips) are common on large writes. A big
# deploy's `POST git/trees` 502'd with zero retry and killed the whole deploy
# (chordsoftruth, 864 files). Retry those on bounded exponential backoff.
_GH_RETRY_STATUS = {502, 503, 504}


def _headers(pat: str) -> dict:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh_send(client: httpx.AsyncClient, method: str, url: str, *,
                   headers: dict, retries: int = 4, **kwargs) -> httpx.Response:
    """Send a GitHub API request, retrying transient 5xx (502/503/504) and
    network/timeout errors with exponential backoff (1→2→4→8s cap).

    Does NOT call raise_for_status — the caller keeps its own status handling
    (e.g. the 422 branch-exists path). Retried writes are safe: blobs and trees
    are content-addressed so re-POSTing is idempotent; a retried commit at worst
    leaves an extra dangling commit that the branch ref then overwrites — far
    better than a dead deploy. A retryable status on the final attempt is
    returned as-is so the caller's raise_for_status surfaces the real error.
    """
    delay = 1.0
    for attempt in range(retries):
        try:
            resp = await client.request(method, url, headers=headers, **kwargs)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt == retries - 1:
                raise
            log.warning("GitHub %s %s network error (%s), retry %d/%d",
                        method, url, type(e).__name__, attempt + 1, retries)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
            continue
        if resp.status_code in _GH_RETRY_STATUS and attempt < retries - 1:
            log.warning("GitHub %s %s -> %s, retry %d/%d",
                        method, url, resp.status_code, attempt + 1, retries)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
            continue
        return resp
    return resp


# =============================================================================
# Read
# =============================================================================

async def github_get_file(repo: str, path: str, pat: str,
                          branch: str = "main") -> Optional[dict]:
    """Read a file from GitHub. Returns {content, sha, size, encoding} or None.

    Content is decoded from base64 to UTF-8 string.
    sha is needed for subsequent updates (GitHub requires it to prevent conflicts).
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{API}/repos/{repo}/contents/{path}",
            headers=_headers(pat),
            params={"ref": branch},
        )

    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    data = resp.json()
    content_b64 = data.get("content", "")
    try:
        content = base64.b64decode(content_b64).decode("utf-8")
    except Exception:
        content = content_b64

    return {
        "content": content,
        "sha": data["sha"],
        "size": data.get("size", 0),
        "encoding": data.get("encoding", "base64"),
        "path": data.get("path", path),
    }


async def github_list_files(repo: str, path: str, pat: str,
                            branch: str = "main") -> list[dict]:
    """List files in a repo directory. Returns [{name, path, type, sha, size}].

    type is 'file' or 'dir'.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{API}/repos/{repo}/contents/{path}",
            headers=_headers(pat),
            params={"ref": branch},
        )

    if resp.status_code == 404:
        return []
    resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, list):
        # Single file, not a directory
        return [{"name": data.get("name"), "path": data.get("path"),
                 "type": data.get("type"), "sha": data.get("sha"),
                 "size": data.get("size", 0)}]

    return [
        {
            "name": item["name"],
            "path": item["path"],
            "type": item["type"],
            "sha": item["sha"],
            "size": item.get("size", 0),
        }
        for item in data
    ]


# =============================================================================
# Branch management
# =============================================================================

async def github_create_branch(repo: str, branch_name: str, pat: str,
                               from_branch: str = "main") -> bool:
    """Create a new branch from an existing branch. Returns True on success."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Get the sha of the source branch
        resp = await client.get(
            f"{API}/repos/{repo}/git/refs/heads/{from_branch}",
            headers=_headers(pat),
        )
        resp.raise_for_status()
        sha = resp.json()["object"]["sha"]

        # Create the new ref
        resp = await client.post(
            f"{API}/repos/{repo}/git/refs",
            headers=_headers(pat),
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )

    if resp.status_code == 201:
        log.info(f"Created branch {branch_name} on {repo} from {from_branch}")
        return True
    elif resp.status_code == 422:
        # Branch already exists
        log.warning(f"Branch {branch_name} already exists on {repo}")
        return True
    else:
        resp.raise_for_status()
        return False


async def github_delete_branch(repo: str, branch: str, pat: str) -> bool:
    """Delete a branch. Returns True on success or if already gone."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.delete(
            f"{API}/repos/{repo}/git/refs/heads/{branch}",
            headers=_headers(pat),
        )

    if resp.status_code in (204, 422):
        log.info(f"Deleted branch {branch} on {repo}")
        return True
    elif resp.status_code == 404:
        return True  # Already gone
    else:
        resp.raise_for_status()
        return False


# =============================================================================
# Write
# =============================================================================

async def github_update_file(repo: str, path: str, content: str,
                             message: str, branch: str, pat: str,
                             sha: Optional[str] = None) -> dict:
    """Create or update a file on a branch. Returns commit info.

    If sha is provided, this is an update (GitHub requires sha to prevent conflicts).
    If sha is None, this creates a new file.
    """
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

    body = {
        "message": message,
        "content": content_b64,
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.put(
            f"{API}/repos/{repo}/contents/{path}",
            headers=_headers(pat),
            json=body,
        )

    resp.raise_for_status()
    data = resp.json()

    log.info(f"{'Updated' if sha else 'Created'} {path} on {repo}/{branch}")
    return {
        "sha": data["content"]["sha"],
        "commit_sha": data["commit"]["sha"],
        "commit_message": message,
    }


# =============================================================================
# Diff + Merge
# =============================================================================

async def github_get_diff(repo: str, base: str, head: str, pat: str) -> str:
    """Get unified diff between two branches. Returns diff text."""
    headers = _headers(pat)
    headers["Accept"] = "application/vnd.github.diff"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API}/repos/{repo}/compare/{base}...{head}",
            headers=headers,
        )

    if resp.status_code == 404:
        return "Branch not found or no difference."
    resp.raise_for_status()
    return resp.text


async def github_get_compare(repo: str, base: str, head: str, pat: str) -> dict:
    """Get comparison info (JSON) between two branches.

    Returns {status, ahead_by, behind_by, files: [{filename, status, additions, deletions, patch}]}.
    Useful for structured diff display.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API}/repos/{repo}/compare/{base}...{head}",
            headers=_headers(pat),
        )

    if resp.status_code == 404:
        return {"status": "not_found", "files": []}
    resp.raise_for_status()

    data = resp.json()
    return {
        "status": data.get("status", ""),
        "ahead_by": data.get("ahead_by", 0),
        "behind_by": data.get("behind_by", 0),
        "total_commits": data.get("total_commits", 0),
        "files": [
            {
                "filename": f["filename"],
                "status": f["status"],  # added, modified, removed
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", ""),
            }
            for f in data.get("files", [])
        ],
    }


# =============================================================================
# Bulk commit (whole-folder mirror) — Git Data API
# =============================================================================

def _git_blob_sha(raw) -> str:
    """Compute the git blob SHA-1 for content (matches GitHub's blob sha)."""
    import hashlib
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    h = hashlib.sha1()
    h.update(b"blob " + str(len(raw)).encode() + b"\x00" + raw)
    return h.hexdigest()


async def github_get_tree_shas(repo: str, branch: str, pat: str) -> dict:
    """Return {path: blob_sha} for every file on `branch` (one API call).

    Lets a caller diff the live site without downloading any content.
    Returns {} if the branch/ref doesn't exist yet (brand-new repo).
    """
    async with httpx.AsyncClient(timeout=60) as client:
        h = _headers(pat)
        r = await _gh_send(client, "GET", f"{API}/repos/{repo}/git/refs/heads/{branch}", headers=h)
        if r.status_code != 200:
            return {}
        parent_sha = r.json()["object"]["sha"]
        rc = await _gh_send(client, "GET", f"{API}/repos/{repo}/git/commits/{parent_sha}", headers=h)
        rc.raise_for_status()
        base_tree_sha = rc.json()["tree"]["sha"]
        rt = await _gh_send(
            client, "GET", f"{API}/repos/{repo}/git/trees/{base_tree_sha}",
            headers=h, params={"recursive": "1"},
        )
        rt.raise_for_status()
        return {e["path"]: e["sha"] for e in rt.json().get("tree", []) if e["type"] == "blob"}


async def github_commit_tree(repo: str, files: dict, message: str,
                             branch: str, pat: str,
                             parent_branch: str = "main",
                             unchanged_shas: dict | None = None) -> dict:
    """Commit the folder as one commit, uploading ONLY changed/new files.

    Diff-aware: compares each file's git blob sha to what's already on
    `parent_branch` and reuses the existing blob for unchanged files (no
    upload). `unchanged_shas` lets the caller pass {path: blob_sha} for files
    it already knows are identical to live (matched by NC etag) so their bytes
    are never downloaded or uploaded. The new tree lists the full file set
    (files + unchanged_shas); anything missing from both is removed (deletes
    propagate — folder is source of truth). History is preserved.

    Returns {commit_sha, branch, file_count, changed, shas, no_changes}.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        h = _headers(pat)

        # 1. Parent commit + its tree sha
        r = await _gh_send(client, "GET", f"{API}/repos/{repo}/git/refs/heads/{parent_branch}", headers=h)
        r.raise_for_status()
        parent_sha = r.json()["object"]["sha"]
        rc = await _gh_send(client, "GET", f"{API}/repos/{repo}/git/commits/{parent_sha}", headers=h)
        rc.raise_for_status()
        base_tree_sha = rc.json()["tree"]["sha"]

        # 2. Existing repo files {path: blob_sha}
        rt = await _gh_send(
            client, "GET", f"{API}/repos/{repo}/git/trees/{base_tree_sha}",
            headers=h, params={"recursive": "1"},
        )
        rt.raise_for_status()
        existing = {e["path"]: e["sha"] for e in rt.json().get("tree", []) if e["type"] == "blob"}

        # 3. Build tree. unchanged_shas are files the caller already matched to
        #    live by NC etag — added by sha with no download/upload. files are
        #    the (few) changed/new files; upload a blob only when content differs.
        tree_entries = []
        all_shas = {}
        changed = 0
        for path, sha in (unchanged_shas or {}).items():
            if path in files:
                continue  # caller flagged it changed; the files loop is authoritative
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
            all_shas[path] = sha
        for path, raw in files.items():
            want = _git_blob_sha(raw)
            if existing.get(path) == want:
                sha = want  # unchanged — reuse existing blob, no upload
            else:
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                b64 = base64.b64encode(raw).decode("ascii")
                br = await _gh_send(
                    client, "POST", f"{API}/repos/{repo}/git/blobs",
                    headers=h, json={"content": b64, "encoding": "base64"},
                )
                br.raise_for_status()
                sha = br.json()["sha"]
                changed += 1
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
            all_shas[path] = sha
        # deletions: files live now but absent from the new tree
        changed += len(set(existing) - set(all_shas))

        # 4. Full tree (no base_tree) → exact mirror; unchanged blobs reused by sha
        tr = await _gh_send(
            client, "POST", f"{API}/repos/{repo}/git/trees",
            headers=h, json={"tree": tree_entries},
        )
        tr.raise_for_status()
        tree_sha = tr.json()["sha"]

        # Nothing changed — don't make an empty commit
        if tree_sha == base_tree_sha:
            log.info(f"Deploy {repo}: no changes")
            return {"commit_sha": parent_sha, "branch": parent_branch,
                    "file_count": len(all_shas), "changed": 0,
                    "shas": all_shas, "no_changes": True}

        # 5. Commit + point the deploy branch at it
        cr = await _gh_send(
            client, "POST", f"{API}/repos/{repo}/git/commits",
            headers=h, json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        cr.raise_for_status()
        commit_sha = cr.json()["sha"]

        cre = await _gh_send(
            client, "POST", f"{API}/repos/{repo}/git/refs",
            headers=h, json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
        if cre.status_code == 422:
            upd = await _gh_send(
                client, "PATCH", f"{API}/repos/{repo}/git/refs/heads/{branch}",
                headers=h, json={"sha": commit_sha, "force": True},
            )
            upd.raise_for_status()
        elif cre.status_code != 201:
            cre.raise_for_status()

    log.info(f"Committed {repo}/{branch}: {changed} changed of {len(all_shas)} files ({commit_sha[:7]})")
    return {"commit_sha": commit_sha, "branch": branch,
            "file_count": len(all_shas), "changed": changed,
            "shas": all_shas, "no_changes": False}


async def github_merge_branch(repo: str, base: str, head: str,
                              message: str, pat: str) -> dict:
    """Merge head branch into base. Returns merge commit info.

    Raises on conflict (409) or failure.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{API}/repos/{repo}/merges",
            headers=_headers(pat),
            json={
                "base": base,
                "head": head,
                "commit_message": message,
            },
        )

    if resp.status_code == 201:
        data = resp.json()
        log.info(f"Merged {head} → {base} on {repo}")
        return {
            "sha": data["sha"],
            "message": data["commit"]["message"],
            "merged": True,
        }
    elif resp.status_code == 204:
        # Already merged / nothing to merge
        return {"merged": True, "message": "Already up to date"}
    elif resp.status_code == 409:
        return {"merged": False, "error": "Merge conflict — resolve manually or recreate the edit"}
    else:
        resp.raise_for_status()
        return {"merged": False, "error": f"HTTP {resp.status_code}"}
