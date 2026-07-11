"""Read-only repo tools for the MERCHANT (release) role — #D17.

Mercer's lane is releases/distribution (NOTICE / LICENSE / versioning), which needs to
SEE the repos, but he is report-only: the steward ships. So the merchant channel binds
this safe subset — the @auto (no-approval) git READ tools from dev_tools — and NOT the
full dev set (no git_push / create_github_pr / commit / db_execute). Capability still
respects the approval decorators; this module simply never includes a gated tool.

Brain geography (the thing Mercer didn't know): the code repos live at paths resolved
by _resolve_repo (checking /sites, /data/projects/, etc.); Nextcloud is the BRAIN
(docs/memory), not the code. A release-role agent asking Nextcloud for a repo path is
looking in the wrong place — these tools are the repo surface.

Bound two ways (same pattern as the steward's universal queue/delegation modules):
listed in cove.yaml.example's merchant_channel.tools for fresh installs, AND appended
universally for merchant channels in channels._channel_tool_modules so a Cove whose
cove.yaml predates this still gets it on upgrade.
"""
from pathlib import Path
from langchain_core.tools import tool

from src.tools.dev_tools import (
    git_status, git_diff, git_log, git_current_branch, git_diff_branch, _resolve_repo,
)
from src.tools.approval import auto


# =============================================================================
# Read-only file access for release-role audits (NOTICE/LICENSE headers) — #D40
# =============================================================================

@auto
@tool
async def read_file(path: str, max_chars: int = 50000) -> str:
    """Read a text file from within a repository. Read-only, @auto tier.

    Path resolution:
      - Project names resolve via _resolve_repo (checks /sites, /data/projects/, etc.)
      - Relative paths resolve within the repo root
      - Rejects: .. path traversal, absolute paths escaping repo roots

    Args:
        path: File path (e.g. 'lucid-cove/src/tools/dev_tools.py' or absolute repo path)
        max_chars: Maximum characters to return (default 50KB). Larger files truncated
                   with a marker indicating total size.

    Returns:
        File contents, or error message if path invalid/inaccessible.
    """
    # Security: reject explicit path traversal attempts
    if ".." in path:
        return "Error: Path traversal (..) not allowed."

    try:
        # Resolve the repo root using the shared resolver
        # For paths like "lucid-cove/src/tools/dev_tools.py", extract project name
        repo_path = Path(path)
        if not repo_path.is_absolute():
            # Extract first component as project name
            parts = repo_path.parts
            if not parts:
                return "Error: Empty path."
            project_name = parts[0]
            repo_root = Path(_resolve_repo(project_name))
            # Remaining path within repo
            if len(parts) > 1:
                file_path = repo_root.joinpath(*parts[1:])
            else:
                file_path = repo_root
        else:
            # Absolute path - validate it resolves within a known repo
            repo_root = Path(_resolve_repo(str(repo_path)))
            file_path = repo_path

        # Security: ensure resolved path stays within repo root
        try:
            file_path.resolve().relative_to(repo_root.resolve())
        except ValueError:
            return "Error: Path escapes repository root."

        # Check file exists and is a file
        if not file_path.exists():
            return f"Error: File not found: {path}"
        if not file_path.is_file():
            return f"Error: Path is not a file: {path}"

        # Read with size cap
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading file: {e}"

        total_len = len(content)
        if total_len > max_chars:
            truncated = content[:max_chars]
            return truncated + f"\n\n[...truncated, total size: {total_len} chars]"

        return content

    except Exception as e:
        return f"Error: {e}"


# Exactly the @auto git READ tools plus scoped file read — no write/dangerous dev tools.
ALL_DEV_READ_TOOLS = [
    git_status, git_diff, git_log, git_current_branch, git_diff_branch, read_file,
]
TOOLS = ALL_DEV_READ_TOOLS  # cove-core channels.py loader entry point (_load_tools)
