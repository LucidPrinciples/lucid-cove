"""Read-only repo tools for the MERCHANT (release) role — #D17.

Mercer's lane is releases/distribution (NOTICE / LICENSE / versioning), which needs to
SEE the repos, but he is report-only: the steward ships. So the merchant channel binds
this safe subset — the @auto (no-approval) git READ tools from dev_tools — and NOT the
full dev set (no git_push / create_github_pr / commit / db_execute). Capability still
respects the approval decorators; this module simply never includes a gated tool.

Brain geography (the thing Mercer didn't know): the code repos live on the box at
/sites and are reached with these git_* tools; Nextcloud is the BRAIN (docs/memory),
not the code. A release-role agent asking Nextcloud for a repo path is looking in the
wrong place — these tools are the repo surface.

Bound two ways (same pattern as the steward's universal queue/delegation modules):
listed in cove.yaml.example's merchant_channel.tools for fresh installs, AND appended
universally for merchant channels in channels._channel_tool_modules so a Cove whose
cove.yaml predates this still gets it on upgrade.
"""
from src.tools.dev_tools import (
    git_status, git_diff, git_log, git_current_branch, git_diff_branch,
)

# Exactly the @auto git READ tools — no write/dangerous dev tools here by construction.
ALL_DEV_READ_TOOLS = [
    git_status, git_diff, git_log, git_current_branch, git_diff_branch,
]
TOOLS = ALL_DEV_READ_TOOLS  # cove-core channels.py loader entry point (_load_tools)
