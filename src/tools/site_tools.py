"""
Site Tools — agent-callable tools for reading and editing Cove websites.

These tools let agents (Atlas, Stuart, Archimedes) interact with sites
managed through the Site Builder for the ACTING scope only (#TIER1).
Read operations are AUTO tier (silent). Edit/create operations are APPROVE
tier — they create a branch with the change and request operator sign-off
before merging to main. Domain resolution must not discover another
presence's sites.

The approval flow is:
  1. Agent calls site_edit_file → tool creates branch, commits change
  2. ApprovalRequired raised → approval card appears in Attention
  3. Operator reviews diff, approves → merge-on-approve merges to main
  4. Cloudflare Pages auto-deploys from main

Registration: Add 'tools.site_tools' to agent.yaml tools.modules.
"""

import logging
import uuid
from datetime import datetime, timezone

from langchain_core.tools import tool

from src.tools.approval import auto, notify

log = logging.getLogger("site_tools")


def _acting_nc_creds() -> tuple[str, str, str]:
    """(url, user, password) for the chat-bound presence NC.

    Chat/flow bind set_request_nc_creds at the same chokepoint as nextcloud_tools.
    site_deploy used to call get_nc_creds() with no Request → always empty in multi
    mode, so Atlas saw "no Nextcloud account configured" even with a healthy NC user.
    """
    from src.tools.nextcloud_tools import _current_creds

    return _current_creds()


def _get_site_config(domain: str) -> dict:
    """Load site.yaml for a domain from the ACTING agent's sites root only.

    #TIER1: do not walk host-wide paths that could resolve another presence's
    site. Order:
      1. {get_sites_path()}/ under /vault and /app/data (relative to config)
      2. legacy /vault/AgentSkills/Sites and /app/data/sites only when they
         match get_sites_path()
      3. WebDAV on the acting presence NC (Tier B personal sites live here —
         vault is usually only the steward mount, so Atlas must hit NC)
    """
    import os
    import yaml
    from src.config import get_sites_path

    domain = (domain or "").strip().lower()
    if not domain or "/" in domain or "\\" in domain or ".." in domain:
        raise ValueError("Invalid domain")

    sites_rel = (get_sites_path() or "AgentSkills/Sites").strip().strip("/")
    candidates = []
    for root in ("/vault", "/app/data"):
        candidates.append(os.path.join(root, sites_rel, domain, "site.yaml"))
    # Only allow legacy flat mounts when they are the configured path
    if sites_rel in ("AgentSkills/Sites", "Sites"):
        for base in ("/vault/AgentSkills/Sites", "/app/data/sites"):
            candidates.append(os.path.join(base, domain, "site.yaml"))

    seen = set()
    for config_path in candidates:
        if config_path in seen:
            continue
        seen.add(config_path)
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            if not isinstance(cfg, dict):
                raise ValueError(f"Invalid site.yaml for {domain}")
            return cfg

    # Presence Tier B: site.yaml is on that user's NC, not the steward vault.
    cfg = _load_site_config_from_nc(domain, sites_rel)
    if cfg is not None:
        return cfg

    raise ValueError(f"Site config not found for {domain}. Is the site set up in Site Builder?")


def _load_site_config_from_nc(domain: str, sites_rel: str) -> dict | None:
    """Fetch AgentSkills/Sites/{domain}/site.yaml via acting NC WebDAV."""
    import yaml
    from urllib.parse import quote

    import httpx

    nc_url, nc_user, nc_pass = _acting_nc_creds()
    if not nc_url or not nc_user or not nc_pass:
        return None
    config_url = (
        f"{nc_url.rstrip('/')}/remote.php/dav/files/{quote(nc_user, safe='')}/"
        f"{sites_rel.strip('/')}/{quote(domain, safe='')}/site.yaml"
    )
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(config_url, auth=(nc_user, nc_pass))
        if resp.status_code != 200 or not resp.content:
            return None
        cfg = yaml.safe_load(resp.text) or {}
        if not isinstance(cfg, dict):
            raise ValueError(f"Invalid site.yaml for {domain}")
        return cfg
    except ValueError:
        raise
    except Exception as e:
        log.debug("NC site.yaml fetch failed for %s: %s", domain, e)
        return None


def _get_pat() -> str:
    """Get GitHub PAT from feature flags."""
    try:
        from src.config import get_feature_flags
        pat = get_feature_flags().get("github_pat", "")
        if not pat:
            raise ValueError("GitHub PAT not configured. Set it in Settings → Tools → GitHub.")
        return pat
    except ImportError:
        raise ValueError("Config module not available")


def _get_repo_and_pat(domain: str) -> tuple[str, str, str]:
    """Get (repo, default_branch, pat) for a domain. Raises on missing config."""
    config = _get_site_config(domain)
    github = config.get("github", {})
    repo = github.get("repo", "")
    branch = github.get("branch", "main")

    if not repo:
        raise ValueError(f"No GitHub repo connected for {domain}. Complete the GitHub step in Site Builder.")

    pat = _get_pat()
    return repo, branch, pat


# =============================================================================
# Read tools (AUTO tier — no approval needed)
# =============================================================================

@auto
@tool
async def site_list_files(domain: str, path: str = "") -> str:
    """List files in a site's GitHub repository.

    Args:
        domain: The site domain (e.g. example.com)
        path: Optional subdirectory path (e.g. 'assets/images'). Empty = repo root.
    """
    try:
        repo, branch, pat = _get_repo_and_pat(domain)
        from src.utils.github import github_list_files

        files = await github_list_files(repo, path, pat, branch)
        if not files:
            return f"No files found in {domain}/{path or 'root'}"

        lines = [f"Files in {domain}/{path or 'root'} ({len(files)} items):"]
        for f in files:
            icon = "📁" if f["type"] == "dir" else "📄"
            size = f" ({f['size']} bytes)" if f["type"] == "file" and f["size"] else ""
            lines.append(f"  {icon} {f['name']}{size}")
        return "\n".join(lines)

    except Exception as e:
        return f"Error listing files for {domain}: {e}"


@auto
@tool
async def site_read_file(domain: str, file_path: str) -> str:
    """Read a file from a site's GitHub repository.

    Args:
        domain: The site domain (e.g. example.com)
        file_path: Path to the file (e.g. index.html, about/index.html)
    """
    try:
        repo, branch, pat = _get_repo_and_pat(domain)
        from src.utils.github import github_get_file

        result = await github_get_file(repo, file_path, pat, branch)
        if not result:
            return f"File not found: {domain}/{file_path}"

        content = result["content"]
        size = result["size"]
        return f"--- {file_path} ({size} bytes) ---\n{content}"

    except Exception as e:
        return f"Error reading {file_path} from {domain}: {e}"


# =============================================================================
# Patch tool (lightweight edit — find/replace, then approval)
# =============================================================================

@notify
@tool
async def site_patch_file(domain: str, file_path: str,
                          find_text: str, replace_text: str,
                          edit_description: str) -> str:
    """Make a targeted edit to a site file using find/replace.

    This is the primary tool for quick fixes: remove a stray character,
    update a line of text, change a link. Much faster than site_edit_file
    because the model only outputs the specific text that changes.

    The find_text must match exactly (including whitespace). If it matches
    multiple times, all occurrences are replaced.

    Creates a branch with the change and requests operator approval.

    Args:
        domain: The site domain (e.g. example.com)
        file_path: Path to the file (e.g. intro/index.html)
        find_text: The exact text to find in the file
        replace_text: The text to replace it with (empty string to delete)
        edit_description: Short description of the change
    """
    try:
        repo, main_branch, pat = _get_repo_and_pat(domain)
        from src.utils.github import (
            github_get_file, github_create_branch, github_update_file,
        )
        from src.config import get_primary_agent_id

        # Read current file
        current = await github_get_file(repo, file_path, pat, main_branch)
        if not current:
            return f"File {file_path} not found on {domain}."

        content = current["content"]
        file_sha = current["sha"]

        # Verify find_text exists in the file
        if find_text not in content:
            return (
                f"Could not find the specified text in {file_path}. "
                f"Make sure the find_text matches exactly (including whitespace). "
                f"Use site_read_file to check the current content."
            )

        # Apply the patch
        new_content = content.replace(find_text, replace_text)
        occurrences = content.count(find_text)

        # Create branch and commit
        agent_id = get_primary_agent_id()
        short_id = uuid.uuid4().hex[:6]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        branch_name = f"{agent_id}/site-patch-{date_str}-{short_id}"

        await github_create_branch(repo, branch_name, pat, main_branch)
        await github_update_file(
            repo, file_path, new_content,
            message=f"Patch: {edit_description}",
            branch=branch_name, pat=pat, sha=file_sha,
        )

        log.info(f"Site patch committed: {domain}/{file_path} on {branch_name} ({occurrences} occurrence(s))")

        # Create approval request
        from src.tools.approval import _save_approval_to_db, ApprovalRequest, Tier

        approval_args = {
            "domain": domain,
            "file_path": file_path,
            "repo": repo,
            "branch": branch_name,
            "edit_description": edit_description,
        }

        req = ApprovalRequest(
            tool_name="site_patch_file",
            description=f"Site patch on {domain}: {edit_description}",
            args=approval_args,
            tier=Tier.APPROVE,
        )
        await _save_approval_to_db(req)

        try:
            import asyncio
            from src.tools.calendar_notify import push_approval_to_calendar
            asyncio.ensure_future(push_approval_to_calendar(
                request_id=req.request_id,
                tool_name="site_patch_file",
                description=f"Site patch: {domain}/{file_path} — {edit_description}",
            ))
        except Exception:
            pass

        return (
            f"Patch applied to `{file_path}` ({occurrences} occurrence(s) replaced).\n"
            f"Branch: `{branch_name}`\n"
            f"Description: {edit_description}\n"
            f"Approval request created (ID: {req.request_id}). "
            f"Check Attention → Pending Approvals to review the diff and approve."
        )

    except Exception as e:
        return f"Error patching {file_path} on {domain}: {e}"


# =============================================================================
# Full write tools (for Archimedes-level builds — full file replacement)
# =============================================================================

@notify
@tool
async def site_edit_file(domain: str, file_path: str, new_content: str,
                         edit_description: str) -> str:
    """Edit a file on a site. Creates a branch with the change and requests
    operator approval before merging to main (which triggers deploy).

    The tool executes (creates branch + commits), then raises an approval
    request. The operator sees the diff in Attention and approves to merge.

    Args:
        domain: The site domain (e.g. example.com)
        file_path: Path to the file to edit (e.g. index.html)
        new_content: The complete new file content
        edit_description: Short description of what changed (shown in approval card)
    """
    try:
        repo, main_branch, pat = _get_repo_and_pat(domain)
        from src.utils.github import (
            github_get_file, github_create_branch, github_update_file,
        )
        from src.config import get_primary_agent_id

        agent_id = get_primary_agent_id()
        short_id = uuid.uuid4().hex[:6]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        branch_name = f"{agent_id}/site-edit-{date_str}-{short_id}"

        # Get current file sha (needed for update)
        current = await github_get_file(repo, file_path, pat, main_branch)
        if not current:
            return f"File {file_path} not found on {main_branch}. Use site_create_file for new files."

        file_sha = current["sha"]

        # Create branch
        await github_create_branch(repo, branch_name, pat, main_branch)

        # Commit the edit to the branch
        await github_update_file(
            repo, file_path, new_content,
            message=f"Site edit: {edit_description}",
            branch=branch_name, pat=pat, sha=file_sha,
        )

        log.info(f"Site edit committed: {domain}/{file_path} on {branch_name}")

        # Create approval request manually (tool is @notify so tool_node won't block)
        from src.tools.approval import _save_approval_to_db, ApprovalRequest, Tier, ApprovalRequired
        import json

        approval_args = {
            "domain": domain,
            "file_path": file_path,
            "repo": repo,
            "branch": branch_name,
            "edit_description": edit_description,
        }

        req = ApprovalRequest(
            tool_name="site_edit_file",
            description=f"Site edit on {domain}: {edit_description}",
            args=approval_args,
            tier=Tier.APPROVE,
        )
        await _save_approval_to_db(req)

        # Push calendar notification for phone alert
        try:
            import asyncio
            from src.tools.calendar_notify import push_approval_to_calendar
            asyncio.ensure_future(push_approval_to_calendar(
                request_id=req.request_id,
                tool_name="site_edit_file",
                description=f"Site edit: {domain}/{file_path} — {edit_description}",
            ))
        except Exception:
            pass

        return (
            f"Edit committed to branch `{branch_name}` on {repo}.\n"
            f"File: {file_path}\n"
            f"Description: {edit_description}\n"
            f"Approval request created (ID: {req.request_id}). "
            f"Check Attention → Pending Approvals to review the diff and approve. "
            f"On approve, the branch merges to {main_branch} and Cloudflare deploys."
        )

    except Exception as e:
        return f"Error editing {file_path} on {domain}: {e}"


@notify
@tool
async def site_create_file(domain: str, file_path: str, content: str,
                           description: str) -> str:
    """Create a new file on a site. Creates a branch with the new file and
    requests operator approval before merging.

    Args:
        domain: The site domain (e.g. example.com)
        file_path: Path for the new file (e.g. blog/new-post.html)
        content: The file content
        description: Short description of the new file (shown in approval card)
    """
    try:
        repo, main_branch, pat = _get_repo_and_pat(domain)
        from src.utils.github import (
            github_get_file, github_create_branch, github_update_file,
        )
        from src.config import get_primary_agent_id

        agent_id = get_primary_agent_id()
        short_id = uuid.uuid4().hex[:6]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        branch_name = f"{agent_id}/site-create-{date_str}-{short_id}"

        # Verify file doesn't already exist
        existing = await github_get_file(repo, file_path, pat, main_branch)
        if existing:
            return f"File {file_path} already exists. Use site_edit_file to modify it."

        # Create branch
        await github_create_branch(repo, branch_name, pat, main_branch)

        # Commit the new file to the branch (no sha = new file)
        await github_update_file(
            repo, file_path, content,
            message=f"New file: {description}",
            branch=branch_name, pat=pat, sha=None,
        )

        log.info(f"Site create committed: {domain}/{file_path} on {branch_name}")

        # Create approval request
        from src.tools.approval import _save_approval_to_db, ApprovalRequest, Tier

        approval_args = {
            "domain": domain,
            "file_path": file_path,
            "repo": repo,
            "branch": branch_name,
            "description": description,
        }

        req = ApprovalRequest(
            tool_name="site_create_file",
            description=f"New file on {domain}: {description}",
            args=approval_args,
            tier=Tier.APPROVE,
        )
        await _save_approval_to_db(req)

        try:
            import asyncio
            from src.tools.calendar_notify import push_approval_to_calendar
            asyncio.ensure_future(push_approval_to_calendar(
                request_id=req.request_id,
                tool_name="site_create_file",
                description=f"New file: {domain}/{file_path} — {description}",
            ))
        except Exception:
            pass

        return (
            f"New file committed to branch `{branch_name}` on {repo}.\n"
            f"File: {file_path}\n"
            f"Description: {description}\n"
            f"Approval request created (ID: {req.request_id}). "
            f"Check Attention → Pending Approvals to review and approve."
        )

    except Exception as e:
        return f"Error creating {file_path} on {domain}: {e}"


# =============================================================================
# Deploy tool (full-folder publish — APPROVE tier)
# =============================================================================

@notify
@tool
async def site_deploy(domain: str, description: str = "Full site deploy") -> str:
    """Publish a site by mirroring its entire working folder live.

    Reads EVERY file in the site's Nextcloud folder (AgentSkills/Sites/{domain}/),
    commits the whole folder to the site's GitHub repo as one commit, and raises
    an operator approval. On approve, it merges to main and Cloudflare deploys.

    Use this after editing site files to publish all changes at once — it is the
    bridge from "edited the folder" to "live on the web". The folder is the source
    of truth: files removed from the folder are removed from the live site (every
    prior state stays recoverable in git history).

    Note: reads the folder using this agent's own Nextcloud account, so it works
    for sites owned by the running Presence (e.g. Atlas deploying the operator's
    sites). For another Presence's site, deploy from that Presence or use the
    Deploy button in Site Builder.

    Args:
        domain: The site domain (e.g. example.com)
        description: Short summary of what changed (shown on the approval card)
    """
    try:
        import asyncio
        import logging
        from src.dashboard.routes.sites import _deploy_site_core
        from src.config import get_primary_agent_id

        # Must use request-scoped NC (set in chat), not get_nc_creds() without Request.
        nc_url, nc_user, nc_pass = _acting_nc_creds()
        if not nc_user or not nc_pass:
            return (
                "Cannot deploy: no Nextcloud account is bound for this chat turn. "
                "On multi-presence Coves the personal agent needs the operator's NC "
                "user (Files working is the smoke test). Or use Site Builder → Deploy."
            )

        agent_id = get_primary_agent_id()
        _log = logging.getLogger("site_tools")

        # Run the deploy in the background so the chat never blocks. The read +
        # diff + commit + approval all happen off the tool call; the approval
        # card shows up in Attention a moment later.
        def _notify_deploy_failure(err: str):
            # C3-11: the tool just PROMISED "the approval card will appear in
            # Attention in a moment" — a background failure must land there too,
            # or the operator waits forever on a promise with no failure channel.
            # Same pattern as the scheduler's LTP failure notification.
            try:
                from src.tools.approval import _notification_queue
                from src.utils.time_utils import now_utc
                _notification_queue.append({
                    "tier": "error",
                    "tool": "site_deploy",
                    "args": {"domain": domain, "error": (err or "")[:200]},
                    "timestamp": now_utc().isoformat(),
                    "message": f"Site deploy for {domain} FAILED: {(err or 'unknown error')[:200]}",
                })
            except Exception:
                pass  # never let notification failure mask the original error

        async def _run_deploy():
            try:
                r = await _deploy_site_core(nc_url, nc_user, nc_pass, domain, description, agent_id)
                if not r.get("ok"):
                    _log.error("Deploy %s failed: %s", domain, r.get("error"))
                    _notify_deploy_failure(str(r.get("error") or ""))
                elif r.get("no_changes"):
                    _log.info("Deploy %s: no changes vs live", domain)
                else:
                    _log.info("Deploy %s staged: %s changed → approval %s",
                              domain, r.get("changed"), r.get("request_id"))
            except Exception as e:
                _log.error("Deploy %s background error: %s", domain, e)
                _notify_deploy_failure(str(e))

        asyncio.create_task(_run_deploy())

        return (
            f"Deploy of {domain} is running in the background. The approval card will appear in "
            f"Attention → Pending Approvals in a moment — approve it whenever you're ready "
            f"and Cloudflare will publish. No need to wait; we can keep working."
        )
    except Exception as e:
        return f"Error starting deploy for {domain}: {e}"


# =============================================================================
# Tool registration
# =============================================================================

def get_tools() -> list:
    """Return all site tools for agent tool loading."""
    return [site_list_files, site_read_file, site_patch_file,
            site_edit_file, site_create_file, site_deploy]
